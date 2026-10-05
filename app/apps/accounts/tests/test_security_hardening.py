from __future__ import annotations

import pytest
from django.core import mail
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.choices import OTPPurpose, UserRole
from apps.accounts.models import OTPCode, User, UserPermissionOverride
from apps.accounts.services import OTPService

PASSWORD = "StrongPass123!"


def make_user(role, n, **extra):
    return User.objects.create_user(
        full_name=f"User {n}",
        email=f"user{n}@example.test",
        phone_number=f"+96399200{n:04d}",
        password=PASSWORD,
        role=role,
        is_email_verified=True,
        **extra,
    )


def client_for(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture(autouse=True)
def _fresh_throttles():
    cache.clear()


@pytest.fixture
def it_support(db):
    return make_user(UserRole.IT_SUPPORT, 1)


@pytest.fixture
def admin(db):
    return make_user(UserRole.ADMIN, 2)


@pytest.fixture
def other_admin(db):
    return make_user(UserRole.ADMIN, 3)


@pytest.fixture
def student(db):
    return make_user(UserRole.NORMAL_USER, 4)


def overrides_url(user):
    return reverse("dashboard-user-permission-overrides", args=[user.pk])


@pytest.mark.django_db
def test_admin_cannot_grant_themselves_system_manage(admin):
    response = client_for(admin).put(
        overrides_url(admin), {"permission_code": "system.manage", "effect": "allow"}, format="json"
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert not UserPermissionOverride.objects.filter(user=admin).exists()


@pytest.mark.django_db
def test_admin_cannot_strip_it_support_capabilities(admin, it_support):
    response = client_for(admin).put(
        overrides_url(it_support), {"permission_code": "users.manage", "effect": "deny"}, format="json"
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert not UserPermissionOverride.objects.filter(user=it_support).exists()


@pytest.mark.django_db
def test_admin_cannot_hand_out_system_manage_to_a_student(admin, student):
    response = client_for(admin).put(
        overrides_url(student), {"permission_code": "system.manage", "effect": "allow"}, format="json"
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_admin_can_still_manage_lower_roles_with_capabilities_they_hold(admin, student):
    response = client_for(admin).put(
        overrides_url(student), {"permission_code": "users.manage", "effect": "allow"}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_admin_cannot_deactivate_or_demote_it_support_or_peer_admins(admin, it_support, other_admin):
    # a second active IT Support exists, so the old "keep one" guard would not have blocked this
    make_user(UserRole.IT_SUPPORT, 9)
    client = client_for(admin)
    for target in (it_support, other_admin):
        deactivate = client.post(
            reverse("dashboard-users-deactivate", args=[target.pk]),
            {"reason": "test"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=f"k-{target.pk}",
        )
        assert deactivate.status_code == status.HTTP_403_FORBIDDEN
        patch = client.patch(reverse("dashboard-users-detail", args=[target.pk]), {"role": "student"}, format="json")
        assert patch.status_code == status.HTTP_403_FORBIDDEN
        target.refresh_from_db()
        assert target.is_active and target.role != UserRole.NORMAL_USER


@pytest.mark.django_db
def test_admin_cannot_promote_someone_to_admin(admin, student):
    response = client_for(admin).patch(
        reverse("dashboard-users-detail", args=[student.pk]), {"role": "admin"}, format="json"
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_it_support_keeps_full_control(it_support, admin):
    response = client_for(it_support).put(
        overrides_url(admin), {"permission_code": "system.manage", "effect": "allow"}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_wrong_otp_guesses_are_counted_and_lock_the_code(student):
    otp, raw = OTPService.send_otp(student.email, OTPPurpose.VERIFY_EMAIL, user=student)
    assert raw is not None  # RETURN_DEVELOPMENT_OTP is on under the test settings
    wrong = "000000" if raw != "000000" else "111111"
    for _ in range(5):
        with pytest.raises(Exception):  # noqa: B017 - DRF ValidationError
            OTPService.verify_otp(student.email, wrong, OTPPurpose.VERIFY_EMAIL)
    otp.refresh_from_db()
    assert otp.attempts_count == 5
    assert otp.locked_at is not None
    # even the right code no longer works once locked
    with pytest.raises(Exception):  # noqa: B017
        OTPService.verify_otp(student.email, raw, OTPPurpose.VERIFY_EMAIL)


@pytest.mark.django_db
def test_wrong_reset_code_attempts_survive_the_failed_request(student):
    client = APIClient()
    request = client.post(
        reverse("request-password-reset"), {"identifier": student.email, "channel": "email"}, format="json"
    )
    assert request.status_code == status.HTTP_200_OK
    cache.clear()
    for _ in range(2):
        response = client.post(
            reverse("confirm-password-reset"),
            {
                "identifier": student.email,
                "channel": "email",
                "code": "000000",
                "new_password": "AnotherStrong123!",
                "new_password_confirm": "AnotherStrong123!",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
    otp = OTPCode.objects.get(email=student.email, purpose=OTPPurpose.RESET_PASSWORD)
    assert otp.attempts_count == 2


@pytest.mark.django_db
def test_json_array_body_is_a_400_not_a_500(student):
    response = APIClient().post(reverse("login"), [1, 2, 3], format="json")
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_password_reset_does_not_reveal_whether_the_account_exists(student, settings):
    settings.OTP_RESEND_COOLDOWN_SECONDS = 60
    client = APIClient()
    url = reverse("request-password-reset")
    first = client.post(url, {"identifier": student.email, "channel": "email"}, format="json")
    cache.clear()
    second = client.post(url, {"identifier": student.email, "channel": "email"}, format="json")  # inside cooldown
    cache.clear()
    unknown = client.post(url, {"identifier": "nobody@example.test", "channel": "email"}, format="json")
    assert first.status_code == second.status_code == unknown.status_code == status.HTTP_200_OK
    assert second.data["code"] == unknown.data["code"]
    assert len(mail.outbox) == 1


@pytest.mark.django_db
def test_otp_verify_has_a_per_account_budget_across_ips(student, settings):
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {
            **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
            "otp_verify": "1000/min",
            "otp_verify_account": "3/hour",
        },
    }
    from apps.common import throttles

    throttles.OTPVerifyAccountThrottle.THROTTLE_RATES = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    statuses = []
    for i in range(5):
        response = APIClient().post(
            reverse("otp-verify"),
            {"identifier": student.email, "channel": "email", "purpose": "verify_email", "code": "000000"},
            format="json",
            REMOTE_ADDR=f"10.0.0.{i + 1}",
        )
        statuses.append(response.status_code)
    assert status.HTTP_429_TOO_MANY_REQUESTS in statuses


@pytest.mark.django_db
def test_reset_code_does_not_mark_the_email_as_verified(db):
    user = make_user(UserRole.NORMAL_USER, 20)
    user.is_email_verified = False
    user.save(update_fields=["is_email_verified"])
    _, raw = OTPService.send_otp(user.email, OTPPurpose.RESET_PASSWORD, user=user)
    response = APIClient().post(
        reverse("otp-verify"),
        {"identifier": user.email, "channel": "email", "purpose": "reset_password", "code": raw},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    user.refresh_from_db()
    assert user.is_email_verified is False
