"""Structured, privacy-preserving logging utilities for production."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

_SENSITIVE_VALUE = re.compile(r"(?i)(authorization|token|password|secret|api[_-]?key|cookie)\\s*([=:])\\s*[^\\s,;]+")


class SensitiveDataFilter(logging.Filter):
    """Remove common credential forms before a record reaches stdout."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _SENSITIVE_VALUE.sub(r"\\1\\2[REDACTED]", record.msg)
            record.args = ()
        return True


class JSONFormatter(logging.Formatter):
    """Emit a stable, one-line JSON log record without request payloads."""

    request_fields = ("request_id", "user_id_hash", "route", "method", "status", "duration_ms")

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in self.request_fields:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            # Exception strings can contain user input; retain a signal without the payload.
            payload["exception"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
