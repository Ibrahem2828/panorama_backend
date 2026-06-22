import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.choices import OTPPurpose, UserRole
from apps.accounts.models import OTPCode, User


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def normal_payload():
    return {
        "full_name": "Hardening User",
        "email": "hardening@example.com",
        "phone_number": "+963900000050",
        "password": "StrongPass123!",
        "password_confirm": "StrongPass123!",
    }


@pytest.fixture
def normal_user(db):
    return User.objects.create_user(
        full_name="Hardening Existing",
        email="hardening-existing@example.com",
        phone_number="+963900000051",
        password="StrongPass123!",
        role=UserRole.NORMAL_USER,
        is_phone_verified=False,
    )


@pytest.mark.django_db
def test_register_returns_phone_verification_flags(api_client, normal_payload):
    response = api_client.post(reverse("register-normal"), normal_payload, format="json")

    assert response.status_code == status.HTTP_201_CREATED
    data = response.data["data"]
    assert data["requires_otp"] is True
    assert data["requires_phone_verification"] is True
    assert data["phone_verified"] is False
    assert data["expires_in_seconds"] == 600
    assert data["resend_after_seconds"] == 60
    assert data["user"]["requires_phone_verification"] is True
    assert data["user"]["phone_verified"] is False


@pytest.mark.django_db
def test_register_does_not_return_development_otp_when_disabled(api_client, normal_payload, settings):
    settings.RETURN_DEVELOPMENT_OTP = False
    response = api_client.post(reverse("register-normal"), normal_payload, format="json")

    assert response.status_code == status.HTTP_201_CREATED
    assert "development_otp" not in response.data["data"]


@pytest.mark.django_db
def test_login_before_phone_verify_remains_allowed(api_client, normal_user):
    response = api_client.post(
        reverse("login"),
        {"identifier": normal_user.email, "password": "StrongPass123!"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data["data"]["user"]["requires_phone_verification"] is True
    assert response.data["data"]["user"]["phone_verified"] is False


@pytest.mark.django_db
def test_me_exposes_phone_verification_flags(api_client, normal_user):
    api_client.force_authenticate(normal_user)
    response = api_client.get(reverse("current-user"))

    assert response.status_code == status.HTTP_200_OK
    assert response.data["data"]["requires_phone_verification"] is True
    assert response.data["data"]["phone_verified"] is False


@pytest.mark.django_db
def test_verify_phone_updates_flags(api_client, normal_user):
    otp_response = api_client.post(
        reverse("otp-send"),
        {"phone_number": normal_user.phone_number, "purpose": OTPPurpose.VERIFY_PHONE},
        format="json",
    )
    code = otp_response.data["data"]["development_otp"]

    verify_response = api_client.post(
        reverse("verify-phone"),
        {"phone_number": normal_user.phone_number, "code": code},
        format="json",
    )
    assert verify_response.status_code == status.HTTP_200_OK
    assert verify_response.data["data"]["phone_verified"] is True
    assert verify_response.data["data"]["requires_phone_verification"] is False

    normal_user.refresh_from_db()
    api_client.force_authenticate(normal_user)
    me_response = api_client.get(reverse("current-user"))
    assert me_response.data["data"]["requires_phone_verification"] is False
    assert me_response.data["data"]["phone_verified"] is True


@pytest.mark.django_db
def test_normal_otp_wrong_code_fails(api_client, normal_user):
    api_client.post(
        reverse("otp-send"),
        {"phone_number": normal_user.phone_number, "purpose": OTPPurpose.VERIFY_PHONE},
        format="json",
    )
    response = api_client.post(
        reverse("otp-verify"),
        {"phone_number": normal_user.phone_number, "purpose": OTPPurpose.VERIFY_PHONE, "code": "000000"},
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_normal_otp_expired_fails(api_client, normal_user):
    send_response = api_client.post(
        reverse("otp-send"),
        {"phone_number": normal_user.phone_number, "purpose": OTPPurpose.VERIFY_PHONE},
        format="json",
    )
    code = send_response.data["data"]["development_otp"]
    otp = OTPCode.objects.filter(phone_number=normal_user.phone_number, purpose=OTPPurpose.VERIFY_PHONE).latest("created_at")
    otp.expires_at = timezone.now() - timezone.timedelta(minutes=1)
    otp.save(update_fields=["expires_at", "updated_at"])

    response = api_client.post(
        reverse("otp-verify"),
        {"phone_number": normal_user.phone_number, "purpose": OTPPurpose.VERIFY_PHONE, "code": code},
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_normal_otp_max_attempts_fails(api_client, normal_user, settings):
    api_client.post(
        reverse("otp-send"),
        {"phone_number": normal_user.phone_number, "purpose": OTPPurpose.VERIFY_PHONE},
        format="json",
    )
    for _ in range(settings.MAX_OTP_VERIFY_ATTEMPTS):
        api_client.post(
            reverse("otp-verify"),
            {"phone_number": normal_user.phone_number, "purpose": OTPPurpose.VERIFY_PHONE, "code": "000000"},
            format="json",
        )

    response = api_client.post(
        reverse("otp-verify"),
        {"phone_number": normal_user.phone_number, "purpose": OTPPurpose.VERIFY_PHONE, "code": "000000"},
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Maximum OTP verification attempts exceeded" in str(response.data)