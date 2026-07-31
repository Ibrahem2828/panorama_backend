from __future__ import annotations

import hashlib
import re

from rest_framework.throttling import SimpleRateThrottle

from apps.common.request_utils import get_client_ip


def _request_ip(request) -> str:
    return get_client_ip(request) or "unknown"


def _normalise_identifier(value: object) -> str:
    return str(value or "").strip().lower()[:254]


class IdentifierRateThrottle(SimpleRateThrottle):
    """Rate limit by both source IP and submitted account identifier."""

    identifier_fields: tuple[str, ...] = ("identifier", "email", "phone_number")
    _period_units = {
        "s": 1,
        "sec": 1,
        "secs": 1,
        "second": 1,
        "seconds": 1,
        "m": 60,
        "min": 60,
        "mins": 60,
        "minute": 60,
        "minutes": 60,
        "h": 3600,
        "hr": 3600,
        "hrs": 3600,
        "hour": 3600,
        "hours": 3600,
        "d": 86400,
        "day": 86400,
        "days": 86400,
    }

    def parse_rate(self, rate: str | None) -> tuple[int | None, int | None]:
        """Support explicit sliding windows such as ``3/10min`` in addition to DRF's shorthand."""
        if rate is None:
            return None, None
        try:
            requests, period = rate.strip().lower().split("/", 1)
            num_requests = int(requests)
        except (AttributeError, ValueError) as exc:
            raise ValueError(f"Invalid throttle rate: {rate!r}") from exc

        match = re.fullmatch(r"(?:(\d+)\s*)?([a-z]+)", period.strip())
        if not match or match.group(2) not in self._period_units:
            raise ValueError(f"Unsupported throttle period: {period!r}")
        multiplier = int(match.group(1) or 1)
        return num_requests, multiplier * self._period_units[match.group(2)]

    def get_identifier(self, request) -> str:
        data = getattr(request, "data", {}) or {}
        for field in self.identifier_fields:
            if data.get(field):
                return _normalise_identifier(data.get(field))
        return "anonymous"

    def get_cache_key(self, request, view):
        if not self.rate:
            return None
        composite = f"{_request_ip(request)}|{self.get_identifier(request)}"
        digest = hashlib.sha256(composite.encode("utf-8")).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}


class LoginRateThrottle(IdentifierRateThrottle):
    scope = "auth_login"


class RegistrationRateThrottle(IdentifierRateThrottle):
    scope = "auth_register"


class OTPRequestRateThrottle(IdentifierRateThrottle):
    scope = "otp_request"


class OTPVerifyRateThrottle(IdentifierRateThrottle):
    scope = "otp_verify"


class PasswordResetRateThrottle(IdentifierRateThrottle):
    scope = "password_reset"


class FeedbackRateThrottle(SimpleRateThrottle):
    scope = "feedback_submit"

    def get_cache_key(self, request, view):
        ident = str(request.user.pk) if request.user and request.user.is_authenticated else _request_ip(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class FileTicketRateThrottle(SimpleRateThrottle):
    scope = "file_ticket"

    def get_cache_key(self, request, view):
        ident = str(request.user.pk) if request.user and request.user.is_authenticated else _request_ip(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class ExternalChannelRateThrottle(SimpleRateThrottle):
    scope = "external_channel"

    def get_cache_key(self, request, view):
        ident = str(request.user.pk) if request.user and request.user.is_authenticated else _request_ip(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class ChatMessageRateThrottle(SimpleRateThrottle):
    scope = "chat_message"

    def get_cache_key(self, request, view):
        ident = str(request.user.pk) if request.user and request.user.is_authenticated else _request_ip(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class ChatReportRateThrottle(SimpleRateThrottle):
    scope = "chat_report"

    def get_cache_key(self, request, view):
        ident = str(request.user.pk) if request.user and request.user.is_authenticated else _request_ip(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class SupportTicketRateThrottle(SimpleRateThrottle):
    scope = "support_ticket"

    def get_cache_key(self, request, view):
        ident = str(request.user.pk) if request.user and request.user.is_authenticated else _request_ip(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class SupportMessageRateThrottle(SimpleRateThrottle):
    scope = "support_message"

    def get_cache_key(self, request, view):
        ident = str(request.user.pk) if request.user and request.user.is_authenticated else _request_ip(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class LectureViewerRateThrottle(SimpleRateThrottle):
    scope = "lecture_viewer"

    def get_cache_key(self, request, view):
        ident = str(request.user.pk) if request.user and request.user.is_authenticated else _request_ip(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class LectureNotesRateThrottle(SimpleRateThrottle):
    scope = "lecture_notes"

    def get_cache_key(self, request, view):
        ident = str(request.user.pk) if request.user and request.user.is_authenticated else _request_ip(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}
