from __future__ import annotations

import re
import uuid

from apps.common.logging import reset_request_id, set_request_id

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _clean_request_id(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip()
    if _REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return None


class RequestIDMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = _clean_request_id(request.headers.get(REQUEST_ID_HEADER)) or str(uuid.uuid4())
        request.request_id = request_id
        token = set_request_id(request_id)
        try:
            response = self.get_response(request)
        finally:
            reset_request_id(token)

        response[REQUEST_ID_HEADER] = request_id
        return response
