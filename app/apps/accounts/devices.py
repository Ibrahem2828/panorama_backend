"""Account device binding: one active device per student account.

Purpose (owner's words): students must not share accounts, because subject registration and group joining become paid.
A student account is therefore bound to ONE device. The app generates a random UUID once, keeps it in secure storage and
sends it as ``X-Panorama-Device-Id`` (plus optional ``X-Panorama-Device-Name`` and ``X-Panorama-Platform``).

Rules
-----
* Students only; staff and normal users are exempt.
* First login (or, for sessions that existed before this feature, first refresh/authenticated request that carries the
  header) binds the device.
* Login from another device proves the password but issues NO tokens: it answers ``409 DEVICE_MISMATCH`` carrying a
  short-lived ``device_switch_token``. ``POST auth/device/switch/`` with that token takes the account over: the old
  binding becomes ``replaced``, every session of the account is invalidated (``User.invalidate_sessions``: refresh tokens
  blacklisted, ``session_version`` bumped, so access tokens and chat WebSockets stop working) and the new device is bound.
* Self-service switches are limited (``DEVICE_SWITCH_LIMIT_PER_TERM`` per rolling ``DEVICE_SWITCH_WINDOW_DAYS``); beyond
  that the student must contact an administrator who can reset the binding from the dashboard.
* Refresh from another device is refused with ``DEVICE_MISMATCH`` and NO switch token: a refresh token alone does not
  prove the password, so the app must send the user back to the login screen.
* ``DEVICE_BINDING_ENFORCED=False`` (default) only records bindings when the header is present and never blocks.

Only a SHA-256 of the device id is stored, never the id itself.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed, Throttled

from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.responses import error_response

from .choices import AccountDeviceBoundVia, AccountDeviceStatus, UserRole
from .models import AccountDevice, User

logger = logging.getLogger(__name__)

DEVICE_ID_HEADER = "X-Panorama-Device-Id"
DEVICE_NAME_HEADER = "X-Panorama-Device-Name"
DEVICE_PLATFORM_HEADER = "X-Panorama-Platform"
DEVICE_HASH_CLAIM = "dh"
_SWITCH_SALT = "panorama.account-device-switch"
_PLATFORMS = {"android", "ios", "web"}
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class DeviceInfo:
    device_hash: str
    name: str
    platform: str


class DeviceBindingRefused(Exception):
    """A device rule stopped the request. The view turns it into the standard error envelope."""

    def __init__(self, *, code: str, message: str, status_code: int, errors: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.errors = errors or {}


class _LimitReached(Exception):
    pass


class DeviceAuthenticationFailed(AuthenticationFailed):
    """Raised from the JWT authenticator when an access token is used from a device other than the bound one."""

    def __init__(self, detail: str, api_code: str, message: str | None = None):
        super().__init__(detail)
        self.api_code = api_code
        self.api_message = message or detail


MISMATCH_MESSAGE = "تم تسجيل الدخول من جهاز آخر"
ADMIN_REVOKED_MESSAGE = "أنهى فريق الدعم جلستك. سجّل الدخول من جديد."


def invalidated_session_error(user_id, device_hash: str | None) -> DeviceAuthenticationFailed | None:
    """Why a session that fails the session-version / blacklist check was ended, when that is a device event.

    Only a token that names (``dh``) a device of THIS user that is no longer bound gets a specific answer, so the code
    reveals nothing the holder of that device's own token did not already know:
    * the device was replaced by a self-service switch (and is not bound again) -> ``DEVICE_MISMATCH``;
    * the binding was revoked by staff (reset) -> ``SESSION_REVOKED_BY_ADMIN``.
    Every other invalidation (password change, logout-all, no device claim) returns None: the generic 401 stays."""

    if not device_hash or not user_id:
        return None
    rows = AccountDevice.objects.filter(user_id=user_id, device_hash=device_hash).values_list("status", flat=True)
    statuses = set(rows)
    if not statuses or AccountDeviceStatus.ACTIVE in statuses:
        return None
    if AccountDeviceStatus.REPLACED in statuses:
        return DeviceAuthenticationFailed(MISMATCH_MESSAGE, "DEVICE_MISMATCH")
    return DeviceAuthenticationFailed(ADMIN_REVOKED_MESSAGE, "SESSION_REVOKED_BY_ADMIN")


def device_refusal_response(refusal: DeviceBindingRefused, request):
    return error_response(
        message=refusal.message,
        errors=refusal.errors,
        status_code=refusal.status_code,
        request=request,
        code=refusal.code,
    )


def hash_device_id(device_id: str) -> str:
    return hashlib.sha256(f"panorama-account-device:{device_id}".encode()).hexdigest()


def read_device_headers(request) -> DeviceInfo | None:
    """The device the request comes from, or None when the header is missing or is not a UUID."""

    raw = (request.META.get("HTTP_X_PANORAMA_DEVICE_ID") or "").strip()
    if not raw:
        return None
    try:
        device_id = str(uuid.UUID(raw))
    except ValueError:
        return None
    name = _CONTROL_CHARS.sub("", request.META.get("HTTP_X_PANORAMA_DEVICE_NAME") or "").strip()[:80]
    platform = (request.META.get("HTTP_X_PANORAMA_PLATFORM") or "").strip().lower()
    return DeviceInfo(hash_device_id(device_id), name, platform if platform in _PLATFORMS else "")


def binding_enforced() -> bool:
    return bool(settings.DEVICE_BINDING_ENFORCED)


def _required() -> DeviceBindingRefused:
    return DeviceBindingRefused(
        code="DEVICE_ID_REQUIRED",
        message="A valid X-Panorama-Device-Id header is required. Update the app.",
        status_code=400,
    )


def _window_start():
    return timezone.now() - timedelta(days=int(settings.DEVICE_SWITCH_WINDOW_DAYS))


def switches_used(user: User) -> int:
    return AccountDevice.objects.filter(
        user=user,
        bound_via=AccountDeviceBoundVia.SELF_SWITCH,
        counts_toward_limit=True,
        bound_at__gte=_window_start(),
    ).count()


def switch_state(user: User) -> dict[str, int]:
    limit = int(settings.DEVICE_SWITCH_LIMIT_PER_TERM)
    used = switches_used(user)
    return {
        "switch_limit": limit,
        "switches_used": used,
        "switches_remaining": max(0, limit - used),
        "window_days": int(settings.DEVICE_SWITCH_WINDOW_DAYS),
    }


class DeviceBindingService:
    @staticmethod
    def applies_to(user) -> bool:
        return getattr(user, "role", None) == UserRole.STUDENT

    # -- binding --------------------------------------------------------------------------------------------------

    @staticmethod
    def active_device(user) -> AccountDevice | None:
        return AccountDevice.objects.filter(user=user, status=AccountDeviceStatus.ACTIVE).first()

    @classmethod
    def _bind(cls, user, info: DeviceInfo, via: str, request=None) -> AccountDevice | None:
        """Create the active binding; returns None when a concurrent request bound another device first."""

        now = timezone.now()
        try:
            with transaction.atomic():
                device = AccountDevice.objects.create(
                    user=user,
                    device_hash=info.device_hash,
                    name=info.name,
                    platform=info.platform,
                    bound_via=via,
                    first_seen_at=now,
                    last_seen_at=now,
                    bound_at=now,
                )
        except IntegrityError:
            return None
        AuditLogService.log(
            actor=user,
            action=AuditAction.ACCOUNT_DEVICE_BOUND,
            target=user,
            new_value={"device_id": device.pk, "platform": info.platform, "via": via},
            request=request,
        )
        return device

    @staticmethod
    def _touch(device: AccountDevice, info: DeviceInfo) -> None:
        device.last_seen_at = timezone.now()
        fields = ["last_seen_at", "updated_at"]
        if info.name and info.name != device.name:
            device.name = info.name
            fields.append("name")
        if info.platform and info.platform != device.platform:
            device.platform = info.platform
            fields.append("platform")
        device.save(update_fields=fields)

    @classmethod
    def _resolve(cls, user, info: DeviceInfo, *, via: str, request) -> tuple[str, AccountDevice | None]:
        """("bound" | "same" | "mismatch", the active binding). Binds when the account has none yet."""

        for _ in range(2):
            active = cls.active_device(user)
            if active is None:
                if cls._bind(user, info, via, request) is not None:
                    return "bound", cls.active_device(user)
                continue  # lost a race with a concurrent first login: re-read
            if active.device_hash == info.device_hash:
                cls._touch(active, info)
                return "same", active
            return "mismatch", active
        return "mismatch", cls.active_device(user)

    # -- login / refresh ------------------------------------------------------------------------------------------

    @classmethod
    def gate_login(cls, user, request) -> str | None:
        """Run after the password was verified, before tokens exist. Returns the device hash to put in the tokens
        (None: no claim). Raises ``DeviceBindingRefused`` for a mismatch (enforced) or a missing header (enforced)."""

        if not cls.applies_to(user):
            return None
        info = read_device_headers(request)
        if info is None:
            if binding_enforced():
                raise _required()
            return None
        outcome, active = cls._resolve(user, info, via=AccountDeviceBoundVia.FIRST_LOGIN, request=request)
        if outcome != "mismatch":
            return info.device_hash
        assert active is not None
        cls._record_mismatch(user, info, active, request)
        if not binding_enforced():
            return None
        raise cls._mismatch_error(user, info, active, with_switch_token=True)

    @classmethod
    def gate_refresh(cls, user, request, token_hash: str | None) -> str | None:
        """Same as ``gate_login`` for a refresh token; a mismatch never carries a switch token."""

        if not cls.applies_to(user):
            return None
        info = read_device_headers(request)
        if info is None:
            if binding_enforced():
                raise _required()
            return token_hash
        outcome, active = cls._resolve(user, info, via=AccountDeviceBoundVia.LEGACY_SESSION, request=request)
        if outcome != "mismatch":
            return info.device_hash
        assert active is not None
        cls._record_mismatch(user, info, active, request)
        if not binding_enforced():
            return token_hash
        raise cls._mismatch_error(user, info, active, with_switch_token=False)

    @classmethod
    def check_request(cls, user, validated_token, request) -> None:
        """Authenticated request while enforcement is on: the header must match the device the token was issued to."""

        if not binding_enforced() or not cls.applies_to(user):
            return
        info = read_device_headers(request)
        if info is None:
            raise DeviceAuthenticationFailed("A valid X-Panorama-Device-Id header is required.", "DEVICE_ID_REQUIRED")
        claim = validated_token.get(DEVICE_HASH_CLAIM)
        if claim:
            if claim != info.device_hash:
                raise DeviceAuthenticationFailed(MISMATCH_MESSAGE, "DEVICE_MISMATCH")
            return
        # A session issued before the feature existed: the first request with the header binds (or is refused).
        outcome, _active = cls._resolve(user, info, via=AccountDeviceBoundVia.LEGACY_SESSION, request=request)
        if outcome == "mismatch":
            raise DeviceAuthenticationFailed(MISMATCH_MESSAGE, "DEVICE_MISMATCH")

    # -- mismatch handling ----------------------------------------------------------------------------------------

    @staticmethod
    def _throttle_mismatch(user) -> None:
        key = f"device:mismatch:{user.pk}"
        cache.add(key, 0, 3600)
        try:
            attempts = int(cache.incr(key))
        except ValueError:
            cache.set(key, 1, 3600)
            attempts = 1
        if attempts > int(settings.DEVICE_MISMATCH_MAX_PER_HOUR):
            raise Throttled(wait=3600, detail="Too many attempts from another device. Please try again later.")

    @classmethod
    def _record_mismatch(cls, user, info: DeviceInfo, active: AccountDevice, request) -> None:
        if binding_enforced():
            cls._throttle_mismatch(user)
        AuditLogService.log(
            actor=user,
            action=AuditAction.ACCOUNT_DEVICE_MISMATCH,
            target=user,
            new_value={
                "enforced": binding_enforced(),
                "attempt_platform": info.platform,
                "bound_device_id": active.pk,
            },
            request=request,
        )

    @classmethod
    def _mismatch_error(cls, user, info: DeviceInfo, active: AccountDevice, *, with_switch_token: bool):
        state = switch_state(user)
        errors: dict[str, Any] = {
            "bound_device": {
                "name": active.name[:40],
                "platform": active.platform,
                "last_seen_at": active.last_seen_at.isoformat(),
            },
            "can_switch": state["switches_remaining"] > 0,
            "switches_remaining": state["switches_remaining"],
            "device_switch_token": cls.issue_switch_token(user, info, active) if with_switch_token else None,
            "device_switch_token_expires_in": int(settings.DEVICE_SWITCH_TOKEN_TTL_SECONDS),
        }
        return DeviceBindingRefused(
            code="DEVICE_MISMATCH",
            message="This account is bound to another device.",
            status_code=409,
            errors=errors,
        )

    # -- switching ------------------------------------------------------------------------------------------------

    @staticmethod
    def issue_switch_token(user, info: DeviceInfo, active: AccountDevice) -> str:
        # Stateless and single-use: it names the binding it replaces, so once the switch happened (or staff reset the
        # binding) the same token no longer matches the account's active device.
        return signing.dumps({"u": user.pk, "d": info.device_hash, "a": active.pk}, salt=_SWITCH_SALT, compress=True)

    @classmethod
    def switch(cls, token: str, request) -> tuple[User, DeviceInfo]:
        info = read_device_headers(request)
        if info is None:
            raise _required()
        invalid = DeviceBindingRefused(
            code="DEVICE_SWITCH_TOKEN_INVALID",
            message="The device switch link expired or was already used. Sign in again.",
            status_code=400,
        )
        try:
            payload = signing.loads(
                str(token or ""), salt=_SWITCH_SALT, max_age=int(settings.DEVICE_SWITCH_TOKEN_TTL_SECONDS)
            )
            user_id, token_hash, bound_id = int(payload["u"]), str(payload["d"]), int(payload["a"])
        except (signing.BadSignature, KeyError, TypeError, ValueError):
            raise invalid from None
        if token_hash != info.device_hash:
            raise invalid
        limit_state: dict[str, int] = {}
        try:
            user = cls._switch_locked(user_id, bound_id, info, request, invalid, limit_state)
        except _LimitReached:
            user = User.objects.get(pk=user_id)
            AuditLogService.log(
                actor=user,
                action=AuditAction.ACCOUNT_DEVICE_SWITCH_REFUSED,
                target=user,
                new_value={"reason": "limit_reached", **limit_state},
                request=request,
            )
            raise DeviceBindingRefused(
                code="DEVICE_SWITCH_LIMIT_REACHED",
                message="You have used your device switches. Contact Panorama support to change your device.",
                status_code=403,
                errors={**limit_state, "contact_admin": True},
            ) from None
        cls._notify_switched(user)
        return user, info

    @classmethod
    def _switch_locked(cls, user_id, bound_id, info, request, invalid, limit_state) -> User:
        with transaction.atomic():
            user = User.objects.select_for_update().filter(pk=user_id, is_active=True, is_deleted=False).first()
            if user is None or not cls.applies_to(user):
                raise invalid
            active = (
                AccountDevice.objects.select_for_update().filter(user=user, status=AccountDeviceStatus.ACTIVE).first()
            )
            if active is None or active.pk != bound_id or active.device_hash == info.device_hash:
                raise invalid
            state = switch_state(user)
            if state["switches_remaining"] <= 0:
                limit_state.update(
                    state
                )  # refused below, after this (read-only) transaction ended: the audit row must persist
                raise _LimitReached
            now = timezone.now()
            active.status = AccountDeviceStatus.REPLACED
            active.replaced_at = now
            active.save(update_fields=["status", "replaced_at", "updated_at"])
            AccountDevice.objects.create(
                user=user,
                device_hash=info.device_hash,
                name=info.name,
                platform=info.platform,
                bound_via=AccountDeviceBoundVia.SELF_SWITCH,
                first_seen_at=now,
                last_seen_at=now,
                bound_at=now,
            )
            # Old device: refresh tokens blacklisted, access tokens and chat sockets fail on the session version.
            user.invalidate_sessions()
            AuditLogService.log(
                actor=user,
                action=AuditAction.ACCOUNT_DEVICE_SWITCHED,
                target=user,
                old_value={"device_id": active.pk, "platform": active.platform},
                new_value={"platform": info.platform, "switches_used_after": state["switches_used"] + 1},
                request=request,
                critical=True,
            )
        return user

    @staticmethod
    def _notify_switched(user) -> None:
        from apps.notifications.models import NotificationType
        from apps.notifications.services import NotificationService

        try:
            NotificationService.create_notification(
                user,
                title="تم تغيير جهاز حسابك",
                body="تم ربط حسابك بجهاز جديد وإنهاء الجلسات على الجهاز السابق. إن لم تكن أنت، تواصل مع الدعم.",
                type=NotificationType.SYSTEM,
                data={"kind": "account_device_switched"},
                title_ar="تم تغيير جهاز حسابك",
                title_en="Your account device was changed",
                body_ar="تم ربط حسابك بجهاز جديد وإنهاء الجلسات على الجهاز السابق. إن لم تكن أنت، تواصل مع الدعم.",
                body_en="Your account is now bound to a new device and the previous device was signed out.",
            )
        except Exception:  # noqa: BLE001 - a notification must never undo a completed switch
            logger.exception("device_switch_notification_failed", extra={"user_id": user.pk})

    # -- staff ----------------------------------------------------------------------------------------------------

    @classmethod
    def reset(cls, user, *, actor, reason: str = "", request=None) -> dict[str, Any]:
        """Staff reset: the binding is revoked, the switch counter restarts and every session ends. The student's next
        login binds whatever device they use."""

        with transaction.atomic():
            locked = User.objects.select_for_update().get(pk=user.pk)
            now = timezone.now()
            revoked = AccountDevice.objects.filter(user=locked, status=AccountDeviceStatus.ACTIVE).update(
                status=AccountDeviceStatus.REVOKED, replaced_at=now, updated_at=now
            )
            cleared = AccountDevice.objects.filter(user=locked, counts_toward_limit=True).update(
                counts_toward_limit=False, updated_at=now
            )
            locked.invalidate_sessions()
            AuditLogService.log(
                actor=actor,
                action=AuditAction.ACCOUNT_DEVICE_RESET,
                target=locked,
                new_value={"revoked_active": revoked, "counter_rows_cleared": cleared, "reason": reason[:255]},
                request=request,
                critical=True,
            )
        cache.delete(f"device:mismatch:{locked.pk}")
        return {"revoked_active": revoked}

    @staticmethod
    def revoke_for_deleted_account(user) -> None:
        """Account anonymization: no binding survives, and nothing identifying is kept on the rows."""

        now = timezone.now()
        AccountDevice.objects.filter(user=user, status=AccountDeviceStatus.ACTIVE).update(
            status=AccountDeviceStatus.REVOKED, replaced_at=now
        )
        AccountDevice.objects.filter(user=user).update(name="", updated_at=now)
