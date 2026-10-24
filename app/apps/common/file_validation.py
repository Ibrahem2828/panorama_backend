from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import filetype
from django.conf import settings
from rest_framework.exceptions import ValidationError


@dataclass(frozen=True)
class UploadPolicy:
    allowed_extensions: frozenset[str]
    allowed_mime_types: frozenset[str]
    max_size_bytes: int


DOCUMENT_POLICY = UploadPolicy(
    allowed_extensions=frozenset({"pdf", "png", "jpg", "jpeg", "webp"}),
    allowed_mime_types=frozenset({"application/pdf", "image/png", "image/jpeg", "image/webp"}),
    max_size_bytes=getattr(settings, "MAX_DOCUMENT_UPLOAD_SIZE", 25 * 1024 * 1024),
)

IMAGE_POLICY = UploadPolicy(
    allowed_extensions=frozenset({"png", "jpg", "jpeg", "webp"}),
    allowed_mime_types=frozenset({"image/png", "image/jpeg", "image/webp"}),
    max_size_bytes=getattr(settings, "MAX_IMAGE_UPLOAD_SIZE", 8 * 1024 * 1024),
)

LECTURE_DOCUMENT_POLICY = UploadPolicy(
    allowed_extensions=frozenset({"pdf", "doc", "docx", "ppt", "pptx"}),
    allowed_mime_types=frozenset(
        {
            "application/pdf",
            "application/msword",
            "application/vnd.ms-powerpoint",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/zip",
            "application/x-ole-storage",
        }
    ),
    max_size_bytes=getattr(settings, "LECTURE_MAX_UPLOAD_SIZE", 50 * 1024 * 1024),
)

#: Library files: PDFs, images and Office documents (everything becomes protected page images for the viewer).
LIBRARY_DOCUMENT_POLICY = UploadPolicy(
    allowed_extensions=DOCUMENT_POLICY.allowed_extensions | LECTURE_DOCUMENT_POLICY.allowed_extensions,
    allowed_mime_types=DOCUMENT_POLICY.allowed_mime_types | LECTURE_DOCUMENT_POLICY.allowed_mime_types,
    max_size_bytes=LECTURE_DOCUMENT_POLICY.max_size_bytes,
)

_OLE_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
_ZIP_SIGNATURE = bytes.fromhex("504B0304")
_PNG_SIGNATURE = bytes.fromhex("89504E470D0A1A0A")
_OFFICE_OPENXML = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
_ZIP_MIMES = frozenset({"application/zip", "application/x-zip-compressed", "application/octet-stream"})
# Generic "unknown" values browsers send when they cannot classify a file. They carry no information, so they
# are ignored for the consistency check; any other supplied type must agree with the detected content.
_UNINFORMATIVE_MIMES = frozenset({"", "application/octet-stream", "binary/octet-stream"})

# Canonical content type -> extensions it may carry and the client-supplied types considered compatible.
_FORMATS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "application/pdf": (frozenset({"pdf"}), frozenset({"application/pdf", "application/x-pdf"})),
    "image/png": (frozenset({"png"}), frozenset({"image/png"})),
    "image/jpeg": (frozenset({"jpg", "jpeg"}), frozenset({"image/jpeg", "image/jpg", "image/pjpeg"})),
    "image/webp": (frozenset({"webp"}), frozenset({"image/webp"})),
    "application/msword": (frozenset({"doc"}), frozenset({"application/msword", "application/x-ole-storage"})),
    "application/vnd.ms-powerpoint": (
        frozenset({"ppt"}),
        frozenset({"application/vnd.ms-powerpoint", "application/x-ole-storage"}),
    ),
    _OFFICE_OPENXML["docx"]: (frozenset({"docx"}), frozenset({_OFFICE_OPENXML["docx"]}) | _ZIP_MIMES),
    _OFFICE_OPENXML["pptx"]: (frozenset({"pptx"}), frozenset({_OFFICE_OPENXML["pptx"]}) | _ZIP_MIMES),
    "text/plain": (frozenset({"txt", "md"}), frozenset({"text/plain", "text/markdown"})),
    "text/csv": (frozenset({"csv"}), frozenset({"text/csv", "text/plain", "application/csv"})),
}
_TEXT_MIMES = frozenset({"text/plain", "text/csv"})
_TEXT_EXTENSIONS = {"txt": "text/plain", "md": "text/plain", "csv": "text/csv"}
# C0 control characters other than TAB, LF, CR (and FF) never appear in genuine text documents.
_BINARY_CONTROL_BYTES = frozenset(set(range(0x00, 0x20)) - {0x09, 0x0A, 0x0C, 0x0D}) | {0x7F}


