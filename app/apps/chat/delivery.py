"""Streaming of protected chat attachments: inline only, byte ranges, cheap revalidation."""

from __future__ import annotations

import re
from collections.abc import Iterator

from django.http import HttpResponse, StreamingHttpResponse
from django.utils.http import content_disposition_header

CHUNK = 64 * 1024
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def parse_range(header: str, size: int) -> tuple[int, int] | None | str:
    """Single ``bytes=`` range -> ``(start, end)`` inclusive; ``None`` to ignore it (serve everything);
    ``"unsatisfiable"`` when it lies outside the file. Multi-range requests are ignored (a full 200 is always valid)."""

    match = _RANGE.match((header or "").strip())
    if not match:
        return None
    first, last = match.groups()
    if not first and not last:
        return None
    if not first:  # suffix: the last N bytes
        count = int(last)
        if count == 0:
            return "unsatisfiable"
        return max(0, size - count), size - 1
    start = int(first)
    if start >= size:
        return "unsatisfiable"
    end = min(int(last), size - 1) if last else size - 1
    if end < start:
        return None
    return start, end


def _etag_matches(header: str, etag: str) -> bool:
    if not header:
        return False
    if header.strip() == "*":
        return True
    bare = etag.removeprefix("W/")
    return any(candidate.strip().removeprefix("W/") == bare for candidate in header.split(","))


def _iter_file(fieldfile, start: int, length: int) -> Iterator[bytes]:
    handle = fieldfile.open("rb")
    try:
        handle.seek(start)
        remaining = length
        while remaining > 0:
            data = handle.read(min(CHUNK, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data
    finally:
        handle.close()


def _protect(response, etag: str):
    response["ETag"] = etag
    response["Accept-Ranges"] = "bytes"
    response["Cache-Control"] = "private, no-store, max-age=0"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"
    response["Cross-Origin-Resource-Policy"] = "same-origin"
    return response


def serve_inline(request, fieldfile, *, content_type: str, filename: str, etag: str, size: int) -> HttpResponse:
    """Serve ``fieldfile`` inline (never as an attachment) honouring ``Range``, ``If-Range`` and ``If-None-Match``."""

    if _etag_matches(request.headers.get("If-None-Match", ""), etag):
        return _protect(HttpResponse(status=304), etag)

    response: HttpResponse | StreamingHttpResponse
    byte_range: tuple[int, int] | None | str = None
    range_header = request.headers.get("Range", "")
    if range_header:
        if_range = request.headers.get("If-Range", "")
        if not if_range or if_range.strip() == etag:
            byte_range = parse_range(range_header, size)

    if byte_range == "unsatisfiable":
        response = HttpResponse(status=416)
        response["Content-Range"] = f"bytes */{size}"
        return _protect(response, etag)

    if isinstance(byte_range, tuple):
        start, end = byte_range
        response = StreamingHttpResponse(
            _iter_file(fieldfile, start, end - start + 1), status=206, content_type=content_type
        )
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
        response["Content-Length"] = str(end - start + 1)
    else:
        response = StreamingHttpResponse(_iter_file(fieldfile, 0, size), content_type=content_type)
        response["Content-Length"] = str(size)
    disposition = content_disposition_header(False, filename)
    if disposition:
        response["Content-Disposition"] = disposition
    return _protect(response, etag)
