"""Django-level API error views for failures occurring outside DRF dispatch."""

from __future__ import annotations

from django.http import JsonResponse
from django.views import defaults

_STATUS_CODES = {
    400: ("BAD_REQUEST", "Bad request"),
    401: ("AUTHENTICATION_REQUIRED", "Authentication credentials were not provided"),
    403: ("PERMISSION_DENIED", "Permission denied"),
    404: ("NOT_FOUND", "Not found"),
    405: ("METHOD_NOT_ALLOWED", "Method not allowed"),
    409: ("CONFLICT", "Conflict"),
    413: ("REQUEST_TOO_LARGE", "Request entity too large"),
    415: ("UNSUPPORTED_MEDIA_TYPE", "Unsupported media type"),
    429: ("RATE_LIMITED", "Too many requests. Please try again later."),
    503: ("SERVICE_UNAVAILABLE", "Service temporarily unavailable"),
}


def api_error_response(
    request, *, status_code: int, code: str | None = None, message: str | None = None
) -> JsonResponse:
    """Build the canonical opaque JSON error response for a versioned API request."""

    default_code, default_message = _STATUS_CODES.get(status_code, ("INTERNAL_SERVER_ERROR", "Server error"))
    payload = {
        "success": False,
        "code": code or default_code,
        "message": message or default_message,
        "errors": {},
    }
    request_id = getattr(request, "request_id", None)
    if request_id:
        payload["request_id"] = request_id
    response = JsonResponse(payload, status=status_code)
    response["X-Content-Type-Options"] = "nosniff"
    response["Referrer-Policy"] = "no-referrer"
    response["Cache-Control"] = "no-store, max-age=0"
    return response


def bad_request(request, exception=None):
    if request.path.startswith("/api/"):
        return api_error_response(request, status_code=400)
    return defaults.bad_request(request, exception)


def permission_denied(request, exception, template_name="403.html"):
    if request.path.startswith("/api/"):
        return api_error_response(request, status_code=403)
    return defaults.permission_denied(request, exception, template_name=template_name)


def page_not_found(request, exception, template_name="404.html"):
    if request.path.startswith("/api/"):
        return api_error_response(request, status_code=404)
    return defaults.page_not_found(request, exception, template_name=template_name)


def server_error(request, template_name="500.html"):
    if request.path.startswith("/api/"):
        return api_error_response(request, status_code=500)
    return defaults.server_error(request, template_name=template_name)
