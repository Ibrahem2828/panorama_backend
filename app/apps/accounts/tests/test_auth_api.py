import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.choices import OTPPurpose, UserRole
from apps.accounts.models import OTPCode, StudentProfile, User


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def normal_payload():
    return {
        "full_name": "Ahmad Ali",
        "email": "ahmad@example.com",
        "phone_number": "+963900000000",
        "password": "StrongPass123!",
        "password_confirm": "StrongPass123!",
    }


@pytest.fixture
def normal_user(db):
    return User.objects.create_user(
        full_name="Ahmad Ali",
        email="ahmad@example.com",
        phone_number="+963900000000",
        password="StrongPass123!",
        role=UserRole.NORMAL_USER,
    )


def register_normal(api_client, payload):
    return api_client.post(reverse("register-normal"), payload, format="json")


@pytest.mark.django_db
def test_normal_user_registration(api_client, normal_payload):
    response = register_normal(api_client, normal_payload)

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["success"] is True
    assert response.data["data"]["requires_otp"] is True
    assert response.data["data"]["next_step"] == "verify_phone"
    assert response.data["data"]["requires_phone_verification"] is True
    assert response.data["data"]["phone_verified"] is False
    assert response.data["data"]["expires_in_seconds"] == 600
    user = User.objects.get(email=normal_payload["email"])
    assert user.role == UserRole.NORMAL_USER
    assert OTPCode.objects.filter(user=user, purpose=OTPPurpose.VERIFY_PHONE).exists()


@pytest.mark.django_db
def test_student_registration_creates_student_profile(api_client):
    payload = {
        "full_name": "Student Name",
        "email": "student@example.com",
        "phone_number": "+963900000001",
        "password": "StrongPass123!",
        "password_confirm": "StrongPass123!",
        "student_number": "20201234",
    }

    response = api_client.post(reverse("register-student"), payload, format="json")

    assert response.status_code == status.HTTP_201_CREATED
    user = User.objects.get(email=payload["email"])
    assert user.role == UserRole.STUDENT
    assert StudentProfile.objects.filter(user=user, student_number="20201234").exists()


@pytest.mark.django_db
def test_duplicate_email_rejection(api_client, normal_payload, normal_user):
    response = register_normal(api_client, normal_payload)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "email" in response.data["errors"]


@pytest.mark.django_db
def test_duplicate_phone_rejection(api_client, normal_payload, normal_user):
    normal_payload["email"] = "new@example.com"
    response = register_normal(api_client, normal_payload)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "phone_number" in response.data["errors"]


@pytest.mark.django_db
def test_login_with_email(api_client, normal_user):
    response = api_client.post(
        reverse("login"),
        {"identifier": normal_user.email, "password": "StrongPass123!"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert "access" in response.data["data"]
    assert response.data["data"]["user"]["role"] == UserRole.NORMAL_USER


@pytest.mark.django_db
def test_login_with_phone(api_client, normal_user):
    response = api_client.post(
        reverse("login"),
        {"identifier": normal_user.phone_number, "password": "StrongPass123!"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert "refresh" in response.data["data"]


@pytest.mark.django_db
def test_wrong_password_login_failure(api_client, normal_user):
    response = api_client.post(
        reverse("login"),
        {"identifier": normal_user.email, "password": "WrongPass123!"},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["success"] is False


@pytest.mark.django_db
def test_get_current_user(api_client, normal_user):
    api_client.force_authenticate(user=normal_user)
    response = api_client.get(reverse("current-user"))

    assert response.status_code == status.HTTP_200_OK
    assert response.data["data"]["email"] == normal_user.email


@pytest.mark.django_db
def test_change_password(api_client, normal_user):
    api_client.force_authenticate(user=normal_user)
    response = api_client.post(
        reverse("change-password"),
        {
            "old_password": "StrongPass123!",
            "new_password": "NewStrongPass123!",
            "new_password_confirm": "NewStrongPass123!",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    normal_user.refresh_from_db()
    assert normal_user.check_password("NewStrongPass123!")


@pytest.mark.django_db
def test_otp_send(api_client, normal_user):
    response = api_client.post(
        reverse("otp-send"),
        {"phone_number": normal_user.phone_number, "purpose": OTPPurpose.VERIFY_PHONE},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert "development_otp" in response.data["data"]


@pytest.mark.django_db
def test_otp_verify(api_client, normal_user):
    response = api_client.post(
        reverse("otp-send"),
        {"phone_number": normal_user.phone_number, "purpose": OTPPurpose.VERIFY_PHONE},
        format="json",
    )
    code = response.data["data"]["development_otp"]

    response = api_client.post(
        reverse("otp-verify"),
        {"phone_number": normal_user.phone_number, "purpose": OTPPurpose.VERIFY_PHONE, "code": code},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data["data"]["phone_verified"] is True
    assert response.data["data"]["requires_phone_verification"] is False
    normal_user.refresh_from_db()
    assert normal_user.is_phone_verified is True


@pytest.mark.django_db
def test_password_reset_flow(api_client, normal_user):
    response = api_client.post(
        reverse("request-password-reset"),
        {"phone_number": normal_user.phone_number},
        format="json",
    )
    code = response.data["data"]["development_otp"]

    response = api_client.post(
        reverse("confirm-password-reset"),
        {
            "phone_number": normal_user.phone_number,
            "code": code,
            "new_password": "ResetStrongPass123!",
            "new_password_confirm": "ResetStrongPass123!",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    normal_user.refresh_from_db()
    assert normal_user.check_password("ResetStrongPass123!")


@pytest.mark.django_db
def test_role_is_assigned_correctly(api_client, normal_payload):
    response = register_normal(api_client, normal_payload)

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["data"]["user"]["role"] == UserRole.NORMAL_USER


@pytest.mark.django_db
def test_student_profile_is_created_for_student_user(api_client):
    payload = {
        "full_name": "Student Name",
        "email": "student2@example.com",
        "phone_number": "+963900000002",
        "password": "StrongPass123!",
        "password_confirm": "StrongPass123!",
    }

    response = api_client.post(reverse("register-student"), payload, format="json")

    assert response.status_code == status.HTTP_201_CREATED
    user = User.objects.get(email=payload["email"])
    assert hasattr(user, "student_profile")
    assert user.student_profile.verification_status == "incomplete"
