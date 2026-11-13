"""Account device binding: one active device per student account (apps.accounts.devices)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

from apps.accounts.choices import AccountDeviceStatus, UserRole
from apps.accounts.devices import DeviceBindingService, hash_device_id
from apps.accounts.models import AccountDevice, User
from apps.audit.models import AuditAction, AuditLog
from apps.chat.services import ChatHandshakeTicketService

pytestmark = pytest.mark.django_db

PASSWORD = "StrongPass123!"
DEVICE_A = str(uuid.uuid4())
DEVICE_B = str(uuid.uuid4())
DEVICE_C = str(uuid.uuid4())


def _headers(device_id: str | None, name: str = "Pixel 7", platform: str = "android") -> dict[str, Any]:
    if device_id is None:
        return {}
    return {
        "HTTP_X_PANORAMA_DEVICE_ID": device_id,
        "HTTP_X_PANORAMA_DEVICE_NAME": name,
        "HTTP_X_PANORAMA_PLATFORM": platform,
    }


@pytest.fixture
def enforced(settings):
    settings.DEVICE_BINDING_ENFORCED = True
    return settings


@pytest.fixture
def student():
    return User.objects.create_user(
        full_name="Dev Student",
        email="dev.student@example.test",
        phone_number="+963955100001",
        password=PASSWORD,
        role=UserRole.STUDENT,
        is_email_verified=True,
    )


@pytest.fixture
def admin():
    return User.objects.create_user(
        full_name="Dev Admin",
        email="dev.admin@example.test",
        phone_number="+963955100002",
        password=PASSWORD,
        role=UserRole.ADMIN,
        is_email_verified=True,
    )


def _login(user, device_id=None, **kwargs):
    return APIClient().post(
        reverse("login"),
        {"identifier": user.email, "password": PASSWORD},
        format="json",
        **_headers(device_id, **kwargs),
    )


def _authed(access: str, device_id: str | None = None) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}", **_headers(device_id))
    return client


def _active(user):
    return AccountDevice.objects.filter(user=user, status=AccountDeviceStatus.ACTIVE)


# -- grace mode -------------------------------------------------------------------------------------------------------


def test_default_policy_is_not_enforced(settings):
    assert settings.DEVICE_BINDING_ENFORCED is False
    assert settings.DEVICE_SWITCH_LIMIT_PER_TERM == 1
    assert settings.DEVICE_SWITCH_WINDOW_DAYS == 120


def test_not_enforced_login_without_header_works_and_binds_nothing(student):
    response = _login(student)
    assert response.status_code == 200
    assert not AccountDevice.objects.filter(user=student).exists()


def test_not_enforced_records_binding_but_never_blocks(student):
    assert _login(student, DEVICE_A).status_code == 200
    bound = _active(student).get()
    assert bound.device_hash == hash_device_id(DEVICE_A)
    assert bound.name == "Pixel 7" and bound.platform == "android"
    other = _login(student, DEVICE_B, name="iPhone", platform="ios")
    assert other.status_code == 200 and other.data["data"]["access"]
    assert _active(student).get().device_hash == hash_device_id(DEVICE_A)  # not re-bound
    assert AuditLog.objects.filter(action=AuditAction.ACCOUNT_DEVICE_MISMATCH, target_id=str(student.pk)).exists()


def test_not_enforced_requests_with_a_foreign_header_are_not_blocked(student):
    access = _login(student, DEVICE_A).data["data"]["access"]
    assert _authed(access, DEVICE_B).get(reverse("current-user")).status_code == 200


# -- enforced: login --------------------------------------------------------------------------------------------------


def test_first_login_binds_and_same_device_logs_in_again(enforced, student):
    first = _login(student, DEVICE_A)
    assert first.status_code == 200
    assert _active(student).count() == 1
    assert AuditLog.objects.filter(action=AuditAction.ACCOUNT_DEVICE_BOUND).count() == 1
    again = _login(student, DEVICE_A)
    assert again.status_code == 200
    assert AccountDevice.objects.filter(user=student).count() == 1


def test_other_device_gets_409_without_tokens(enforced, student):
    _login(student, DEVICE_A)
    outstanding = OutstandingToken.objects.count()
    response = _login(student, DEVICE_B, name="Galaxy", platform="android")
    assert response.status_code == 409
    body = response.data
    assert body["code"] == "DEVICE_MISMATCH" and body["success"] is False
    errors = body["errors"]
    assert errors["bound_device"]["name"] == "Pixel 7"
    assert errors["bound_device"]["platform"] == "android"
    assert errors["bound_device"]["last_seen_at"]
    assert errors["can_switch"] is True and errors["switches_remaining"] == 1
    assert errors["device_switch_token"]
    assert "access" not in str(body) and "refresh" not in str(body)
    assert OutstandingToken.objects.count() == outstanding  # no token was minted for the refused login
    assert AuditLog.objects.filter(action=AuditAction.ACCOUNT_DEVICE_MISMATCH).exists()
    # nothing identifying beyond name/platform/last_seen
    assert set(errors["bound_device"]) == {"name", "platform", "last_seen_at"}


def test_missing_or_invalid_header_is_refused_for_students_only(enforced, student, admin):
    assert _login(student).data["code"] == "DEVICE_ID_REQUIRED"
    assert _login(student, "not-a-uuid").data["code"] == "DEVICE_ID_REQUIRED"
    assert _login(admin).status_code == 200  # staff are exempt
    assert _login(admin, DEVICE_B).status_code == 200
    assert not AccountDevice.objects.filter(user=admin).exists()


def test_wrong_password_never_reveals_the_binding(enforced, student):
    _login(student, DEVICE_A)
    response = APIClient().post(
        reverse("login"), {"identifier": student.email, "password": "nope"}, format="json", **_headers(DEVICE_B)
    )
    assert response.status_code == 400 and response.data["code"] != "DEVICE_MISMATCH"


def test_mismatch_attempts_are_throttled(enforced, student):
    enforced.DEVICE_MISMATCH_MAX_PER_HOUR = 2
    _login(student, DEVICE_A)
    assert _login(student, DEVICE_B).status_code == 409
    assert _login(student, DEVICE_B).status_code == 409
    assert _login(student, DEVICE_B).status_code == 429


# -- switching --------------------------------------------------------------------------------------------------------


def _mismatch_token(student, device_id=DEVICE_B):
    return _login(student, device_id).data["errors"]["device_switch_token"]


def _switch(token, device_id=DEVICE_B):
    return APIClient().post(
        reverse("device-switch"), {"device_switch_token": token}, format="json", **_headers(device_id, name="Galaxy")
    )


def test_switch_takes_the_account_over_and_kills_old_sessions(enforced, student):
    old = _login(student, DEVICE_A).data["data"]
    token = _mismatch_token(student)
    response = _switch(token)
    assert response.status_code == 200 and response.data["code"] == "DEVICE_SWITCHED"
    new = response.data["data"]
    # the new device works
    assert _authed(new["access"], DEVICE_B).get(reverse("current-user")).status_code == 200
    # old device: access token and refresh token are dead, and it cannot log back in
    assert _authed(old["access"], DEVICE_A).get(reverse("current-user")).status_code == 401
    refresh = APIClient().post(
        reverse("token-refresh"), {"refresh": old["refresh"]}, format="json", **_headers(DEVICE_A)
    )
    assert refresh.status_code == 401
    assert _login(student, DEVICE_A).status_code == 409
    states = {d.device_hash: d for d in AccountDevice.objects.filter(user=student)}
    assert states[hash_device_id(DEVICE_A)].status == AccountDeviceStatus.REPLACED
    assert states[hash_device_id(DEVICE_A)].replaced_at is not None
    assert states[hash_device_id(DEVICE_B)].status == AccountDeviceStatus.ACTIVE
    assert AuditLog.objects.filter(action=AuditAction.ACCOUNT_DEVICE_SWITCHED).count() == 1
    assert student.notifications.filter(data__kind="account_device_switched").exists()


def test_chat_handshake_state_dies_with_the_switch(enforced, student):
    _login(student, DEVICE_A)
    before = User.objects.get(pk=student.pk).session_version
    _switch(_mismatch_token(student))
    after = User.objects.get(pk=student.pk)
    assert after.session_version == before + 1  # chat sockets/tickets compare against this and close
    claims = {"user_id": student.pk, "group_id": 1, "session_version": before}
    assert ChatHandshakeTicketService.authenticate_claims(claims, group_id=1) is None


def test_second_self_service_switch_hits_the_limit(enforced, student):
    _login(student, DEVICE_A)
    assert _switch(_mismatch_token(student)).status_code == 200
    token = _login(student, DEVICE_C).data["errors"]
    assert token["can_switch"] is False and token["switches_remaining"] == 0
    refused = _switch(token["device_switch_token"], DEVICE_C)
    assert refused.status_code == 403
    assert refused.data["code"] == "DEVICE_SWITCH_LIMIT_REACHED"
    assert refused.data["errors"]["contact_admin"] is True
    assert _active(student).get().device_hash == hash_device_id(DEVICE_B)
    assert AuditLog.objects.filter(action=AuditAction.ACCOUNT_DEVICE_SWITCH_REFUSED).exists()


def test_switch_limit_is_configurable_and_windowed(enforced, student):
    enforced.DEVICE_SWITCH_LIMIT_PER_TERM = 2
    _login(student, DEVICE_A)
    assert _switch(_mismatch_token(student, DEVICE_B), DEVICE_B).status_code == 200
    assert _switch(_mismatch_token(student, DEVICE_C), DEVICE_C).status_code == 200
    assert _login(student, DEVICE_A).data["errors"]["switches_remaining"] == 0
    # switches older than the window stop counting
    AccountDevice.objects.filter(user=student).update(bound_at="2000-01-01T00:00:00Z")
    assert _login(student, DEVICE_A).data["errors"]["switches_remaining"] == 2


def test_switch_token_is_single_use_device_bound_and_expires(enforced, student):
    _login(student, DEVICE_A)
    token = _mismatch_token(student)
    assert _switch(token, DEVICE_C).data["code"] == "DEVICE_SWITCH_TOKEN_INVALID"  # another device's header
    assert _switch("garbage").data["code"] == "DEVICE_SWITCH_TOKEN_INVALID"
    assert _switch(token).status_code == 200
    replay = _switch(token)
    assert replay.status_code == 400 and replay.data["code"] == "DEVICE_SWITCH_TOKEN_INVALID"
    # expiry
    _login(student, DEVICE_B)  # bound device now B; A tries again
    stale = _login(student, DEVICE_A).data["errors"]["device_switch_token"]
    enforced.DEVICE_SWITCH_TOKEN_TTL_SECONDS = -1
    assert _switch(stale, DEVICE_A).data["code"] == "DEVICE_SWITCH_TOKEN_INVALID"


def test_switch_requires_the_header_and_is_not_open_to_staff_or_other_accounts(enforced, student, admin):
    _login(student, DEVICE_A)
    token = _mismatch_token(student)
    no_header = APIClient().post(reverse("device-switch"), {"device_switch_token": token}, format="json")
    assert no_header.status_code == 400 and no_header.data["code"] == "DEVICE_ID_REQUIRED"
    assert DeviceBindingService.applies_to(admin) is False


# -- refresh / requests -----------------------------------------------------------------------------------------------


def test_refresh_from_bound_device_rotates_and_keeps_the_device_claim(enforced, student):
    tokens = _login(student, DEVICE_A).data["data"]
    client = APIClient()
    response = client.post(
        reverse("token-refresh"), {"refresh": tokens["refresh"]}, format="json", **_headers(DEVICE_A)
    )
    assert response.status_code == 200
    rotated = response.data["data"]
    assert _authed(rotated["access"], DEVICE_A).get(reverse("current-user")).status_code == 200
    again = client.post(reverse("token-refresh"), {"refresh": rotated["refresh"]}, format="json", **_headers(DEVICE_A))
    assert again.status_code == 200


def test_refresh_from_another_device_is_refused_without_a_switch_token(enforced, student):
    tokens = _login(student, DEVICE_A).data["data"]
    response = APIClient().post(
        reverse("token-refresh"), {"refresh": tokens["refresh"]}, format="json", **_headers(DEVICE_B)
    )
    assert response.status_code == 409 and response.data["code"] == "DEVICE_MISMATCH"
    assert response.data["errors"]["device_switch_token"] is None
    assert "access" not in response.data["errors"]
    missing = APIClient().post(reverse("token-refresh"), {"refresh": tokens["refresh"]}, format="json")
    assert missing.status_code == 400 and missing.data["code"] == "DEVICE_ID_REQUIRED"


def test_access_token_only_works_from_its_device(enforced, student):
    access = _login(student, DEVICE_A).data["data"]["access"]
    assert _authed(access, DEVICE_A).get(reverse("current-user")).status_code == 200
    foreign = _authed(access, DEVICE_B).get(reverse("current-user"))
    assert foreign.status_code == 401 and foreign.data["code"] == "DEVICE_MISMATCH"
    missing = _authed(access).get(reverse("current-user"))
    assert missing.status_code == 401 and missing.data["code"] == "DEVICE_ID_REQUIRED"


def test_legacy_sessions_bind_on_first_refresh_or_request(enforced, student):
    from apps.accounts.authentication import SessionVersionRefreshToken

    # tokens issued before the feature shipped carry no device claim
    refresh = SessionVersionRefreshToken.for_user(student)
    response = APIClient().post(
        reverse("token-refresh"), {"refresh": str(refresh)}, format="json", **_headers(DEVICE_A)
    )
    assert response.status_code == 200
    assert _active(student).get().device_hash == hash_device_id(DEVICE_A)
    assert _active(student).get().bound_via == "legacy_session"
    assert _authed(response.data["data"]["access"], DEVICE_A).get(reverse("current-user")).status_code == 200

    other = User.objects.create_user(
        full_name="Legacy Two",
        email="legacy2@example.test",
        phone_number="+963955100009",
        password=PASSWORD,
        role=UserRole.STUDENT,
        is_email_verified=True,
    )
    legacy_access = str(SessionVersionRefreshToken.for_user(other).access_token)
    assert _authed(legacy_access, DEVICE_B).get(reverse("current-user")).status_code == 200  # first request binds
    assert _active(other).get().device_hash == hash_device_id(DEVICE_B)
    assert _authed(legacy_access, DEVICE_C).get(reverse("current-user")).status_code == 401


def test_logout_keeps_the_binding(enforced, student):
    tokens = _login(student, DEVICE_A).data["data"]
    response = _authed(tokens["access"], DEVICE_A).post(
        reverse("logout"), {"refresh": tokens["refresh"]}, format="json"
    )
    assert response.status_code == 200
    assert _active(student).get().device_hash == hash_device_id(DEVICE_A)
    assert _login(student, DEVICE_B).status_code == 409  # logging out does not free the account for someone else
    assert _login(student, DEVICE_A).status_code == 200


def test_staff_tokens_ignore_device_headers(enforced, admin):
    access = _login(admin).data["data"]["access"]
    assert _authed(access).get(reverse("current-user")).status_code == 200
    assert _authed(access, DEVICE_B).get(reverse("current-user")).status_code == 200


def test_the_device_id_is_stored_hashed_only(enforced, student):
    _login(student, DEVICE_A)
    device = _active(student).get()
    assert DEVICE_A not in device.device_hash and len(device.device_hash) == 64


def test_account_deletion_revokes_the_binding(enforced, student):
    from apps.product.services import AccountDeletionService

    _login(student, DEVICE_A)
    deletion = AccountDeletionService.request(student, reason="bye")
    AccountDeletionService.execute(deletion.pk, force=True)
    device = AccountDevice.objects.get(user=student)
    assert device.status == AccountDeviceStatus.REVOKED and device.name == ""


# -- dashboard --------------------------------------------------------------------------------------------------------


def _staff_client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


def test_staff_sees_history_and_counter(enforced, student, admin):
    _login(student, DEVICE_A)
    _switch(_mismatch_token(student))
    response = _staff_client(admin).get(f"/api/v1/dashboard/users/{student.pk}/account-devices/")
    assert response.status_code == 200
    data = response.data["data"]
    assert data["switches_used"] == 1 and data["switch_limit"] == 1 and data["switches_remaining"] == 0
    assert data["binding_enforced"] is True and data["binding_applies"] is True
    assert [d["status"] for d in data["devices"]] == ["active", "replaced"]
    assert data["active_device"]["platform"] == "android"
    assert "device_hash" not in str(data)


def test_staff_reset_clears_binding_counter_and_sessions(enforced, student, admin):
    tokens = _login(student, DEVICE_A).data["data"]
    _switch(_mismatch_token(student))
    response = _staff_client(admin).post(
        f"/api/v1/dashboard/users/{student.pk}/account-devices/reset/", {"reason": "lost phone"}, format="json"
    )
    assert response.status_code == 200 and response.data["code"] == "ACCOUNT_DEVICE_RESET"
    assert not _active(student).exists()
    assert AuditLog.objects.filter(action=AuditAction.ACCOUNT_DEVICE_RESET, actor=admin).exists()
    assert _authed(tokens["access"], DEVICE_A).get(reverse("current-user")).status_code == 401  # sessions ended
    # the counter restarted: next login binds whatever device is used, and one more switch is allowed
    assert _login(student, DEVICE_C).status_code == 200
    state = _staff_client(admin).get(f"/api/v1/dashboard/users/{student.pk}/account-devices/").data["data"]
    assert state["switches_used"] == 0
    assert _switch(_login(student, DEVICE_A).data["errors"]["device_switch_token"], DEVICE_A).status_code == 200


def test_device_endpoints_require_user_management(enforced, student, admin):
    other = User.objects.create_user(
        full_name="Print",
        email="print@example.test",
        phone_number="+963955100020",
        password=PASSWORD,
        role=UserRole.PRINT_STAFF,
    )
    for actor in (student, other):
        client = _staff_client(actor)
        assert client.get(f"/api/v1/dashboard/users/{student.pk}/account-devices/").status_code == 403
        assert (
            client.post(f"/api/v1/dashboard/users/{student.pk}/account-devices/reset/", {}, format="json").status_code
            == 403
        )
    assert APIClient().get(f"/api/v1/dashboard/users/{student.pk}/account-devices/").status_code == 401
    # an admin cannot reset an account that outranks them
    it_support = User.objects.create_user(
        full_name="IT",
        email="it@example.test",
        phone_number="+963955100021",
        password=PASSWORD,
        role=UserRole.IT_SUPPORT,
    )
    assert (
        _staff_client(admin)
        .post(f"/api/v1/dashboard/users/{it_support.pk}/account-devices/reset/", {}, format="json")
        .status_code
        == 403
    )
