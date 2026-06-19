from typing import Any

import logging
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.views import exception_handler

from apps.common.responses import error_response

logger = logging.getLogger(__name__)


def _default_message(exc: Exception) -> str:
    if isinstance(exc, exceptions.ValidationError):
        return "Validation error"
    if isinstance(exc, exceptions.AuthenticationFailed):
        return "Authentication failed"
    if isinstance(exc, exceptions.NotAuthenticated):
        return "Authentication credentials were not provided"
    if isinstance(exc, exceptions.PermissionDenied):
        return "Permission denied"
    if isinstance(exc, Http404):
        return "Not found"
    if isinstance(exc, exceptions.NotFound):
        return "Not found"
    if isinstance(exc, exceptions.Throttled):
        return "Request was throttled"
    if isinstance(exc, exceptions.ParseError):
        return "Malformed request"
    if isinstance(exc, exceptions.UnsupportedMediaType):
        return "Unsupported media type"
    if isinstance(exc, exceptions.APIException):
        detail = getattr(exc, "detail", None)
        if isinstance(detail, (str, exceptions.ErrorDetail)):
            return str(detail)
    detail = getattr(exc, "detail", None)
    if isinstance(detail, str):
        return detail
    return "An error occurred"


def custom_exception_handler(exc: Exception, context: dict[str, Any]):
    request = context.get("request")
    request_id = getattr(request, "request_id", None)
    response = exception_handler(exc, context)
    if response is None:
        logger.exception("Unhandled API exception", exc_info=exc)
        return error_response(
            message="Server error",
            errors={"detail": "An unexpected error occurred."},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            request_id=request_id,
        )

    errors = response.data
    response.data = {
        "success": False,
        "message": _default_message(exc),
        "errors": errors,
    }
    if request_id:
        response.data["request_id"] = request_id
    if response.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
        response.data["message"] = "Server error"
        response.data["errors"] = {"detail": "An unexpected error occurred."}
    return response
