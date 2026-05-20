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
    if isinstance(exc, Http404):
        return "Not found"
    detail = getattr(exc, "detail", None)
    if isinstance(detail, str):
        return detail
    return "An error occurred"


def custom_exception_handler(exc: Exception, context: dict[str, Any]):
    response = exception_handler(exc, context)
    if response is None:
        return response

    errors = response.data
    response.data = {
        "success": False,
        "message": _default_message(exc),
        "errors": errors,
    }
    if response.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
        response.data["message"] = "Server error"
    return response
