"""Per-account login lockout, last_login, token lifetime and the auth audit trail."""

from __future__ import annotations

import pytest
from django.conf import settings as django_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts import lockout
from apps.accounts.choices import UserRole
from apps.accounts.lockout import LoginLockoutService
from apps.accounts.models import User
from apps.audit.models import AuditAction, AuditLog

PASSWORD = "StrongPass123!"


@pytest.fixture
def user(db):
    return User.objects.create_user(
        full_name="Lock Target",
        email="Target@Example.com",
        phone_number="+963944000001",
        password=PASSWORD,
        role=UserRole.NORMAL_USER,
        is_email_verified=True,
    )


@pytest.fixture
def staff(db):
    return User.objects.create_user(
        full_name="Staff Admin",
        email="staff-lockout@example.com",
        phone_number="+963944000002",
        password=PASSWORD,
        role=UserRole.ADMIN,
        is_email_verified=True,
    )


@pytest.fixture
def small_lockout(settings):
    settings.LOGIN_LOCKOUT_THRESHOLD = 3
    settings.LOGIN_LOCKOUT_WINDOW_MINUTES = 15
    settings.LOGIN_LOCKOUT_MINUTES = 10


def login(identifier: str, password: str, ip: str = "10.1.0.1"):
    return APIClient().post(
        reverse("login"), {"identifier": identifier, "password": password}, format="json", REMOTE_ADDR=ip
    )


def fail_n(identifier: str, n: int) -> list:
    # A different source IP per attempt: the per-IP+identifier throttle never helps here.
    return [login(identifier, "wrong-password", ip=f"10.2.0.{i + 1}") for i in range(n)]


@pytest.mark.usefixtures("small_lockout")
def test_account_locks_after_threshold_across_rotating_ips(user):
    responses = fail_n(user.email, 3)
    assert [r.status_code for r in responses] == [400, 400, 400]  # the locking attempt itself looks like any failure

    locked = login(user.email, PASSWORD, ip="10.3.0.9")  # even the right password is refused while locked
    assert locked.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert locked.data["code"] == "RATE_LIMITED"
    assert 0 < int(locked["Retry-After"]) <= 600
    assert locked.data["retry_after_seconds"] == int(locked["Retry-After"])

    rows = AuditLog.objects.filter(action=AuditAction.ACCOUNT_LOCKED)
    assert rows.count() == 1
    row = rows.get()
    assert row.target_id == str(user.id)
    assert row.new_value == {"failed_attempts": 3, "locked_minutes": 10}
    assert user.email.lower() not in str(row.new_value).lower()


@pytest.mark.usefixtures("small_lockout")
def test_unknown_identifier_is_locked_exactly_like_a_real_one(user):
    ghost = "nobody@example.com"
    real_codes = [r.status_code for r in fail_n(user.email, 4)]
    LoginLockoutService.clear(user.email)
    ghost_codes = [r.status_code for r in fail_n(ghost, 4)]
    assert real_codes == ghost_codes == [400, 400, 400, 429]
    # Only real accounts are audited, so spraying random identifiers cannot flood the audit log.
    assert AuditLog.objects.filter(action=AuditAction.ACCOUNT_LOCKED).count() == 1


@pytest.mark.usefixtures("small_lockout")
def test_identifier_is_normalised_for_the_counter(user):
    login("TARGET@example.com ", "bad", ip="10.4.0.1")
    login("target@EXAMPLE.com", "bad", ip="10.4.0.2")
    login(user.email, "bad", ip="10.4.0.3")
    assert login(user.email, PASSWORD, ip="10.4.0.4").status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.usefixtures("small_lockout")
def test_successful_login_resets_counter_and_sets_last_login(user):
    assert user.last_login is None
    fail_n(user.email, 2)
    ok = login(user.email, PASSWORD, ip="10.5.0.1")
    assert ok.status_code == status.HTTP_200_OK
    user.refresh_from_db()
    assert user.last_login is not None

    # Two more failures after the reset must not lock: the counter restarted at zero.
    assert [r.status_code for r in fail_n(user.email, 2)] == [400, 400]
    assert login(user.email, PASSWORD, ip="10.5.0.2").status_code == status.HTTP_200_OK


@pytest.mark.usefixtures("small_lockout")
def test_lock_expires_after_the_lockout_period(user, monkeypatch):
    fail_n(user.email, 3)
    assert login(user.email, PASSWORD, ip="10.6.0.1").status_code == 429
    real_time = lockout.time.time
    monkeypatch.setattr(lockout.time, "time", lambda: real_time() + 601)
    assert LoginLockoutService.retry_after(user.email) == 0
    assert login(user.email, PASSWORD, ip="10.6.0.2").status_code == status.HTTP_200_OK


@pytest.mark.usefixtures("small_lockout")
def test_correct_password_for_unverified_account_does_not_count_as_a_failure(db):
    unverified = User.objects.create_user(
        full_name="Unverified",
        email="unverified-lock@example.com",
        phone_number="+963944000003",
        password=PASSWORD,
        role=UserRole.NORMAL_USER,
    )
    codes = [login(unverified.email, PASSWORD, ip=f"10.7.0.{i}").status_code for i in range(1, 6)]
    assert codes == [400] * 5
    assert not AuditLog.objects.filter(action=AuditAction.ACCOUNT_LOCKED).exists()


