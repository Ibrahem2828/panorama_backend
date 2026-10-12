import json
import logging
from typing import Any

from django.core.cache import cache
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from apps.common.request_utils import get_client_ip

from .models import AuditLog

logger = logging.getLogger(__name__)

AUDIT_FAILURE_KEY_PREFIX = "audit:failures:"
_AUDIT_FAILURE_TTL_SECONDS = 3 * 24 * 3600


def audit_failure_key(day=None) -> str:
    return f"{AUDIT_FAILURE_KEY_PREFIX}{(day or timezone.now().date()).isoformat()}"


def record_audit_failure() -> None:
    """Count a failed audit write in the cache so a monitor can alert on it. Never raises."""
    key = audit_failure_key()
    try:
        cache.add(key, 0, _AUDIT_FAILURE_TTL_SECONDS)
        cache.incr(key)
    except Exception:  # noqa: BLE001 - the counter must never turn a best-effort audit into a failure
        logger.warning("audit_failure_counter_unavailable")


def audit_failures_today() -> int:
    try:
        return int(cache.get(audit_failure_key()) or 0)
    except Exception:  # noqa: BLE001
        return 0


SENSITIVE_KEYS = {
    "password",
    "token",
    "refresh",
    "access",
    "otp",
    "code",
    "secret",
    "authorization",
    "encrypted_url",
    "whatsapp_url",
    "card_image",
    "file",
    "attachment",
    "email_host_password",
    "student_number",
    "phone_number",
    "email",
}


def sanitize_value(value: Any):
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in SENSITIVE_KEYS else sanitize_value(item) for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [sanitize_value(item) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    # Decimals, datetimes, UUIDs and file handles are not JSON native; a failed write here used to
    # drop the whole audit row silently, so coerce them to something storable.
    try:
        return json.loads(json.dumps(value, cls=DjangoJSONEncoder))
    except (TypeError, ValueError):
        return str(value)


class AuditLogService:
    @staticmethod
    def log(
        actor=None,
        action: str = "",
        target=None,
        old_value=None,
        new_value=None,
        request=None,
        *,
        critical: bool = False,
    ):
        """Write an audit row.

        Default is best-effort: a failure is logged at ERROR, counted (``audit:failures:<date>``) and swallowed so
        the request still succeeds. ``critical=True`` re-raises after the same logging; use it only for
        security-critical events that sit inside an atomic block which must roll back when the trail cannot be
        written.
        """
        try:
            target_type = target.__class__.__name__ if target is not None else ""
            target_id = str(getattr(target, "id", "")) if target is not None else ""
            ip_address = get_client_ip(request) if request is not None else None
            user_agent = request.META.get("HTTP_USER_AGENT", "")[:1000] if request is not None else ""
            return AuditLog.objects.create(
                actor=actor if getattr(actor, "is_authenticated", False) else None,
                action=action,
                target_type=target_type,
                target_id=target_id,
                old_value=sanitize_value(old_value),
                new_value=sanitize_value(new_value),
                ip_address=ip_address or None,
                user_agent=user_agent,
            )
        except Exception as exc:
            record_audit_failure()
            logger.error(
                "audit_log_write_failed",
                exc_info=True,
                extra={
                    "event": "audit_log_write_failed",
                    "audit_action": str(action),
                    "audit_target_type": target.__class__.__name__ if target is not None else "",
                    "audit_target_id": str(getattr(target, "id", "")) if target is not None else "",
                    "audit_actor_id": getattr(actor, "pk", None),
                    "audit_critical": critical,
                    "failure_class": type(exc).__name__,
                },
            )
            if critical:
                raise
            return None
