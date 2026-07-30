from __future__ import annotations

import io
from unittest.mock import patch
from urllib.error import HTTPError

import pytest
from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.common.crypto import decrypt_text, encrypt_text
from apps.common.file_validation import validate_document_upload
from apps.files.document_inspection import compute_sha256, detect_pages_count
from apps.notifications.models import DevicePlatform, DeviceToken, Notification
from apps.notifications.services import NotificationService, PushNotificationService
from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.exceptions import ValidationError


def _pdf_bytes() -> bytes:
    from pypdf import PdfWriter

    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()


def _create_user() -> User:
    return User.objects.create_user(
        full_name="Service Test User",
        email="service-test@example.com",
        phone_number="+963900123456",
        password="StrongTestPassword123!",
        role=UserRole.NORMAL_USER,
    )


def test_field_encryption_round_trip_and_invalid_ciphertext_are_handled():
    key = Fernet.generate_key().decode("ascii")
    with override_settings(DEBUG=False, FIELD_ENCRYPTION_KEY=key):
        encrypted = encrypt_text("private channel value")
        assert encrypted != "private channel value"
        assert decrypt_text(encrypted) == "private channel value"
        assert encrypt_text("") == ""
        assert decrypt_text("") == ""
        with pytest.raises(ValueError, match="cannot be decrypted"):
            decrypt_text("not-a-fernet-token")


def test_field_encryption_rejects_missing_key_outside_debug_and_uses_debug_fallback():
    with override_settings(DEBUG=False, FIELD_ENCRYPTION_KEY=""):
        with pytest.raises(ImproperlyConfigured, match="FIELD_ENCRYPTION_KEY is required"):
            encrypt_text("private")

    with override_settings(DEBUG=True, SECRET_KEY="debug-only-secret", FIELD_ENCRYPTION_KEY=""):
        assert decrypt_text(encrypt_text("temporary")) == "temporary"


def test_document_validation_requires_matching_signature_extension_and_nonempty_content():
    valid_pdf = SimpleUploadedFile("proof.pdf", _pdf_bytes(), content_type="application/pdf")
    assert validate_document_upload(valid_pdf) is valid_pdf

    with pytest.raises(ValidationError, match="empty"):
        validate_document_upload(SimpleUploadedFile("empty.pdf", b"", content_type="application/pdf"))
    with pytest.raises(ValidationError, match="extension"):
        validate_document_upload(SimpleUploadedFile("renamed.jpg", _pdf_bytes(), content_type="image/jpeg"))


def test_document_inspection_hash_page_count_and_invalid_pdf_preserve_stream_position():
    payload = _pdf_bytes()
    stream = io.BytesIO(payload)
    stream.seek(7)
    digest = compute_sha256(stream)
    assert len(digest) == 64
    assert stream.tell() == 7

    stream.seek(5)
    assert detect_pages_count(stream, "document.pdf") == 1
    assert stream.tell() == 5
    assert detect_pages_count(io.BytesIO(b"image"), "card.png") == 1
    with pytest.raises(ValidationError, match="Only PDF"):
        detect_pages_count(io.BytesIO(b"plain"), "notes.txt")
    corrupt = io.BytesIO(b"not a PDF")
    corrupt.seek(2)
    with pytest.raises(ValidationError, match="damaged"):
        detect_pages_count(corrupt, "corrupt.pdf")
    assert corrupt.tell() == 2


@pytest.mark.django_db
def test_notification_creation_and_push_delivery_update_only_active_tokens():
    user = _create_user()
    notification = NotificationService.create_notification(user, "Title", "Body", data={"kind": "test"})
    assert Notification.objects.get(pk=notification.pk).data == {"kind": "test"}
    created = NotificationService.create_bulk_notifications([user], "Bulk", "Body")
    assert len(created) == 1

    token = DeviceToken.objects.create(user=user, token="ExponentPushToken[test]", platform=DevicePlatform.ANDROID)
    with patch.object(PushNotificationService, "_send_expo", return_value=1) as send:
        assert PushNotificationService.send_to_user(user, "Push", "Body") is True
    send.assert_called_once()
    token.refresh_from_db()
    assert token.last_used_at is not None


def test_push_provider_rejects_untrusted_endpoint_and_handles_provider_failures():
    with override_settings(EXPO_PUSH_ENDPOINT="http://untrusted.example", EXPO_PUSH_ALLOWED_HOSTS=frozenset({"exp.host"})):
        assert PushNotificationService._send_expo(["ExponentPushToken[test]"], "T", "B") == 0

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    with override_settings(EXPO_PUSH_ENDPOINT="https://exp.host/--/api/v2/push/send", EXPO_PUSH_ALLOWED_HOSTS=frozenset({"exp.host"})):
        with patch("apps.notifications.services.urllib_request.urlopen", return_value=Response()) as urlopen:
            assert PushNotificationService._send_expo(["ExponentPushToken[test]"], "T", "B") == 1
        request = urlopen.call_args.args[0]
        assert request.full_url.startswith("https://exp.host/")

        error = HTTPError("https://exp.host/--/api/v2/push/send", 503, "unavailable", {}, None)
        with patch("apps.notifications.services.urllib_request.urlopen", side_effect=error):
            assert PushNotificationService._send_expo(["ExponentPushToken[test]"], "T", "B") == 0