@pytest.mark.usefixtures("small_lockout")
def test_password_reset_confirmation_lifts_the_lockout_and_is_audited(user):
    fail_n(user.email, 3)
    assert login(user.email, PASSWORD, ip="10.8.0.1").status_code == 429

    client = APIClient()
    sent = client.post(reverse("request-password-reset"), {"email": user.email.lower()}, format="json")
    assert sent.status_code == status.HTTP_200_OK  # reset/OTP flows are not blocked by the login lockout
    done = client.post(
        reverse("confirm-password-reset"),
        {
            "email": user.email.lower(),
            "code": sent.data["data"]["development_otp"],
            "new_password": "ResetStrongPass123!",
            "new_password_confirm": "ResetStrongPass123!",
        },
        format="json",
    )
    assert done.status_code == status.HTTP_200_OK
    assert login(user.email, "ResetStrongPass123!", ip="10.8.0.2").status_code == status.HTTP_200_OK

    row = AuditLog.objects.get(action=AuditAction.PASSWORD_RESET_COMPLETED)
    assert row.target_id == str(user.id)
    assert "ResetStrongPass123!" not in str(row.new_value)


@pytest.mark.usefixtures("small_lockout")
def test_staff_can_unlock_an_account(user, staff):
    fail_n(user.email, 3)
    assert login(user.email, PASSWORD, ip="10.9.0.1").status_code == 429

    client = APIClient()
    client.force_authenticate(staff)
    response = client.post(f"/api/v1/dashboard/users/{user.id}/unlock-login/")
    assert response.status_code == status.HTTP_200_OK
    assert response.data["code"] == "DASHBOARD_USER_LOGIN_UNLOCKED"
    assert AuditLog.objects.filter(action=AuditAction.ACCOUNT_LOGIN_UNLOCKED, target_id=str(user.id)).count() == 1
    assert login(user.email, PASSWORD, ip="10.9.0.2").status_code == status.HTTP_200_OK


@pytest.mark.usefixtures("small_lockout")
def test_regular_user_cannot_unlock_accounts(user):
    other = User.objects.create_user(
        full_name="Other",
        email="other-lock@example.com",
        phone_number="+963944000004",
        password=PASSWORD,
        role=UserRole.NORMAL_USER,
    )
    client = APIClient()
    client.force_authenticate(other)
    assert client.post(f"/api/v1/dashboard/users/{user.id}/unlock-login/").status_code == status.HTTP_403_FORBIDDEN


def test_lockout_defaults_are_conservative():
    assert django_settings.LOGIN_LOCKOUT_THRESHOLD >= 3
    assert django_settings.LOGIN_LOCKOUT_MINUTES >= 1
    assert django_settings.LOGIN_LOCKOUT_WINDOW_MINUTES >= 1


@pytest.mark.django_db
def test_access_token_lifetime_follows_settings_and_logout_blacklists_refresh(user):
    response = login(user.email, PASSWORD)
    access = AccessToken(response.data["data"]["access"])
    lifetime = django_settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"]
    assert access["exp"] - access["iat"] == int(lifetime.total_seconds())  # type: ignore[attr-defined]

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['data']['access']}")
    refresh = response.data["data"]["refresh"]
    assert client.post(reverse("logout"), {"refresh": refresh}, format="json").status_code == status.HTTP_200_OK
    assert (
        APIClient().post(reverse("token-refresh"), {"refresh": refresh}, format="json").status_code
        == status.HTTP_401_UNAUTHORIZED
    )


@pytest.mark.django_db
def test_change_password_is_audited_without_secrets(user):
    client = APIClient()
    client.force_authenticate(user)
    response = client.post(
        reverse("change-password"),
        {"old_password": PASSWORD, "new_password": "NewStrongPass123!", "new_password_confirm": "NewStrongPass123!"},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    row = AuditLog.objects.get(action=AuditAction.PASSWORD_CHANGED)
    assert row.actor_id == user.id and row.target_id == str(user.id)
    assert "NewStrongPass123!" not in str(row.new_value) and PASSWORD not in str(row.new_value)


@pytest.mark.django_db
def test_registration_is_audited_with_ids_only():
    payload = {
        "full_name": "Fresh Person",
        "email": "fresh-audit@example.com",
        "phone_number": "+963944000099",
        "password": PASSWORD,
        "password_confirm": PASSWORD,
    }
    response = APIClient().post(reverse("register-normal"), payload, format="json")
    assert response.status_code == status.HTTP_201_CREATED
    created = User.objects.get(email="fresh-audit@example.com")
    row = AuditLog.objects.get(action=AuditAction.USER_REGISTERED)
    assert row.target_id == str(created.id)
    serialised = f"{row.old_value}{row.new_value}"
    assert "fresh-audit" not in serialised and "+963944000099" not in serialised and "Fresh" not in serialised
