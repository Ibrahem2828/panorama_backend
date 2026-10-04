from datetime import timedelta
from unittest import mock

import pytest
from cryptography.fernet import Fernet
from django.core import mail
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import tasks
from apps.accounts.choices import OTPPurpose
from apps.accounts.models import OTPCode, User
from apps.accounts.services import OTPService
from apps.common.crypto import encrypt_text


@pytest.fixture
def api_client():
    cache.clear()  # reset DRF throttle counters shared across tests
    return APIClient()


@pytest.fixture
def normal_user(db):
    return User.objects.create_user(
        full_name="Ahmad Ali",
        email="ahmad@example.com",
        phone_number="+963900000000",
        password="StrongPass123!",
    )


@pytest.fixture
def async_otp(settings):
    settings.OTP_EMAIL_ASYNC = True
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.EMAIL_HOST_PASSWORD = "unused-with-locmem"
    settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")


def _send(api_client, email):
    return api_client.post(reverse("otp-send"), {"email": email, "purpose": OTPPurpose.VERIFY_EMAIL}, format="json")


@pytest.mark.django_db
def test_otp_email_is_sent_by_the_task_after_commit(
    api_client, normal_user, async_otp, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        response = _send(api_client, normal_user.email)

    assert response.status_code == 200
    code = response.data["data"]["development_otp"]
    assert len(mail.outbox) == 1
    assert code in mail.outbox[0].body
    assert mail.outbox[0].to == [normal_user.email]


@pytest.mark.django_db
def test_nothing_is_sent_inside_the_request_before_commit(api_client, normal_user, async_otp):
    # Without running on_commit callbacks the request itself must not deliver mail.
    _send(api_client, normal_user.email)
    assert mail.outbox == []


@pytest.mark.django_db
def test_enqueue_failure_falls_back_to_inline_delivery(
    api_client, normal_user, async_otp, django_capture_on_commit_callbacks
):
    with mock.patch.object(tasks.send_otp_email, "apply_async", side_effect=ConnectionError("redis down")):
        with django_capture_on_commit_callbacks(execute=True):
            response = _send(api_client, normal_user.email)

    assert response.status_code == 200
    assert len(mail.outbox) == 1


@pytest.mark.django_db
def test_stale_or_used_codes_are_not_sent(normal_user, async_otp):
    otp, _ = OTPService.send_otp(normal_user.email, OTPPurpose.VERIFY_EMAIL, user=normal_user)
    mail.outbox.clear()

    OTPCode.objects.filter(pk=otp.pk).update(is_used=True)
    tasks.send_otp_email.apply(args=(otp.pk, encrypt_text("123456")))
    assert mail.outbox == []

    OTPCode.objects.filter(pk=otp.pk).update(is_used=False, expires_at=timezone.now() - timedelta(seconds=1))
    tasks.send_otp_email.apply(args=(otp.pk, encrypt_text("123456")))
    assert mail.outbox == []


@pytest.mark.django_db
def test_task_retries_transient_mail_errors(normal_user, async_otp):
    otp, _ = OTPService.send_otp(normal_user.email, OTPPurpose.VERIFY_EMAIL, user=normal_user)
    mail.outbox.clear()

    assert OSError in tasks.send_otp_email.autoretry_for
    assert tasks.send_otp_email.max_retries >= 3
    with mock.patch.object(OTPService, "_deliver_email", side_effect=[OSError("smtp timeout"), None]) as deliver:
        result = tasks.send_otp_email.apply(args=(otp.pk, encrypt_text("123456")))
    assert deliver.call_count == 2
    assert result.successful()
