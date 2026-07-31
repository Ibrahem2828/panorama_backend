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


def _read_head(uploaded_file, size: int = 4096) -> bytes:
    current = uploaded_file.tell() if hasattr(uploaded_file, "tell") else None
    try:
        if hasattr(uploaded_file, "seek"):
            uploaded_file.seek(0)
        return uploaded_file.read(size)
    finally:
        if current is not None and hasattr(uploaded_file, "seek"):
            uploaded_file.seek(current)


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
    kind = filetype.guess(head)
    detected_mime = kind.mime if kind else None

    supplied_mime = str(getattr(uploaded_file, "content_type", "") or "").lower()
    effective_mime = detected_mime or supplied_mime
    if effective_mime not in policy.allowed_mime_types:
        raise ValidationError({field_name: "The file content does not match an allowed type."})

    expected_extensions = {
        "application/pdf": {"pdf"},
        "image/png": {"png"},
        "image/jpeg": {"jpg", "jpeg"},
        "image/webp": {"webp"},
    }
    if extension not in expected_extensions.get(effective_mime, {extension}):
        raise ValidationError({field_name: "The file extension does not match its content."})
    return uploaded_file


def validate_document_upload(uploaded_file, field_name: str = "file"):
    return validate_uploaded_file(uploaded_file, DOCUMENT_POLICY, field_name)


def validate_image_upload(uploaded_file, field_name: str = "image"):
    return validate_uploaded_file(uploaded_file, IMAGE_POLICY, field_name)
