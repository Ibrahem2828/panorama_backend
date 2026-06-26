import pytest
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from apps.accounts.choices import OTPPurpose, UserRole
from apps.accounts.models import OTPCode, User
from apps.accounts.phone_numbers import normalize_phone_number


@pytest.fixture
def api_client():
    return APIClient()


def normal_payload(**overrides):
    payload = {
        "full_name": "إبراهيم محمد خير سعد الدين",
        "email": "ibrahemsa28@example.com",
        "phone_number": "0994109259",
        "password": "StrongPass123!",
        "password_confirm": "StrongPass123!",
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0994109259", "+963994109259"),
        ("963994109259", "+963994109259"),
        ("+963994109259", "+963994109259"),
        ("٠٩٩٤١٠٩٢٥٩", "+963994109259"),
    ],
)
def test_phone_normalization_accepts_syrian_mobile_formats(value, expected):
    assert normalize_phone_number(value) == expected


@pytest.mark.django_db
def test_normal_register_accepts_local_phone_and_returns_normalized_phone(api_client, settings):
    settings.RETURN_DEVELOPMENT_OTP = False

    response = api_client.post("/api/v1/auth/register/normal/", normal_payload(), format="json")

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["data"]["phone_number"] == "+963994109259"
    assert response.data["data"]["user"]["phone_number"] == "+963994109259"
    assert response.data["data"]["requires_otp"] is True
    assert response.data["data"]["requires_phone_verification"] is True
    assert "development_otp" not in response.data["data"]
    user = User.objects.get(email="ibrahemsa28@example.com")
    assert user.phone_number == "+963994109259"
    assert OTPCode.objects.filter(user=user, phone_number="+963994109259", purpose=OTPPurpose.VERIFY_PHONE).exists()


@pytest.mark.django_db
def test_invalid_phone_returns_arabic_structured_error(api_client):
    response = api_client.post(
        "/api/v1/auth/register/normal/",
        normal_payload(phone_number="994109259"),
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["message"] == "تحقق من البيانات المدخلة وحاول مرة أخرى."
    assert response.data["data"]["error_code"] == "invalid_phone"
    assert response.data["data"]["expected_phone_format"] == "E.164"
    assert "phone_number" in response.data["errors"]


@pytest.mark.django_db
def test_duplicate_phone_uses_normalized_value(api_client):
    User.objects.create_user(
        full_name="Existing",
        email="existing@example.com",
        phone_number="+963994109259",
        password="StrongPass123!",
        role=UserRole.NORMAL_USER,
    )

    response = api_client.post(
        "/api/v1/auth/register/normal/",
        normal_payload(email="new@example.com", phone_number="0994109259"),
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["message"] == "رقم الجوال مستخدم مسبقاً."
    assert response.data["data"]["error_code"] == "duplicate_phone"
    assert response.data["errors"]["phone_number"][0] == "رقم الجوال مستخدم مسبقاً."


@pytest.mark.django_db
def test_duplicate_email_returns_arabic_structured_error(api_client):
    User.objects.create_user(
        full_name="Existing",
        email="ibrahemsa28@example.com",
        phone_number="+963900000111",
        password="StrongPass123!",
        role=UserRole.NORMAL_USER,
    )

    response = api_client.post(
        "/api/v1/auth/register/normal/",
        normal_payload(phone_number="+963994109259"),
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["message"] == "البريد الإلكتروني مستخدم مسبقاً."
    assert response.data["data"]["error_code"] == "duplicate_email"


@pytest.mark.django_db
def test_verify_phone_accepts_local_phone_after_normalized_registration(api_client):
    register = api_client.post("/api/v1/auth/register/normal/", normal_payload(), format="json")
    code = register.data["data"]["development_otp"]

    response = api_client.post(
        "/api/v1/auth/verify-phone/",
        {"phone_number": "0994109259", "code": code},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data["data"]["phone_verified"] is True
    assert response.data["data"]["next_step"] == "login"


@pytest.mark.django_db
def test_normal_register_rate_limit_is_seven_attempts_per_twenty_minutes(api_client, monkeypatch):
    cache.clear()
    monkeypatch.setitem(ScopedRateThrottle.THROTTLE_RATES, "normal_register", "7/20min")
    payload = normal_payload()

    responses = [
        api_client.post("/api/v1/auth/register/normal/", payload, format="json")
        for _ in range(8)
    ]

    assert [response.status_code for response in responses[:7]] == [
        status.HTTP_201_CREATED,
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_400_BAD_REQUEST,
    ]
    throttled = responses[7]
    assert throttled.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert throttled.data["message"].startswith("تم تجاوز عدد المحاولات المسموح.")
    assert throttled.data["data"]["error_code"] == "rate_limited"
    assert 0 < throttled.data["data"]["retry_after_seconds"] <= 1200
    assert throttled.data["data"]["retry_after_minutes"] <= 20
    assert "Retry-After" in throttled
    assert "Request was throttled" not in str(throttled.data)
