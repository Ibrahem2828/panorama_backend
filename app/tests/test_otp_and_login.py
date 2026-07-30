import pytest
from apps.accounts.choices import OTPDeliveryChannel, OTPPurpose, UserRole
from apps.accounts.models import User
from apps.accounts.serializers import LoginSerializer, VerifyOTPSerializer
from apps.accounts.services import OTPService
from django.contrib.auth.hashers import check_password
from django.core import mail
from django.test import override_settings
from rest_framework import serializers


@pytest.mark.django_db
@override_settings(RETURN_DEVELOPMENT_OTP=True)
def test_email_otp_is_hashed_delivered_and_verifies(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    user = User.objects.create_user(
        email="student@example.com",
        phone_number="+963900000001",
        password="A-Strong-Test-Password-123!",
        full_name="Student",
        role=UserRole.STUDENT,
    )
    otp, raw_code = OTPService.send_otp(
        user.email,
        OTPPurpose.VERIFY_EMAIL,
        user=user,
        channel=OTPDeliveryChannel.EMAIL,
    )
    assert raw_code
    assert otp.code_hash != raw_code
    assert check_password(raw_code, otp.code_hash)
    assert len(mail.outbox) == 1
    assert raw_code in mail.outbox[0].body

    serializer = VerifyOTPSerializer(
        data={
            "email": user.email,
            "channel": OTPDeliveryChannel.EMAIL,
            "purpose": OTPPurpose.VERIFY_EMAIL,
            "code": raw_code,
        }
    )
    serializer.is_valid(raise_exception=True)
    serializer.save()
    user.refresh_from_db()
    assert user.is_email_verified is True


@pytest.mark.django_db
def test_login_requires_verified_identity():
    user = User.objects.create_user(
        email="unverified@example.com",
        phone_number="+963900000002",
        password="A-Strong-Test-Password-123!",
        full_name="Unverified",
        role=UserRole.NORMAL_USER,
    )
    serializer = LoginSerializer(data={"identifier": user.email, "password": "A-Strong-Test-Password-123!"})
    with pytest.raises(serializers.ValidationError):
        serializer.is_valid(raise_exception=True)

    user.is_email_verified = True
    user.save(update_fields=["is_email_verified", "updated_at"])
    serializer = LoginSerializer(data={"identifier": user.email, "password": "A-Strong-Test-Password-123!"})
    serializer.is_valid(raise_exception=True)
    assert serializer.validated_data["access"]
    assert serializer.validated_data["refresh"]
