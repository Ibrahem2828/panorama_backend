"""Staff onboarding: templates, invite by link / temporary password, accept, resend/revoke, sessions and activity."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import PermissionTemplate, User, UserInvitation
from apps.accounts.permissions import Capability, PermissionService
from apps.audit.models import AuditAction, AuditLog

INVITE = "/api/v1/dashboard/users/invite/"
TEMPLATES = "/api/v1/dashboard/permission-templates/"


def make(role, n):
    return User.objects.create_user(
        full_name=f"Staff {n}",
        email=f"staff{n}@example.test",
        phone_number=f"+96398880{n:03d}",
        password="StrongPass123!",
        role=role,
        is_email_verified=True,
    )


def client(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.fixture
def admin(db):
    return make(UserRole.ADMIN, 1)


@pytest.fixture
def it(db):
    return make(UserRole.IT_SUPPORT, 2)


def template(key) -> PermissionTemplate:
    return PermissionTemplate.objects.get(key=key)


def invite(actor, key="print_officer", **over):
    body = {
        "full_name": "New Officer",
        "email": "officer@example.test",
        "phone_number": "+963988800999",
        "template": template(key).pk,
        "mode": "link",
    }
    body.update(over)
    return client(actor).post(INVITE, body, format="json")


def token_from_mail() -> str:
    match = re.search(r"/invite/([A-Za-z0-9_-]+)", str(mail.outbox[-1].body))
    assert match, mail.outbox[-1].body
    return match.group(1)


def test_five_system_templates_exist(db):
    assert set(PermissionTemplate.objects.filter(is_system=True).values_list("key", flat=True)) == {
        "admin",
        "print_officer",
        "content_officer",
        "conversation_admin",
        "support_staff",
    }


def test_invite_by_link_creates_a_locked_account_with_exactly_the_template_permissions(
    admin, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        response = invite(admin)
    assert response.status_code == 201, response.json()
    user = User.objects.get(email="officer@example.test")
    assert user.role == UserRole.PRINT_STAFF and not user.has_usable_password()
    assert PermissionService.has(user, Capability.PRINTING_MANAGE) and not PermissionService.has(
        user, Capability.FINANCE_MANAGE
    )
    assert len(mail.outbox) == 1 and "/invite/" in mail.outbox[0].body
    assert "password" not in str(response.json()).lower().replace("passwords", "")  # no secret in the API response
    assert AuditLog.objects.filter(action=AuditAction.USER_INVITED, target_id=str(user.pk)).exists()


def test_conversation_admin_template_can_manage_groups_only(admin, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        invite(admin, key="conversation_admin", email="conv@example.test", phone_number="+963988800777")
    user = User.objects.get(email="conv@example.test")
    assert PermissionService.has(user, Capability.GROUPS_MANAGE) and not PermissionService.has(
        user, Capability.FILES_MANAGE
    )


def test_accept_sets_the_password_once_and_the_user_can_log_in(admin, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        invite(admin)
    token = token_from_mail()
    api = APIClient()
    assert api.get(f"/api/v1/auth/invitations/{token}/").status_code == 200
    weak = api.post(
        "/api/v1/auth/invitations/accept/",
        {"token": token, "password": "123", "password_confirm": "123"},
        format="json",
    )
    assert weak.status_code == 400
    good = {"token": token, "password": "Str0ng!Passphrase#42", "password_confirm": "Str0ng!Passphrase#42"}
    assert api.post("/api/v1/auth/invitations/accept/", good, format="json").status_code == 200
    assert api.post("/api/v1/auth/invitations/accept/", good, format="json").status_code == 400  # single use
    assert api.get(f"/api/v1/auth/invitations/{token}/").status_code == 400
    login = api.post(
        "/api/v1/auth/login/", {"identifier": "officer@example.test", "password": "Str0ng!Passphrase#42"}, format="json"
    )
    assert login.status_code == 200, login.json()


def test_expired_and_revoked_links_are_refused(admin, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        invite(admin)
    token = token_from_mail()
    UserInvitation.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
    api = APIClient()
    body = {"token": token, "password": "Str0ng!Passphrase#42", "password_confirm": "Str0ng!Passphrase#42"}
    assert api.post("/api/v1/auth/invitations/accept/", body, format="json").status_code == 400
    UserInvitation.objects.update(expires_at=timezone.now() + timedelta(hours=1))
    user = User.objects.get(email="officer@example.test")
    assert client(admin).post(f"/api/v1/dashboard/users/{user.pk}/revoke-invitation/").status_code == 200
    assert api.post("/api/v1/auth/invitations/accept/", body, format="json").status_code == 400


def test_resend_replaces_the_old_link(admin, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        invite(admin)
    first = token_from_mail()
    user = User.objects.get(email="officer@example.test")
    with django_capture_on_commit_callbacks(execute=True):
        assert client(admin).post(f"/api/v1/dashboard/users/{user.pk}/resend-invitation/").status_code == 200
    second = token_from_mail()
    assert first != second
    api = APIClient()
    body = {"password": "Str0ng!Passphrase#42", "password_confirm": "Str0ng!Passphrase#42"}
    assert api.post("/api/v1/auth/invitations/accept/", {"token": first, **body}, format="json").status_code == 400
    assert api.post("/api/v1/auth/invitations/accept/", {"token": second, **body}, format="json").status_code == 200


def test_the_token_is_stored_only_as_a_hash(admin, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        invite(admin)
    token = token_from_mail()
    assert UserInvitation.objects.get().token_hash != token and token not in UserInvitation.objects.get().token_hash


def test_temporary_password_mode_forces_a_change(admin, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        assert invite(admin, mode="temporary_password").status_code == 201
    user = User.objects.get(email="officer@example.test")
    assert user.must_change_password and user.has_usable_password()
    found = re.search(r"المؤقتة: (\S+)", str(mail.outbox[-1].body))
    assert found
    temp = found.group(1)
    api = APIClient()
    login = api.post("/api/v1/auth/login/", {"identifier": user.email, "password": temp}, format="json")
    assert login.status_code == 200 and login.json()["data"]["user"]["must_change_password"] is True
    api.credentials(HTTP_AUTHORIZATION="Bearer " + login.json()["data"]["access"])
    changed = api.post(
        "/api/v1/auth/change-password/",
        {"old_password": temp, "new_password": "Str0ng!Passphrase#42", "new_password_confirm": "Str0ng!Passphrase#42"},
        format="json",
    )
    assert changed.status_code == 200
    user.refresh_from_db()
    assert user.must_change_password is False


def test_duplicate_email_and_rank_rules(admin, it):
    User.objects.create_user(
        full_name="x", email="officer@example.test", phone_number="+963988800123", password="StrongPass123!"
    )
    assert invite(admin).status_code == 400
    # An admin cannot mint another admin (equal rank); IT Support can.
    assert invite(admin, key="admin", email="a2@example.test", phone_number="+963988800124").status_code == 403
    assert invite(it, key="admin", email="a3@example.test", phone_number="+963988800125").status_code == 201


def test_non_managers_cannot_invite(db):
    assert invite(make(UserRole.PRINT_STAFF, 5)).status_code == 403


def test_dangerous_templates_need_confirmation_and_only_what_you_hold(admin, it):
    body = {
        "key": "finance_lead",
        "name_ar": "مالي",
        "name_en": "Finance lead",
        "role": "admin",
        "capabilities": ["finance.manage"],
    }
    assert client(it).post(TEMPLATES, body, format="json").status_code == 400  # unconfirmed sensitive grant
    ok = client(it).post(TEMPLATES, {**body, "confirm_dangerous": True}, format="json")
    assert ok.status_code == 201 and ok.json()["data"]["warnings"] == ["finance.manage"]
    # system.manage never leaves IT Support
    risky = {**body, "key": "sys", "capabilities": ["system.manage"], "confirm_dangerous": True}
    assert client(admin).post(TEMPLATES, risky, format="json").status_code in {400, 403}
    unknown = {**body, "key": "bad", "capabilities": ["nope.manage"], "confirm_dangerous": True}
    assert client(it).post(TEMPLATES, unknown, format="json").status_code == 400


def test_system_templates_cannot_be_deleted_but_custom_ones_can(it):
    system = template("support_staff")
    assert client(it).delete(f"{TEMPLATES}{system.pk}/").status_code == 400
    custom = client(it).post(
        TEMPLATES,
        {"key": "tmp", "name_ar": "م", "name_en": "Tmp", "role": "support_staff", "capabilities": ["dashboard.access"]},
        format="json",
    )
    assert client(it).delete(f"{TEMPLATES}{custom.json()['data']['id']}/").status_code in {200, 204}


def test_activity_and_ending_sessions(admin, django_capture_on_commit_callbacks):
    staff = make(UserRole.SUPPORT_STAFF, 7)
    login = APIClient().post(
        "/api/v1/auth/login/", {"identifier": staff.email, "password": "StrongPass123!"}, format="json"
    )
    assert login.status_code == 200
    activity = client(admin).get(f"/api/v1/dashboard/users/{staff.pk}/activity/").json()["data"]
    assert activity["active_sessions"] >= 1 and activity["last_login"] is not None
    before = staff.session_version
    assert client(admin).post(f"/api/v1/dashboard/users/{staff.pk}/end-sessions/").status_code == 200
    staff.refresh_from_db()
    assert staff.session_version == before + 1
    after = client(admin).get(f"/api/v1/dashboard/users/{staff.pk}/activity/").json()["data"]
    assert after["active_sessions"] == 0
    assert AuditLog.objects.filter(action=AuditAction.USER_SESSIONS_ENDED).exists()


def test_cannot_manage_equal_or_higher_roles(admin, it):
    other_admin = make(UserRole.ADMIN, 9)
    assert client(admin).post(f"/api/v1/dashboard/users/{other_admin.pk}/end-sessions/").status_code == 403
    assert client(admin).post(f"/api/v1/dashboard/users/{it.pk}/revoke-invitation/").status_code == 403
