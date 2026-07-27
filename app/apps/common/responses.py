from __future__ import annotations

from typing import Any

from rest_framework.response import Response


def _request_id(request=None) -> str | None:
    return getattr(request, "request_id", None) if request is not None else None


def success_response(
    data: Any = None,
    message: str = "Operation completed successfully",
    status_code: int = 200,
    *,
    request=None,
    code: str = "OK",
) -> Response:
    payload = {
        "success": True,
        "code": code,
        "message": message,
        "data": {} if data is None else data,
    }
    request_id = _request_id(request)
    if request_id:
        payload["request_id"] = request_id
    return Response(payload, status=status_code)


def error_response(
    message: str = "An error occurred",
    errors: Any = None,
    status_code: int = 400,
    *,
    request=None,
    code: str = "ERROR",
) -> Response:
    payload = {
        "success": False,
        "code": code,
        "message": message,
        "errors": {} if errors is None else errors,
    }
    request_id = _request_id(request)
    if request_id:
        payload["request_id"] = request_id
    return Response(payload, status=status_code)