def _read_head(uploaded_file, size: int = 4096) -> bytes:
    current = uploaded_file.tell() if hasattr(uploaded_file, "tell") else None
    try:
        if hasattr(uploaded_file, "seek"):
            uploaded_file.seek(0)
        return uploaded_file.read(size)
    finally:
        if current is not None and hasattr(uploaded_file, "seek"):
            uploaded_file.seek(current)


def is_strict_text(head: bytes) -> bool:
    """Strict plain-text check: valid UTF-8, no NUL and no binary control characters."""
    if not head:
        return False
    if any(byte in _BINARY_CONTROL_BYTES for byte in head):
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        # The head window may cut a multi-byte sequence in half; tolerate only that case.
        if exc.reason != "unexpected end of data" or exc.end != len(head):
            return False
    return True


def detect_content_type(head: bytes, extension: str) -> str | None:
    """Detect the canonical content type from the file bytes only (never from client metadata).

    Returns ``None`` when the content is not a recognised format; callers must treat that as a rejection.
    """
    # Strict per-format magic checks. A PDF must begin with its signature, which rejects HTML/script polyglots
    # that merely embed ``%PDF-`` later in the file.
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(_PNG_SIGNATURE):
        return "image/png"
    if head[:3] == bytes((0xFF, 0xD8, 0xFF)):
        return "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:4] == _ZIP_SIGNATURE:
        if extension in _OFFICE_OPENXML:
            return _OFFICE_OPENXML[extension]
        kind = filetype.guess(head)
        return kind.mime if kind else "application/zip"
    if head.startswith(_OLE_SIGNATURE):
        if extension == "ppt":
            return "application/vnd.ms-powerpoint"
        if extension == "doc":
            return "application/msword"
        kind = filetype.guess(head)
        return kind.mime if kind else "application/x-ole-storage"
    if extension in _TEXT_EXTENSIONS and is_strict_text(head):
        return _TEXT_EXTENSIONS[extension]
    return None


def validate_uploaded_file(uploaded_file, policy: UploadPolicy, field_name: str = "file"):
    if not uploaded_file:
        return uploaded_file

    size = int(getattr(uploaded_file, "size", 0) or 0)
    if size <= 0:
        raise ValidationError({field_name: "The uploaded file is empty."})
    if size > policy.max_size_bytes:
        raise ValidationError({field_name: f"The uploaded file exceeds {policy.max_size_bytes // (1024 * 1024)} MB."})

    extension = Path(getattr(uploaded_file, "name", "")).suffix.lower().lstrip(".")
    if extension not in policy.allowed_extensions:
        raise ValidationError({field_name: "Unsupported file extension."})

    head = _read_head(uploaded_file)
    detected_mime = detect_content_type(head, extension)
    # Content detection is authoritative: the client-supplied type is never a substitute for it.
    if detected_mime is None:
        raise ValidationError({field_name: "The file content does not match an allowed type."})
    if detected_mime not in policy.allowed_mime_types:
        raise ValidationError({field_name: "The file content does not match an allowed type."})

    allowed_extensions, compatible_supplied = _FORMATS.get(detected_mime, (frozenset(), frozenset()))
    if extension not in allowed_extensions:
        raise ValidationError({field_name: "The file extension does not match its content."})

    supplied_mime = str(getattr(uploaded_file, "content_type", "") or "").split(";")[0].strip().lower()
    if supplied_mime not in _UNINFORMATIVE_MIMES and supplied_mime not in compatible_supplied:
        raise ValidationError({field_name: "The declared file type does not match its content."})
    return uploaded_file


def validate_library_upload(uploaded_file, field_name: str = "file"):
    return validate_uploaded_file(uploaded_file, LIBRARY_DOCUMENT_POLICY, field_name)


def validate_document_upload(uploaded_file, field_name: str = "file"):
    return validate_uploaded_file(uploaded_file, DOCUMENT_POLICY, field_name)


def validate_image_upload(uploaded_file, field_name: str = "image"):
    return validate_uploaded_file(uploaded_file, IMAGE_POLICY, field_name)
