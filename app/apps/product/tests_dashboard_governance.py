from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.audit.models import AuditAction, AuditLog

from .models import AccountDeletionRequest, AccountDeletionStatus, DeviceInstallation


def make_user(email, role=UserRole.NORMAL_USER, phone="+963944450001"):
    return User.objects.create_user(
        full_name=email.split("@")[0], email=email, phone_number=phone, password="StrongPass123!", role=role
    )


@pytest.fixture
def admin(db):
    return make_user("gov-admin@example.com", UserRole.ADMIN, "+963944450010")


@pytest.fixture
def client_for(db):
    def _make(user):
        api = APIClient()
        api.force_authenticate(user=user)
        return api

    return _make


@pytest.fixture
def pending(db):
    user = make_user("leaving@example.com", phone="+963944450011")
    return AccountDeletionRequest.objects.create(
        user=user, scheduled_for=timezone.now() + timedelta(days=20), reason="no longer studying"
    )


@pytest.mark.django_db
def test_only_staff_with_user_management_can_list_deletions(admin, client_for, pending):
    assert client_for(admin).get("/api/v1/dashboard/account-deletions/").status_code == status.HTTP_200_OK
    outsider = make_user("student@example.com", phone="+963944450012")
    assert client_for(outsider).get("/api/v1/dashboard/account-deletions/").status_code == status.HTTP_403_FORBIDDEN
    assert APIClient().get("/api/v1/dashboard/account-deletions/").status_code in {401, 403}


@pytest.mark.django_db
def test_list_filters_and_detail_timeline(admin, client_for, pending):
    api = client_for(admin)
    listed = api.get("/api/v1/dashboard/account-deletions/?status=requested&search=leaving").json()["data"]
    rows = listed["results"] if "results" in listed else listed
    assert [row["id"] for row in rows] == [pending.id]
    assert rows[0]["days_remaining"] in {19, 20}
    none_yet = api.get("/api/v1/dashboard/account-deletions/?status=completed").json()["data"]
    assert (none_yet["results"] if "results" in none_yet else none_yet) == []
    detail = api.get(f"/api/v1/dashboard/account-deletions/{pending.id}/").json()["data"]
    assert detail["user"]["email"] == "leaving@example.com"
    assert isinstance(detail["timeline"], list)


@pytest.mark.django_db
def test_staff_cancel_needs_a_reason_and_is_audited(admin, client_for, pending):
    api = client_for(admin)
    url = f"/api/v1/dashboard/account-deletions/{pending.id}/cancel/"
    assert api.post(url, {"reason": "x"}, format="json").status_code == status.HTTP_400_BAD_REQUEST
    ok = api.post(url, {"reason": "student asked us to keep the account"}, format="json")
    assert ok.status_code == status.HTTP_200_OK
    pending.refresh_from_db()
    assert pending.status == AccountDeletionStatus.CANCELLED
    entry = AuditLog.objects.filter(action=AuditAction.ACCOUNT_DELETION_CANCELLED, actor=admin).latest("id")
    assert entry.new_value["by_staff"] is True
    assert api.post(url, {"reason": "again please"}, format="json").status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_execute_requires_admin_confirmation_and_anonymizes(admin, client_for, pending):
    api = client_for(admin)
    url = f"/api/v1/dashboard/account-deletions/{pending.id}/execute/"
    wrong = api.post(url, {"reason": "legal request", "confirm_email": "other@example.com"}, format="json")
    assert wrong.status_code == status.HTTP_400_BAD_REQUEST
    pending.refresh_from_db()
    assert pending.status == AccountDeletionStatus.REQUESTED
    done = api.post(url, {"reason": "legal request", "confirm_email": "LEAVING@example.com"}, format="json")
    assert done.status_code == status.HTTP_200_OK, done.content
    pending.refresh_from_db()
    pending.user.refresh_from_db()
    assert pending.status == AccountDeletionStatus.COMPLETED
    assert pending.user.email.startswith("deleted-") and not pending.user.is_active
    entry = AuditLog.objects.filter(action=AuditAction.ACCOUNT_DELETION_COMPLETED, actor=admin).latest("id")
    assert entry.new_value["reason"] == "legal request"


@pytest.mark.django_db
def test_support_staff_cannot_execute_and_nobody_acts_on_own_or_higher_accounts(client_for, pending, admin):
    support = make_user("support@example.com", UserRole.SUPPORT_STAFF, "+963944450013")
    assert (
        client_for(support)
        .post(f"/api/v1/dashboard/account-deletions/{pending.id}/execute/", {}, format="json")
        .status_code
        == status.HTTP_403_FORBIDDEN
    )
    own = AccountDeletionRequest.objects.create(user=admin, scheduled_for=timezone.now() + timedelta(days=5))
    assert (
        client_for(admin)
        .post(f"/api/v1/dashboard/account-deletions/{own.id}/cancel/", {"reason": "cancelling mine"}, format="json")
        .status_code
        == status.HTTP_403_FORBIDDEN
    )


@pytest.fixture
def device(db):
    user = make_user("phone-owner@example.com", phone="+963944450014")
    return DeviceInstallation.objects.create(
        user=user,
        installation_id=uuid4(),
        platform="android",
        app_version="2.3.0",
        push_token="secret-push-token-value",
        device_model="Pixel",
    )


@pytest.mark.django_db
def test_devices_list_detail_never_expose_the_push_token(admin, client_for, device):
    api = client_for(admin)
    response = api.get("/api/v1/dashboard/devices/?platform=android&revoked=false")
    assert response.status_code == status.HTTP_200_OK
    assert "secret-push-token-value" not in response.content.decode()
    assert api.get(f"/api/v1/dashboard/devices/{device.id}/").status_code == status.HTTP_200_OK
    assert "secret-push-token-value" not in api.get(f"/api/v1/dashboard/devices/{device.id}/").content.decode()


@pytest.mark.django_db
def test_revoke_device_clears_the_token_and_is_audited(admin, client_for, device):
    api = client_for(admin)
    url = f"/api/v1/dashboard/devices/{device.id}/revoke/"
    assert api.post(url, {"reason": "no"}, format="json").status_code == status.HTTP_400_BAD_REQUEST
    assert api.post(url, {"reason": "lost phone reported"}, format="json").status_code == status.HTTP_200_OK
    device.refresh_from_db()
    assert device.revoked_at is not None and device.push_token is None and device.notifications_enabled is False
    assert AuditLog.objects.filter(action=AuditAction.DEVICE_INSTALLATION_REVOKED, actor=admin).exists()
    assert api.post(url, {"reason": "second time"}, format="json").status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_device_stats_and_permissions(admin, client_for, device):
    stats = client_for(admin).get("/api/v1/dashboard/devices/stats/").json()["data"]
    assert stats["total"] == 1 and stats["active"] == 1 and stats["by_platform"][0]["platform"] == "android"
    print_staff = make_user("print@example.com", UserRole.PRINT_STAFF, "+963944450015")
    assert client_for(print_staff).get("/api/v1/dashboard/devices/stats/").status_code == status.HTTP_403_FORBIDDEN
