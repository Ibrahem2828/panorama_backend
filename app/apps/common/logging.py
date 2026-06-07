from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any

REQUEST_ID_LOG_DEFAULT = "-"

_request_id: ContextVar[str] = ContextVar("request_id", default=REQUEST_ID_LOG_DEFAULT)

SENSITIVE_LOG_KEYS = {
    "access",
    "api_key",
    "authorization",
    "card_image",
    "code",
    "cookie",
    "file",
    "fcm_server_key",
    "new_password",
    "new_password_confirm",
    "old_password",
    "otp",
    "password",
    "password_confirm",
    "refresh",
    "secret",
    "token",
}


def get_request_id() -> str:
    return _request_id.get()


def set_request_id(request_id: str):
    return _request_id.set(request_id)


def reset_request_id(token) -> None:
    _request_id.reset(token)


def _is_sensitive_key(key: Any) -> bool:
    normalized_key = str(key).strip().lower()
    return normalized_key in SENSITIVE_LOG_KEYS


def sanitize_for_logging(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _is_sensitive_key(key) else sanitize_for_logging(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_for_logging(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_for_logging(item) for item in value)
    return value


class RequestIDLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True
