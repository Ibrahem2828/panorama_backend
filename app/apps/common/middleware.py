from __future__ import annotations

import re
import uuid

from django.conf import settings

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class RequestIDMiddleware:
    """Attach a safe correlation id to every request and response."""

    header_name = "HTTP_X_REQUEST_ID"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        candidate = request.META.get(self.header_name, "")
        request.request_id = candidate if _REQUEST_ID_RE.match(candidate) else uuid.uuid4().hex
        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id
        return response


class APISecurityHeadersMiddleware:
    """Apply conservative security/cache headers to API responses."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/api/"):
            response.setdefault("X-Content-Type-Options", "nosniff")
            response.setdefault("Referrer-Policy", "no-referrer")
            response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            response.setdefault("Cross-Origin-Resource-Policy", "same-site")
            if any(
                part in request.path
                for part in ("/auth/", "/verification/", "/protected-files/", "/protected-print-items/", "/verification-card-access/", "/support/attachments/", "/external-channels/")
            ):
                response.setdefault("Cache-Control", "no-store, max-age=0")
                response.setdefault("Pragma", "no-cache")
        if getattr(settings, "API_CONTENT_SECURITY_POLICY", ""):
            response.setdefault("Content-Security-Policy", settings.API_CONTENT_SECURITY_POLICY)
        return response
