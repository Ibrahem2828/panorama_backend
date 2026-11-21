"""Shared machinery of the offline protected packages (lectures and library files).

Both ``apps.lectures`` and ``apps.files`` own their blob/license models (so lecture tables and responses stay
byte-compatible); everything that does not depend on the owning model lives here: the PNOF encryption of a source
file, the content-key wrapping, license state/payload helpers, the Range/ETag streaming response, the ``sync``
reconciliation and the combined ``revoke_all_user_offline_licenses`` hook used by the device-switch flows.
Format reference: ``docs/OFFLINE_LECTURE_PACKAGES.md`` (PNOF v1 is unchanged).
"""

from __future__ import annotations

import base64
import hashlib
import os
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import IO, Any

from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.utils import timezone
from rest_framework import status

from apps.common.crypto import decrypt_text, encrypt_text

DEVICE_HEADER = "X-Panorama-Device-Id"

# Revocation reasons (stored in ``revoke_reason``; the app may show or log them).
REASON_MEMBERSHIP_ENDED = "membership_ended"
REASON_ACCESS_LOST = "access_lost"
REASON_LECTURE_UNAVAILABLE = "lecture_unavailable"
REASON_LECTURE_REPLACED = "lecture_replaced"
REASON_FILE_UNAVAILABLE = "file_unavailable"
REASON_FILE_REPLACED = "file_replaced"
REASON_RELEASED = "released_by_device"
REASON_STAFF = "revoked_by_staff"
REASON_DEVICE_REPLACED = "device_replaced"
REASON_ACCOUNT_DISABLED = "account_disabled"
REASON_EXPIRED = "expired"


def device_hash_from_request(request) -> str:
    """SHA-256 of the optional ``X-Panorama-Device-Id`` header (same convention as the device binding)."""

    raw = (request.headers.get(DEVICE_HEADER, "") or "").strip()[:256]
    return hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else ""


