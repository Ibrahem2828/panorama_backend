"""Structured, privacy-preserving logging utilities for production."""

from __future__ import annotations

import json
import logging
import re
import traceback
from datetime import UTC, datetime
from typing import Any

_SENSITIVE_VALUE = re.compile(r"(?i)(authorization|token|password|secret|api[_-]?key|cookie)\s*([=:])\s*[^,;]+")


class SensitiveDataFilter(logging.Filter):
    """Remove common credential forms before a record reaches stdout."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Format first so %-arguments are redacted too and are not lost when the args are cleared.
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a malformed log call must never break the request
            message = str(record.msg)
        record.msg = _SENSITIVE_VALUE.sub(r"\1\2[REDACTED]", message)
        record.args = ()
        return True


class JSONFormatter(logging.Formatter):
    """Emit a stable, one-line JSON log record without request payloads."""

    request_fields = (
        "request_id",
        "user_id_hash",
        "route",
        "method",
        "status",
        "duration_ms",
        "code",
        "dependency",
        "failure_class",
        "pending_migration_count",
    )

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
        if record.exc_info and record.exc_info[0] is not None:
            # Exception strings can contain user input, so keep the class and the call sites only.
            payload["exception"] = record.exc_info[0].__name__
            payload["frames"] = [
                f"{frame.filename.rsplit('site-packages/', 1)[-1]}:{frame.lineno}:{frame.name}"
                for frame in traceback.extract_tb(record.exc_info[2])[-8:]
            ]
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
