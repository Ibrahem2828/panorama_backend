from __future__ import annotations

from typing import Any

from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.views import exception_handler


def _default_message(exc: Exception) -> str:
    if isinstance(exc, exceptions.ValidationError):
        return "Validation error"
    if isinstance(exc, exceptions.AuthenticationFailed):
        return "Authentication failed"
    if isinstance(exc, exceptions.NotAuthenticated):
        return "Authentication credentials were not provided"
    if isinstance(exc, exceptions.PermissionDenied):
        return "Permission denied"
    if isinstance(exc, exceptions.Throttled):
        return "Too many requests. Please try again later."
    if isinstance(exc, Http404):
        return "Not found"
    detail = getattr(exc, "detail", None)
    if isinstance(detail, str):
        return detail
    return "An error occurred"


def _error_code(exc: Exception, status_code: int) -> str:
    mapping = {
        status.HTTP_400_BAD_REQUEST: "VALIDATION_ERROR",
        status.HTTP_401_UNAUTHORIZED: "AUTHENTICATION_REQUIRED",
        status.HTTP_403_FORBIDDEN: "PERMISSION_DENIED",
        status.HTTP_404_NOT_FOUND: "NOT_FOUND",
        status.HTTP_405_METHOD_NOT_ALLOWED: "METHOD_NOT_ALLOWED",
        status.HTTP_409_CONFLICT: "CONFLICT",
        status.HTTP_429_TOO_MANY_REQUESTS: "RATE_LIMITED",
    }
    if isinstance(exc, exceptions.AuthenticationFailed):
        return "AUTHENTICATION_FAILED"
    if getattr(exc, "default_code", "") == "feature_disabled":
        return "FEATURE_DISABLED"
    if getattr(exc, "default_code", "") == "idempotency_in_progress":
        return "IDEMPOTENCY_IN_PROGRESS"
    return mapping.get(status_code, "SERVER_ERROR" if status_code >= 500 else "REQUEST_FAILED")


def custom_exception_handler(exc: Exception, context: dict[str, Any]):
    response = exception_handler(exc, context)
    if response is None:
        return response

    request = context.get("request")
    errors = response.data
    response.data = {
        "success": False,
        "code": _error_code(exc, response.status_code),
        "message": _default_message(exc),
        "errors": errors,
    }
    request_id = getattr(request, "request_id", None)
    if request_id:
        response.data["request_id"] = request_id
    if isinstance(exc, exceptions.Throttled):
        response.data["retry_after_seconds"] = int(exc.wait or 0)
    if response.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
        response.data["message"] = "Server error"
        response.data["errors"] = {}
    return response
