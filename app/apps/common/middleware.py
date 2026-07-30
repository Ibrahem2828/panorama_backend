from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid

from django.conf import settings

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
request_logger = logging.getLogger("panorama.request")


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


class StructuredRequestLogMiddleware:
    """Log request metadata only; never request bodies, query strings, or credentials."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.perf_counter()
        response = self.get_response(request)
        if request.path.startswith("/api/"):
            user = getattr(request, "user", None)
            user_id_hash = None
            if getattr(user, "is_authenticated", False):
                user_id_hash = hashlib.sha256(str(user.pk).encode("utf-8")).hexdigest()[:16]
            match = getattr(request, "resolver_match", None)
            route = getattr(match, "route", None) or "unresolved"
            request_logger.info(
                "http_request",
                extra={
                    "request_id": getattr(request, "request_id", None),
                    "user_id_hash": user_id_hash,
                    "route": route,
                    "method": request.method,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
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