def safe_uuid(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


# -- encryption ---------------------------------------------------------------------------------------------------------


@dataclass
class EncryptedPackage:
    """The result of ``encrypt_source``: ``cipher`` is a seeked-to-start temporary file the caller must close."""

    cipher: IO[bytes]
    key_id: uuid.UUID
    wrapped_key: str
    info: Any  # offline_crypto.PackageInfo


def wrap_key(key: bytes) -> str:
    return encrypt_text(base64.b64encode(key).decode("ascii"))


def unwrap_key(wrapped: str) -> bytes:
    return base64.b64decode(decrypt_text(wrapped))


def encrypt_source(source: IO[bytes], plaintext_size: int, chunk_size: int) -> EncryptedPackage:
    """Encrypt ``source`` into a temporary file with a fresh random key (one chunk in memory at a time)."""

    from apps.lectures import offline_crypto

    key = os.urandom(offline_crypto.KEY_SIZE)
    key_id = uuid.uuid4()
    cipher = tempfile.TemporaryFile()
    try:
        info = offline_crypto.encrypt_stream(
            source, cipher, key=key, key_id=key_id, plaintext_size=plaintext_size, chunk_size=chunk_size
        )
        cipher.seek(0)
    except BaseException:
        cipher.close()
        raise
    return EncryptedPackage(cipher=cipher, key_id=key_id, wrapped_key=wrap_key(key), info=info)


# -- payloads -----------------------------------------------------------------------------------------------------------


def license_state(license_) -> str:
    if license_.revoked_at:
        return "revoked"
    return "expired" if license_.expires_at <= timezone.now() else "active"


def summary_fields(license_, blob) -> dict[str, Any]:
    return {
        "license_id": str(license_.public_id),
        "key_id": str(blob.key_id),
        "package_version": blob.version,
        "issued_at": license_.issued_at,
        "expires_at": license_.expires_at,
        "state": license_state(license_),
        "revoked_at": license_.revoked_at,
        "revoke_reason": license_.revoke_reason,
        "file_size": blob.package_size,
        "sha256_of_ciphertext": blob.package_sha256,
    }


def payload_fields(blob, package_url: str) -> dict[str, Any]:
    """Key and format parameters appended to a summary on issue/renew."""

    from apps.lectures import offline_crypto

    return {
        "content_key": base64.b64encode(unwrap_key(blob.wrapped_key)).decode("ascii"),
        "algorithm": offline_crypto.ALGORITHM_NAME,
        "format_version": offline_crypto.FORMAT_VERSION,
        "header_size": offline_crypto.HEADER_SIZE,
        "chunk_size": blob.chunk_size,
        "tag_size": offline_crypto.TAG_SIZE,
        "plaintext_size": blob.plaintext_size,
        "nonce_scheme": offline_crypto.NONCE_SCHEME,
        "aad_scheme": offline_crypto.AAD_SCHEME,
        "package_url": package_url,
        "content_type": "application/pdf",
    }


# -- streaming ----------------------------------------------------------------------------------------------------------

RANGE_CHUNK_SIZE = 64 * 1024


def parse_single_range(range_header: str, file_size: int) -> tuple[int, int] | None:
    """Parse one RFC 7233 byte range without accepting multi-range responses."""

    if not range_header:
        return None
    if not range_header.startswith("bytes=") or "," in range_header:
        raise ValueError("Unsupported range")
    start_text, separator, end_text = range_header.removeprefix("bytes=").partition("-")
    if not separator or (not start_text and not end_text):
        raise ValueError("Invalid range")
    try:
        if start_text:
            start = int(start_text)
            end = int(end_text) if end_text else file_size - 1
        else:
            suffix_length = int(end_text)
            if suffix_length <= 0:
                raise ValueError("Invalid suffix range")
            start = max(file_size - suffix_length, 0)
            end = file_size - 1
    except ValueError as exc:
        raise ValueError("Invalid range") from exc
    if start < 0 or start >= file_size or end < start:
        raise ValueError("Unsatisfiable range")
    return start, min(end, file_size - 1)


def range_chunks(file_handle, length: int):
    try:
        remaining = length
        while remaining:
            chunk = file_handle.read(min(RANGE_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        file_handle.close()


def no_store(response):
    response["Cache-Control"] = "private, no-store, max-age=0"
    response["Pragma"] = "no-cache"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def package_response(request, blob, filename: str):
    """ETag / If-None-Match / Range / If-Range streaming of the ciphertext package of ``blob``."""

    etag = '"' + blob.package_sha256 + '"'
    size = blob.package_size
    if request.headers.get("If-None-Match", "") == etag:
        return no_store(HttpResponse(status=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag}))
    range_header = request.headers.get("Range", "")
    if_range = request.headers.get("If-Range", "")
    if if_range and if_range != etag:
        range_header = ""  # the validator no longer matches: send the whole (new) representation
    try:
        byte_range = parse_single_range(range_header, size)
    except ValueError:
        unsatisfiable = HttpResponse(status=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE)
        unsatisfiable["Content-Range"] = f"bytes */{size}"
        return no_store(unsatisfiable)
    handle = blob.package.open("rb")
    response: HttpResponse | FileResponse | StreamingHttpResponse
    if byte_range is None:
        response = FileResponse(handle, content_type="application/octet-stream")
        response["Content-Length"] = str(size)
    else:
        start, end = byte_range
        handle.seek(start)
        response = StreamingHttpResponse(
            range_chunks(handle, end - start + 1), status=206, content_type="application/octet-stream"
        )
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
        response["Content-Length"] = str(end - start + 1)
    response["Accept-Ranges"] = "bytes"
    response["ETag"] = etag
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["X-Package-Key-Id"] = str(blob.key_id)
    return no_store(response)


# -- sync ---------------------------------------------------------------------------------------------------------------


def reconcile_inventory(
    owned: dict[uuid.UUID, Any],
    license_ids: list[str],
    device_hash: str,
    *,
    denial_reason: Callable[..., str | None],
    revoke: Callable[[Any, str], object],
) -> dict[str, list[str]]:
    """``keep`` / ``renew`` / ``purge`` for the app's local inventory (see the lecture docs for the semantics)."""

    keep: list[str] = []
    renew: list[str] = []
    purge: list[str] = []
    for raw in dict.fromkeys(license_ids):
        value = safe_uuid(raw)
        item = owned.get(value) if value else None
        if item is None or item.device_hash != device_hash:
            purge.append(raw)
            continue
        reason = denial_reason(item)
        if reason is None:
            keep.append(raw)
            continue
        if reason == REASON_EXPIRED:
            if denial_reason(item, check_expiry=False) is None:
                renew.append(raw)
                continue
            reason = denial_reason(item, check_expiry=False) or REASON_ACCESS_LOST
        if item.revoked_at is None:
            revoke(item, reason)
        purge.append(raw)
    return {"keep": keep, "renew": renew, "purge": purge}


# -- combined revocation ------------------------------------------------------------------------------------------------


def revoke_all_user_offline_licenses(user, reason: str, *, actor=None, request=None) -> int:
    """Revoke every offline license (lectures AND library files) of ``user``; used by device switch/reset flows."""

    from apps.files import offline_services as file_offline
    from apps.lectures import offline_services as lecture_offline

    return lecture_offline.revoke_user_licenses(user, reason, actor=actor, request=request) + (
        file_offline.revoke_user_licenses(user, reason, actor=actor, request=request)
    )
