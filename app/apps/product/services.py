from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import APIException

from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService

from .models import (
    AccountDeletionRequest,
    AccountDeletionStatus,
    FeatureFlag,
    IdempotencyRecord,
    MaintenanceMode,
    MobileAppReleasePolicy,
)

FEATURE_DEFAULTS = {
    "registrations_enabled": True,
    "otp_email_enabled": True,
    "lecture_upload_enabled": True,
    "lecture_processing_enabled": True,
    "lecture_viewer_enabled": True,
    "lecture_notes_enabled": True,
    "chat_enabled": True,
    "notifications_enabled": True,
    "feedback_enabled": True,
    "account_deletion_enabled": False,
}
_CONFIG_TTL_SECONDS = 60


class FeatureDisabledError(APIException):
    """Raised only at an endpoint boundary; no feature flag grants authorization."""

    status_code = 503
    default_detail = "This feature is temporarily unavailable."
    default_code = "feature_disabled"


class FeatureFlagService:
    @staticmethod
    def _cache_key(key: str, platform: str, role: str) -> str:
        return f"panorama:product:feature:v1:{key}:{platform or '-'}:{role or '-'}"

    @classmethod
    def is_enabled(cls, key: str, *, platform: str = "", role: str = "") -> bool:
        cache_key = cls._cache_key(key, platform, role)
        try:
            cached = cache.get(cache_key)
        except Exception:
            cached = None
        if cached is not None:
            return bool(cached)

        candidates = [(platform, role), (platform, ""), ("", role), ("", "")]
        flag = None
        for candidate_platform, candidate_role in candidates:
            flag = FeatureFlag.objects.filter(
                key=key,
                platform=candidate_platform,
                role=candidate_role,
                is_deleted=False,
            ).first()
            if flag is not None:
                break
        enabled = bool(flag.enabled) if flag is not None else FEATURE_DEFAULTS.get(key, False)
        try:
            cache.set(cache_key, enabled, timeout=_CONFIG_TTL_SECONDS)
        except Exception:
            pass
        return enabled

    @classmethod
    def invalidate(cls, key: str | None = None) -> None:
        # Versioned, short-lived cache entries make a cache-wide delete unnecessary and
        # avoid coupling this path to Redis availability. Signals evict known scopes.
        if key:
            for platform in ("", "android", "ios"):
                for role in ("", "admin", "it_support", "student", "normal_user"):
                    try:
                        cache.delete(cls._cache_key(key, platform, role))
                    except Exception:
                        return

    @classmethod
    def public_flags(cls, *, platform: str) -> dict[str, bool]:
        exposed_keys = FeatureFlag.objects.filter(expose_to_mobile=True, is_deleted=False).values_list("key", flat=True)
        keys = sorted(set(exposed_keys) | set(FEATURE_DEFAULTS))
        return {key: cls.is_enabled(key, platform=platform) for key in keys}


class ProductConfigurationService:
    @staticmethod
    def active_release_policy(platform: str) -> MobileAppReleasePolicy | None:
        cache_key = f"panorama:product:release:v1:{platform}"
        try:
            cached_id = cache.get(cache_key)
        except Exception:
            cached_id = None
        if cached_id:
            policy = MobileAppReleasePolicy.objects.filter(pk=cached_id, is_active=True, is_deleted=False).first()
            if policy:
                return policy
        policy = (
            MobileAppReleasePolicy.objects.filter(platform=platform, is_active=True, is_deleted=False)
            .order_by("-updated_at")
            .first()
        )
        if policy:
            try:
                cache.set(cache_key, policy.pk, timeout=_CONFIG_TTL_SECONDS)
            except Exception:
                pass
        return policy

    @staticmethod
    def active_maintenance() -> MaintenanceMode | None:
        cache_key = "panorama:product:maintenance:v1"
        try:
            cached_id = cache.get(cache_key)
        except Exception:
            cached_id = None
        if cached_id:
            mode = MaintenanceMode.objects.filter(pk=cached_id, is_deleted=False).first()
            if mode and mode.is_currently_active:
                return mode
        mode = MaintenanceMode.objects.filter(enabled=True, is_deleted=False).order_by("-updated_at").first()
        if mode and mode.is_currently_active:
            try:
                cache.set(cache_key, mode.pk, timeout=30)
            except Exception:
                pass
            return mode
        return None

    @staticmethod
    def invalidate_release(platform: str | None = None) -> None:
        if platform:
            try:
                cache.delete(f"panorama:product:release:v1:{platform}")
            except Exception:
                pass

    @staticmethod
    def invalidate_maintenance() -> None:
        try:
            cache.delete("panorama:product:maintenance:v1")
        except Exception:
            pass


def feature_enabled_or_raise(key: str, *, request=None, platform: str = "") -> None:
    role = getattr(getattr(request, "user", None), "role", "")
    if not FeatureFlagService.is_enabled(key, platform=platform, role=role):
        raise FeatureDisabledError(key)


@dataclass(frozen=True)
class IdempotencyDecision:
    replay_status: int | None = None
    replay_body: dict[str, Any] | None = None
    record_id: int | None = None


