from __future__ import annotations

import hashlib
from typing import Any

from rest_framework.throttling import ScopedRateThrottle


IDENTIFIER_FIELDS_BY_SCOPE = {
    "login": ("identifier", "email", "phone_number"),
    "register": ("email", "phone_number"),
    "otp_send": ("phone_number",),
    "otp_verify": ("phone_number",),
    "password_reset": ("phone_number", "email"),
}


class IdentifierScopedRateThrottle(ScopedRateThrottle):
    """
    Scoped throttle that keeps normal DRF behavior for most endpoints and adds
    request identifier entropy for auth/OTP endpoints.
    """

    def _request_value(self, request, field_names: tuple[str, ...]) -> str:
        data: Any = getattr(request, "data", {}) or {}
        for field_name in field_names:
            value = data.get(field_name) if hasattr(data, "get") else None
            if value:
                return str(value).strip().lower()
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
