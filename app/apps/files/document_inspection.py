from __future__ import annotations

import hashlib
from pathlib import Path

from pypdf import PdfReader
from rest_framework.exceptions import ValidationError


def _restore_position(file_obj, position):
    if position is not None and hasattr(file_obj, "seek"):
        file_obj.seek(position)


def compute_sha256(file_obj) -> str:
    position = file_obj.tell() if hasattr(file_obj, "tell") else None
    digest = hashlib.sha256()
    try:
        if hasattr(file_obj, "seek"):
            file_obj.seek(0)
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    finally:
        _restore_position(file_obj, position)
    return digest.hexdigest()


def detect_pages_count(file_obj, filename: str) -> int:
    extension = Path(filename).suffix.lower()
    if extension in {".png", ".jpg", ".jpeg", ".webp"}:
        return 1
    if extension != ".pdf":
        raise ValidationError({"file": "Only PDF and supported image files can be processed."})
    position = file_obj.tell() if hasattr(file_obj, "tell") else None
    try:
        if hasattr(file_obj, "seek"):
            file_obj.seek(0)
        reader = PdfReader(file_obj, strict=False)
        count = len(reader.pages)
        if count < 1:
            raise ValidationError({"file": "The PDF contains no readable pages."})
        return count
    except ValidationError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ValidationError({"file": "The PDF is damaged, encrypted, or unreadable."}) from exc
    finally:
        _restore_position(file_obj, position)
