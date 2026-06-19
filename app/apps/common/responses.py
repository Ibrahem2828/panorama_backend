from typing import Any

from rest_framework.response import Response


def success_response(
    data: Any = None,
    message: str = "Operation completed successfully",
    status_code: int = 200,
) -> Response:
    payload = {
        "success": True,
        "message": message,
        "data": {} if data is None else data,
    }
    return Response(
        payload,
        status=status_code,
    )


def error_response(
    message: str = "An error occurred",
    errors: Any = None,
    status_code: int = 400,
    request_id: str | None = None,
) -> Response:
    payload = {
        "success": False,
        "message": message,
        "errors": {} if errors is None else errors,
    }
    if request_id:
        payload["request_id"] = request_id
    return Response(payload, status=status_code)
