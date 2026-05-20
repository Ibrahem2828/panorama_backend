from typing import Any

from rest_framework.response import Response


def success_response(
    data: Any = None,
    message: str = "Operation completed successfully",
    status_code: int = 200,
) -> Response:
    return Response(
        {
            "success": True,
            "message": message,
            "data": {} if data is None else data,
        },
        status=status_code,
    )


def error_response(
    message: str = "An error occurred",
    errors: Any = None,
    status_code: int = 400,
) -> Response:
    return Response(
        {
            "success": False,
            "message": message,
            "errors": {} if errors is None else errors,
        },
        status=status_code,
    )
