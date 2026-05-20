import logging
from typing import Any

from .models import AuditLog

logger = logging.getLogger(__name__)

SENSITIVE_KEYS = {"password", "token", "refresh", "access", "otp", "code", "secret", "authorization"}


def sanitize_value(value: Any):
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in SENSITIVE_KEYS else sanitize_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_value(item) for item in value]
    return value


class AuditLogService:
    @staticmethod
    def log(actor=None, action: str = "", target=None, old_value=None, new_value=None, request=None):
        try:
            target_type = target.__class__.__name__ if target is not None else ""
            target_id = str(getattr(target, "id", "")) if target is not None else ""
            ip_address = None
            user_agent = ""
            if request is not None:
                ip_address = request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "")).split(",")[0]
                user_agent = request.META.get("HTTP_USER_AGENT", "")
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
        except Exception:
            logger.exception("Failed to write audit log")
            return None
