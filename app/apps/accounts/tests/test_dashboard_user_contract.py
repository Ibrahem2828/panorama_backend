from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.audit.models import AuditAction, AuditLog


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(
        full_name="Dashboard Admin",
        email="dashboard-admin@example.test",
        phone_number="+963991000001",
        password="StrongPass123!",
        role=UserRole.ADMIN,
    )


@pytest.fixture
def target_user(db):
    return User.objects.create_user(
        full_name="Target User",
        email="target@example.test",
        phone_number="+963991000002",
        password="StrongPass123!",
        role=UserRole.NORMAL_USER,
    )


@pytest.fixture
def client(admin_user):
    api_client = APIClient()
    api_client.force_authenticate(admin_user)
    return api_client


@pytest.mark.django_db
def test_dashboard_user_contract_only_allows_get_patch_and_explicit_actions(client, target_user):
    detail = reverse("dashboard-users-detail", args=[target_user.pk])
    collection = reverse("dashboard-users-list")

    assert client.post(collection, {}, format="json").status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    assert client.put(detail, {"full_name": "Changed"}, format="json").status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    assert client.delete(detail).status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    mass_assignment = client.patch(detail, {"is_active": False}, format="json")
    assert mass_assignment.status_code == status.HTTP_400_BAD_REQUEST

    changed = client.patch(detail, {"full_name": "Allowed change"}, format="json")
    assert changed.status_code == status.HTTP_200_OK
    assert changed.data["data"]["full_name"] == "Allowed change"
    assert "effective_capabilities" in changed.data["data"]


@pytest.mark.django_db
def test_dashboard_deactivation_requires_reason_is_idempotent_and_revokes_sessions(client, target_user):
    endpoint = reverse("dashboard-users-deactivate", args=[target_user.pk])
    key = "deactivate-target-user-once"

    assert client.post(endpoint, {}, format="json", HTTP_IDEMPOTENCY_KEY=key).status_code == status.HTTP_400_BAD_REQUEST

    response = client.post(
        endpoint,
        {"reason": "Verified policy violation"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.data["code"] == "DASHBOARD_USER_DEACTIVATED"
    target_user.refresh_from_db()
    assert target_user.is_active is False
    assert target_user.session_version == 2
    assert (
        AuditLog.objects.filter(
            action=AuditAction.USER_STATUS_CHANGED,
            target_id=str(target_user.pk),
        ).count()
        == 1
    )

    replay = client.post(
        endpoint,
        {"reason": "Verified policy violation"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
    )
    assert replay.status_code == status.HTTP_200_OK
    assert replay.data == response.data
    assert (
        AuditLog.objects.filter(
            action=AuditAction.USER_STATUS_CHANGED,
            target_id=str(target_user.pk),
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_dashboard_cannot_deactivate_itself_or_last_it_support(client, admin_user):
    own = client.post(
        reverse("dashboard-users-deactivate", args=[admin_user.pk]),
        {"reason": "Not permitted"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="self-deactivation",
    )
    assert own.status_code == status.HTTP_403_FORBIDDEN

    it_support = User.objects.create_user(
        full_name="Only IT",
        email="only-it@example.test",
        phone_number="+963991000003",
        password="StrongPass123!",
        role=UserRole.IT_SUPPORT,
    )
    last_critical = client.post(
        reverse("dashboard-users-deactivate", args=[it_support.pk]),
        {"reason": "Not permitted"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="last-it-deactivation",
    )
    # An admin cannot touch IT Support at all, which is stricter than the old "keep one" guard.
    assert last_critical.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_session_version_rejects_access_token_after_password_change():
    user = User.objects.create_user(
        full_name="Session User",
        email="session@example.test",
        phone_number="+963991000004",
        password="StrongPass123!",
        role=UserRole.NORMAL_USER,
        is_email_verified=True,
    )
    client = APIClient()
    login = client.post(
        reverse("login"),
        {"identifier": user.email, "password": "StrongPass123!"},
        format="json",
    )
    assert login.status_code == status.HTTP_200_OK
    access = login.data["data"]["access"]
    refresh = login.data["data"]["refresh"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    assert client.get(reverse("current-user")).status_code == status.HTTP_200_OK

    changed = client.post(
        reverse("change-password"),
        {
            "old_password": "StrongPass123!",
            "new_password": "NewStrongPass123!",
            "new_password_confirm": "NewStrongPass123!",
        },
        format="json",
    )
    assert changed.status_code == status.HTTP_200_OK
    assert client.get(reverse("current-user")).status_code == status.HTTP_401_UNAUTHORIZED

    client.credentials()
    assert (
        client.post(reverse("token-refresh"), {"refresh": refresh}, format="json").status_code
        == status.HTTP_401_UNAUTHORIZED
    )
