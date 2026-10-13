"""Per-account login protection.

The per-IP+identifier throttle in ``apps.common.throttles`` stops one source from hammering one account, but an
attacker rotating IPs gets a fresh budget every time. This counter is keyed by the normalised identifier only, so
the budget belongs to the account no matter where the guesses come from.

Design notes:

* State lives in the Django cache (Redis in production, the same store the throttles use).
* The key is a hash of the normalised identifier and is written for unknown identifiers exactly as for real
  ones, so a lock (HTTP 429) never reveals whether an account exists.
* A locked account rejects every attempt, even with the right password, otherwise guessing could continue.
* A successful login, a completed password reset or a staff unlock clears the state.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from rest_framework.exceptions import Throttled

from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService

_FAIL_PREFIX = "login:fail:"
_LOCK_PREFIX = "login:lock:"


def _normalise(identifier: object) -> str:
    return str(identifier or "").strip().lower()[:254]


def _digest(identifier: object) -> str:
    return hashlib.sha256(_normalise(identifier).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FailureOutcome:
    attempts: int
    locked_now: bool


class LoginLockoutService:
    @staticmethod
    def _threshold() -> int:
        return int(settings.LOGIN_LOCKOUT_THRESHOLD)

    @staticmethod
    def _window_seconds() -> int:
        return max(1, int(settings.LOGIN_LOCKOUT_WINDOW_MINUTES) * 60)

    @staticmethod
    def _lock_seconds() -> int:
        return max(1, int(settings.LOGIN_LOCKOUT_MINUTES) * 60)

    @classmethod
    def retry_after(cls, identifier: object) -> int:
        """Seconds until the identifier may try again; 0 when it is not locked."""
        locked_until = cache.get(_LOCK_PREFIX + _digest(identifier))
        if not locked_until:
            return 0
        return max(0, math.ceil(float(locked_until) - time.time()))

    @classmethod
    def ensure_not_locked(cls, identifier: object) -> None:
        wait = cls.retry_after(identifier)
        if wait > 0:
            # Throttled renders as 429 + Retry-After through the project exception handler.
            raise Throttled(wait=wait, detail="Too many failed login attempts. Please try again later.")

    @classmethod
    def record_failure(cls, identifier: object, *, user=None, request=None) -> FailureOutcome:
        digest = _digest(identifier)
        fail_key = _FAIL_PREFIX + digest
        window = cls._window_seconds()
        cache.add(fail_key, 0, window)
        try:
            attempts = int(cache.incr(fail_key))
        except ValueError:  # the window expired between add() and incr()
            cache.set(fail_key, 1, window)
            attempts = 1
        if attempts < cls._threshold():
            return FailureOutcome(attempts=attempts, locked_now=False)

        lock_seconds = cls._lock_seconds()
        # add() succeeds for exactly one of several concurrent failing requests, so only one audit row is written.
        newly_locked = cache.add(_LOCK_PREFIX + digest, time.time() + lock_seconds, lock_seconds)
        cache.delete(fail_key)
        if newly_locked and user is not None:
            # Only real accounts are audited: spraying unknown identifiers must not be able to flood the audit log.
            AuditLogService.log(
                actor=None,
                action=AuditAction.ACCOUNT_LOCKED,
                target=user,
                new_value={"failed_attempts": attempts, "locked_minutes": lock_seconds // 60},
                request=request,
            )
        return FailureOutcome(attempts=attempts, locked_now=bool(newly_locked))

    @classmethod
    def record_success(cls, user) -> None:
        """Clear the failure state for the account and stamp ``last_login``."""
        cls.clear_for_user(user)
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])

    @classmethod
    def clear(cls, identifier: object) -> None:
        digest = _digest(identifier)
        cache.delete_many([_FAIL_PREFIX + digest, _LOCK_PREFIX + digest])

    @classmethod
    def clear_for_user(cls, user) -> None:
        """Accounts can sign in with email or phone; both identifiers share the lockout."""
        for identifier in (user.email, user.phone_number):
            if identifier:
                cls.clear(identifier)
