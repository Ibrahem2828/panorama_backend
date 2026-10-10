from __future__ import annotations

import logging
from socket import timeout as SocketTimeout
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError
from django.http import Http404
from redis.exceptions import RedisError
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler

from apps.common.responses import error_response

api_error_logger = logging.getLogger("panorama.api.errors")


class IdempotencyInProgress(exceptions.APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "A request with this Idempotency-Key is already in progress."
    default_code = "idempotency_in_progress"


class IdempotencyKeyReused(exceptions.APIException):
    """The same Idempotency-Key was sent with a different request body (the retry is not a retry)."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = "This Idempotency-Key was already used for a different request."
    default_code = "idempotency_key_reused"


DEPENDENCY_EXCEPTION_TYPES = (DatabaseError, RedisError, TimeoutError, SocketTimeout, ImproperlyConfigured)


def is_dependency_error(exc: Exception) -> bool:
    """Return whether an exception represents a required runtime dependency failure."""

    return isinstance(exc, DEPENDENCY_EXCEPTION_TYPES)


def _request_id(request) -> str | None:
    return getattr(request, "request_id", None) if request is not None else None


def _log_api_failure(event: str, exc: Exception, request, *, status_code: int, code: str) -> None:
    """Log only classification and correlation metadata; never request payloads or exception text."""

    api_error_logger.exception(
        event,
        extra={
            "request_id": _request_id(request),
            "status": status_code,
            "code": code,
            "failure_class": type(exc).__name__,
        },
    )


def dependency_error_response(exc: Exception, request) -> Response:
    """Return the stable, non-enumerating API result for a required dependency failure."""

    code = (
        "SERVICE_CONFIGURATION_INVALID" if isinstance(exc, ImproperlyConfigured) else "SERVICE_DEPENDENCY_UNAVAILABLE"
    )
    _log_api_failure("api_dependency_unavailable", exc, request, status_code=503, code=code)
    return error_response(
        message="A required service dependency is unavailable.",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        request=request,
        code=code,
    )


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
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: "REQUEST_TOO_LARGE",
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: "UNSUPPORTED_MEDIA_TYPE",
        status.HTTP_429_TOO_MANY_REQUESTS: "RATE_LIMITED",
        status.HTTP_503_SERVICE_UNAVAILABLE: "SERVICE_UNAVAILABLE",
    }
    if isinstance(exc, exceptions.AuthenticationFailed):
        return "AUTHENTICATION_FAILED"
    if getattr(exc, "default_code", "") == "feature_disabled":
        return "FEATURE_DISABLED"
    if getattr(exc, "default_code", "") == "idempotency_in_progress":
        return "IDEMPOTENCY_IN_PROGRESS"
    if getattr(exc, "default_code", "") == "idempotency_key_reused":
        return "IDEMPOTENCY_KEY_REUSED"
    if getattr(exc, "default_code", "") == "chat_ticket_storage_unavailable":
        return "CHAT_TICKET_SERVICE_UNAVAILABLE"
    return mapping.get(status_code, "SERVER_ERROR" if status_code >= 500 else "REQUEST_FAILED")


def custom_exception_handler(exc: Exception, context: dict[str, Any]):
    response = exception_handler(exc, context)
    if response is None:
        request = context.get("request")
        if isinstance(exc, ImproperlyConfigured) or is_dependency_error(exc):
            return dependency_error_response(exc, request)
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
        response.data["retry_after_seconds"] = int(getattr(exc, "wait", 0) or 0)
    if response.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
        response.data["message"] = "Server error"
        response.data["errors"] = {}
    return response
