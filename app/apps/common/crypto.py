from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _fernet() -> Fernet:
    key = getattr(settings, "FIELD_ENCRYPTION_KEY", "")
    if not key and settings.DEBUG:
        digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
        key = base64.urlsafe_b64encode(digest).decode("ascii")
    if not key:
        raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY is required for encrypted external channel data.")
    try:
        return Fernet(key.encode("ascii") if isinstance(key, str) else key)
    except Exception as exc:  # noqa: BLE001
        raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY must be a valid Fernet key.") from exc


def encrypt_text(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_text(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Encrypted value cannot be decrypted with the configured key.") from exc
