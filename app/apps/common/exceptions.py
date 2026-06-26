from typing import Any

import math
import logging
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.views import exception_handler

from apps.common.responses import error_response
from apps.accounts.phone_numbers import EXPECTED_PHONE_FORMAT, PHONE_EXAMPLES

logger = logging.getLogger(__name__)


def _default_message(exc: Exception) -> str:
    if isinstance(exc, exceptions.ValidationError):
        return "تحقق من البيانات المدخلة وحاول مرة أخرى."
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
        return "تم تجاوز عدد المحاولات المسموح. حاول مرة أخرى لاحقاً."
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


def _collect_error_codes(detail: Any) -> set[str]:
    codes: set[str] = set()
    if isinstance(detail, exceptions.ErrorDetail):
        codes.add(detail.code)
    elif isinstance(detail, dict):
        for value in detail.values():
            codes.update(_collect_error_codes(value))
    elif isinstance(detail, (list, tuple)):
        for value in detail:
            codes.update(_collect_error_codes(value))
    return codes


def _collect_error_messages(detail: Any) -> set[str]:
    messages: set[str] = set()
    if isinstance(detail, (str, exceptions.ErrorDetail)):
        messages.add(str(detail))
    elif isinstance(detail, dict):
        for value in detail.values():
            messages.update(_collect_error_messages(value))
    elif isinstance(detail, (list, tuple)):
        for value in detail:
            messages.update(_collect_error_messages(value))
    return messages


def _validation_metadata(exc: Exception) -> tuple[str | None, dict[str, Any] | None]:
    codes = _collect_error_codes(getattr(exc, "detail", None))
    messages = _collect_error_messages(getattr(exc, "detail", None))
    if "invalid_phone" in codes:
        return (
            "تحقق من البيانات المدخلة وحاول مرة أخرى.",
            {
                "error_code": "invalid_phone",
                "expected_phone_format": EXPECTED_PHONE_FORMAT,
                "examples": PHONE_EXAMPLES,
            },
        )
    if "duplicate_phone" in codes or "رقم الجوال مستخدم مسبقاً." in messages:
        return "رقم الجوال مستخدم مسبقاً.", {"error_code": "duplicate_phone"}
    if "duplicate_email" in codes or "البريد الإلكتروني مستخدم مسبقاً." in messages:
        return "البريد الإلكتروني مستخدم مسبقاً.", {"error_code": "duplicate_email"}
    return None, None


def _throttle_payload(exc: exceptions.Throttled) -> tuple[str, dict[str, str], dict[str, Any]]:
    wait = getattr(exc, "wait", None) or 0
    retry_after_seconds = max(int(math.ceil(wait)), 0)
    retry_after_minutes = max(int(math.ceil(retry_after_seconds / 60)), 1)
    message = f"تم تجاوز عدد المحاولات المسموح. حاول مرة أخرى بعد {retry_after_minutes} دقيقة."
    return (
        message,
        {"detail": message},
        {
            "error_code": "rate_limited",
            "retry_after_seconds": retry_after_seconds,
            "retry_after_minutes": retry_after_minutes,
        },
    )


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

    if isinstance(exc, exceptions.Throttled):
        message, errors, data = _throttle_payload(exc)
        response.data = {
            "success": False,
            "message": message,
            "errors": errors,
            "data": data,
        }
        if request_id:
            response.data["request_id"] = request_id
        return response

    errors = response.data
    message = _default_message(exc)
    data = None
    if isinstance(exc, exceptions.ValidationError):
        validation_message, data = _validation_metadata(exc)
        if validation_message:
            message = validation_message
    response.data = {
        "success": False,
        "message": message,
        "errors": errors,
    }
    if data:
        response.data["data"] = data
    if request_id:
        response.data["request_id"] = request_id
    if response.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
        response.data["message"] = "Server error"
        response.data["errors"] = {"detail": "An unexpected error occurred."}
    return response
