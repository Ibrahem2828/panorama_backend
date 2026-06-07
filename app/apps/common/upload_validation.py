from pathlib import PurePath

from django.conf import settings
from rest_framework import serializers


DANGEROUS_EXTENSIONS = {
    "bat",
    "cmd",
    "com",
    "dll",
    "exe",
    "js",
    "msi",
    "ps1",
    "scr",
    "sh",
    "vbs",
}


def _extension(uploaded_file) -> str:
    name = PurePath(getattr(uploaded_file, "name", "") or "").name
    suffix = PurePath(name).suffix.lower().lstrip(".")
    if not name or not suffix:
        raise serializers.ValidationError("Uploaded file must include a safe file extension.")
    if suffix in DANGEROUS_EXTENSIONS:
        raise serializers.ValidationError("This file type is not allowed.")
    return suffix


def _validate_upload(uploaded_file, *, allowed_extensions: list[str], max_size_mb: int) -> None:
    if uploaded_file is None:
        return
    size = getattr(uploaded_file, "size", 0) or 0
    if size <= 0:
        raise serializers.ValidationError("Uploaded file cannot be empty.")
    max_size = max_size_mb * 1024 * 1024
    if size > max_size:
        raise serializers.ValidationError(f"Uploaded file must be {max_size_mb} MB or smaller.")
    extension = _extension(uploaded_file)
    normalized_allowed = {item.lower().lstrip(".") for item in allowed_extensions}
    if extension not in normalized_allowed:
        raise serializers.ValidationError("This file extension is not allowed.")


def validate_image_upload(uploaded_file) -> None:
    _validate_upload(
        uploaded_file,
        allowed_extensions=settings.ALLOWED_IMAGE_EXTENSIONS,
        max_size_mb=settings.MAX_IMAGE_UPLOAD_SIZE_MB,
    )


def validate_document_upload(uploaded_file) -> None:
    _validate_upload(
        uploaded_file,
        allowed_extensions=settings.ALLOWED_DOCUMENT_EXTENSIONS,
        max_size_mb=settings.MAX_DOCUMENT_UPLOAD_SIZE_MB,
    )