class IdempotencyService:
    """A durable, user-scoped idempotency protocol for JSON API writes."""

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _payload_hash(data: Any) -> str:
        serialized = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @classmethod
    def begin(cls, request, *, endpoint: str, required: bool = False) -> IdempotencyDecision:
        raw_key = request.headers.get("Idempotency-Key", "").strip()
        if not raw_key:
            if required:
                raise ValueError("Idempotency-Key header is required.")
            return IdempotencyDecision()
        if len(raw_key) > 255:
            raise ValueError("Idempotency-Key must not exceed 255 characters.")
        actor_id = getattr(getattr(request, "user", None), "pk", None)
        actor_key = f"user:{actor_id}" if actor_id else f"ip:{request.META.get('REMOTE_ADDR', 'unknown')}"
        key_hash = cls._digest(raw_key)
        payload_hash = cls._payload_hash(request.data)
        now = timezone.now()
        with transaction.atomic():
            IdempotencyRecord.objects.filter(expires_at__lte=now).delete()
            try:
                # A nested savepoint keeps the outer transaction usable after a
                # uniqueness race, including on SQLite-backed test environments.
                with transaction.atomic():
                    record = IdempotencyRecord.objects.create(
                        actor_key=actor_key,
                        endpoint=endpoint,
                        key_hash=key_hash,
                        payload_hash=payload_hash,
                        expires_at=now + timedelta(hours=24),
                    )
            except IntegrityError as exc:
                record = IdempotencyRecord.objects.select_for_update().get(
                    actor_key=actor_key, endpoint=endpoint, key_hash=key_hash
                )
                if record.payload_hash != payload_hash:
                    raise ValueError("Idempotency-Key cannot be reused with a different request payload.") from exc
                if record.response_status and record.response_body is not None:
                    return IdempotencyDecision(replay_status=record.response_status, replay_body=record.response_body)
                raise RuntimeError("A request with this Idempotency-Key is already in progress.") from exc
        return IdempotencyDecision(record_id=record.pk)

    @staticmethod
    def complete(decision: IdempotencyDecision, response) -> None:
        if not decision.record_id or response.status_code >= 500:
            return
        body = getattr(response, "data", None)
        if not isinstance(body, dict):
            return
        IdempotencyRecord.objects.filter(pk=decision.record_id).update(
            response_status=response.status_code,
            response_body=body,
        )


class AccountDeletionService:
    @staticmethod
    def request(user, *, reason: str = "", request=None) -> AccountDeletionRequest:
        scheduled_for = AccountDeletionRequest.default_scheduled_for()
        deletion, _ = AccountDeletionRequest.objects.update_or_create(
            user=user,
            defaults={
                "status": AccountDeletionStatus.REQUESTED,
                "requested_at": timezone.now(),
                "scheduled_for": scheduled_for,
                "cancelled_at": None,
                "completed_at": None,
                "reason": reason[:500],
                "is_deleted": False,
                "deleted_at": None,
            },
        )
        AuditLogService.log(actor=user, action=AuditAction.ACCOUNT_DELETION_REQUESTED, target=deletion, request=request)
        return deletion

    @staticmethod
    def cancel(user, *, request=None) -> AccountDeletionRequest:
        with transaction.atomic():
            deletion = AccountDeletionRequest.objects.select_for_update().get(user=user)
            if deletion.status != AccountDeletionStatus.REQUESTED:
                raise ValueError("No active account deletion request exists.")
            deletion.status = AccountDeletionStatus.CANCELLED
            deletion.cancelled_at = timezone.now()
            deletion.save(update_fields=["status", "cancelled_at", "updated_at"])
        AuditLogService.log(actor=user, action=AuditAction.ACCOUNT_DELETION_CANCELLED, target=deletion, request=request)
        return deletion

    @staticmethod
    def execute_due(limit: int = 100) -> int:
        """Anonymize due accounts without deleting records needed for retained audit trails."""

        completed = 0
        due_ids = list(
            AccountDeletionRequest.objects.filter(
                status=AccountDeletionStatus.REQUESTED,
                scheduled_for__lte=timezone.now(),
                is_deleted=False,
            )
            .order_by("scheduled_for")
            .values_list("pk", flat=True)[:limit]
        )
        for deletion_id in due_ids:
            with transaction.atomic():
                deletion = AccountDeletionRequest.objects.select_for_update().select_related("user").get(pk=deletion_id)
                if deletion.status != AccountDeletionStatus.REQUESTED or deletion.scheduled_for > timezone.now():
                    continue
                user = deletion.user
                suffix = hashlib.sha256(f"{user.pk}:{deletion.pk}".encode()).hexdigest()[:16]
                from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

                from apps.notifications.models import DeviceToken
                from apps.product.models import DeviceInstallation

                for token in OutstandingToken.objects.filter(user=user):
                    BlacklistedToken.objects.get_or_create(token=token)
                DeviceToken.objects.filter(user=user).update(is_active=False, updated_at=timezone.now())
                DeviceInstallation.objects.filter(user=user, revoked_at__isnull=True).update(
                    push_token=None,
                    notifications_enabled=False,
                    revoked_at=timezone.now(),
                    updated_at=timezone.now(),
                )
                user.full_name = "Deleted user"
                user.email = f"deleted-{user.pk}-{suffix}@invalid.local"
                user.phone_number = f"deleted-{user.pk}-{suffix[:8]}"
                user.username = None
                user.is_active = False
                user.is_deleted = True
                user.deleted_at = timezone.now()
                user.set_unusable_password()
                user.save()
                deletion.status = AccountDeletionStatus.COMPLETED
                deletion.completed_at = timezone.now()
                deletion.save(update_fields=["status", "completed_at", "updated_at"])
                AuditLogService.log(actor=None, action=AuditAction.ACCOUNT_DELETION_COMPLETED, target=deletion)
                completed += 1
        return completed
