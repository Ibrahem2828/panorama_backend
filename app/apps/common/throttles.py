from __future__ import annotations

import hashlib
import re
from typing import Any

from rest_framework.throttling import ScopedRateThrottle

from apps.accounts.phone_numbers import normalize_phone_number_or_none


IDENTIFIER_FIELDS_BY_SCOPE = {
    "login": ("identifier", "email", "phone_number"),
    "register": ("email", "phone_number"),
    "normal_register": ("email", "phone_number"),
    "otp_send": ("phone_number",),
    "otp_verify": ("phone_number",),
    "password_reset": ("phone_number", "email"),
    "student_account_request": ("email", "phone_number"),
}


class IdentifierScopedRateThrottle(ScopedRateThrottle):
    """
    Scoped throttle that keeps normal DRF behavior for most endpoints and adds
    request identifier entropy for auth/OTP endpoints.
    """

    def parse_rate(self, rate):
        if rate is None:
            return None, None

        match = re.fullmatch(r"(\d+)/(\d+)?([A-Za-z]+)", rate)
        if not match:
            return super().parse_rate(rate)

        num_requests = int(match.group(1))
        multiplier = int(match.group(2) or 1)
        unit = match.group(3).lower()
        if unit.startswith("s"):
            seconds = 1
        elif unit.startswith("m"):
            seconds = 60
        elif unit.startswith("h"):
            seconds = 60 * 60
        elif unit.startswith("d"):
            seconds = 60 * 60 * 24
        else:
            return super().parse_rate(rate)
        return num_requests, multiplier * seconds

    def _request_value(self, request, field_names: tuple[str, ...]) -> str:
        data: Any = getattr(request, "data", {}) or {}
        for field_name in field_names:
            value = data.get(field_name) if hasattr(data, "get") else None
            if value:
                value = str(value).strip().lower()
                if field_name in {"identifier", "phone_number"}:
                    value = normalize_phone_number_or_none(value) or value
                return value
        return ""

    def get_cache_key(self, request, view):
        self.scope = getattr(view, self.scope_attr, None)
        if not self.scope:
            return None
        self.rate = self.get_rate()
        if self.rate is None:
            return None

        ident = self.get_ident(request)
        field_names = IDENTIFIER_FIELDS_BY_SCOPE.get(self.scope)
        if field_names:
            identifier = self._request_value(request, field_names)
            if identifier:
                identifier_hash = hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:32]
                ident = f"{ident}:{identifier_hash}"
        return self.cache_format % {"scope": self.scope, "ident": ident}
