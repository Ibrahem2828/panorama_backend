"""Serve a stored image through an authenticated API view (production does not publish ``/media/``).

One place for the rules every private image endpoint shares: the content type comes from the file's magic bytes
(never from the name), the ``ETag`` changes whenever the row or the file changes (so ``If-None-Match`` gets a 304),
the cache is private and the browser may not sniff the type.
"""

from __future__ import annotations

import hashlib

from django.http import FileResponse, Http404, HttpResponse
from rest_framework import status

PNG_MAGIC = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
JPEG_MAGIC = bytes([0xFF, 0xD8, 0xFF])


def sniff_image_type(handle) -> str:
    """Content type of an open image file from its first bytes; anything else is a 404 (never served)."""

    head = handle.read(16)
    handle.seek(0)
    if head.startswith(PNG_MAGIC):
        return "image/png"
    if head[:3] == JPEG_MAGIC:
        return "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    raise Http404


def image_etag(*parts: object) -> str:
    return '"' + hashlib.sha256(":".join(str(part) for part in parts).encode()).hexdigest()[:32] + '"'


def serve_stored_image(request, field_file, etag: str, *, max_age: int = 3600) -> HttpResponse | FileResponse:
    """200 with the file, or 304 when the client already holds this ``etag``; 404 when the file is gone."""

    if not field_file:
        raise Http404
    if request.headers.get("If-None-Match") == etag:
        response: HttpResponse | FileResponse = HttpResponse(status=status.HTTP_304_NOT_MODIFIED)
    else:
        try:
            handle = field_file.open("rb")
        except (FileNotFoundError, ValueError) as exc:
            raise Http404 from exc
        response = FileResponse(handle, content_type=sniff_image_type(handle))
    response["ETag"] = etag
    response["Cache-Control"] = f"private, max-age={max_age}"
    response["X-Content-Type-Options"] = "nosniff"
    return response
