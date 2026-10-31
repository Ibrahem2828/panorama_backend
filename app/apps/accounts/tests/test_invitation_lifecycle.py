"""Invitation lifecycle: explicit link states, coded refusals, resend in every account state, delivery reporting."""

from __future__ import annotations

import re
from datetime import timedelta
from unittest import mock

import pytest
from django.core import mail
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import User, UserInvitation
from apps.accounts.staff_onboarding import InvitationService
from apps.audit.activity_registry import ACTION_LABELS, action_label
from apps.audit.models import AuditAction, AuditLog

from .test_staff_onboarding import client, invite, make, token_from_mail

INFO = "/api/v1/auth/invitations/{}/"
ACCEPT = "/api/v1/auth/invitations/accept/"
GOOD = {"password": "Str0ng!Passphrase#42", "password_confirm": "Str0ng!Passphrase#42"}


def resend_url(user) -> str:
    return f"/api/v1/dashboard/users/{user.pk}/resend-invitation/"


@pytest.fixture
def admin(db):
    return make(UserRole.ADMIN, 1)


@pytest.fixture
def it(db):
    return make(UserRole.IT_SUPPORT, 2)


@pytest.fixture
def invited(admin):
    assert invite(admin).status_code == 201
    return User.objects.get(email="officer@example.test"), token_from_mail()


def accept(token, **over):
    return APIClient().post(ACCEPT, {"token": token, **GOOD, **over}, format="json")


# ---- info endpoint: five states ----------------------------------------------------------------------------------------


def test_info_valid(invited):
    user, token = invited
    body = APIClient().get(INFO.format(token)).json()
    assert body["data"]["state"] == "valid"
    assert body["data"]["full_name"] == user.full_name and body["data"]["purpose"] == "invite"
    assert body["data"]["expires_at"]


def test_info_accepted_masks_email_and_has_no_name(invited):
    user, token = invited
    assert accept(token).status_code == 200
    response = APIClient().get(INFO.format(token))
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["state"] == "accepted" and data["email_masked"] == "of***@example.test"
    assert "full_name" not in data and user.email not in str(data)


def test_info_expired_revoked_unknown(admin, invited):
    user, token = invited
    UserInvitation.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
    expired = APIClient().get(INFO.format(token))
    assert expired.status_code == 200 and expired.json()["data"] == {"state": "expired"}
    UserInvitation.objects.update(expires_at=timezone.now() + timedelta(hours=1))
    assert client(admin).post(f"/api/v1/dashboard/users/{user.pk}/revoke-invitation/").status_code == 200
    assert APIClient().get(INFO.format(token)).json()["data"] == {"state": "revoked"}
    for junk in ("garbage", "x" * 200, "a-b_c" * 5):
        response = APIClient().get(INFO.format(junk))
        assert response.status_code == 200 and response.json()["data"] == {"state": "unknown"}


def test_info_for_deactivated_account_is_revoked(invited):
    user, token = invited
    User.objects.filter(pk=user.pk).update(is_active=False)
    assert APIClient().get(INFO.format(token)).json()["data"] == {"state": "revoked"}


# ---- accept: stable codes ----------------------------------------------------------------------------------------------


def assert_coded(response, code):
    body = response.json()
    assert response.status_code == 400
    assert body["code"] == code and body["success"] is False
    assert re.search("[؀-ۿ]", body["message"]), body["message"]  # Arabic message, not the generic shape


def test_accept_codes_for_every_non_valid_state(admin, invited):
    user, token = invited
    assert accept("nope-not-a-token").status_code == 400
    assert_coded(accept("nope-not-a-token"), "INVITATION_INVALID")
    assert accept(token).status_code == 200
    assert_coded(accept(token), "INVITATION_ACCEPTED_ALREADY")
    # a different invitation: expired then revoked
    with mock.patch("apps.accounts.staff_onboarding.send_mail"):
        resend_response = client(admin).post(resend_url(user))
    assert resend_response.status_code == 200
    second = UserInvitation.objects.filter(accepted_at__isnull=True).get()
    assert second.purpose == "reset"


def test_accept_expired_and_revoked_codes(admin, invited):
    user, token = invited
    UserInvitation.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
    assert_coded(accept(token), "INVITATION_EXPIRED")
    UserInvitation.objects.update(expires_at=timezone.now() + timedelta(hours=1))
    client(admin).post(f"/api/v1/dashboard/users/{user.pk}/revoke-invitation/")
    assert_coded(accept(token), "INVITATION_REVOKED")


def test_password_policy_failure_keeps_field_errors_and_does_not_burn_the_link(invited):
    _, token = invited
    response = accept(token, password="123", password_confirm="123")
    assert response.status_code == 400 and "password" in response.json()["errors"]
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert accept(token).status_code == 200


def test_double_submit_race_returns_accepted_already(invited):
    """The second request passes the unlocked pre-check, then finds the row already accepted under the lock."""

    _, token = invited
    stale = InvitationService.find(token)
    assert accept(token).status_code == 200
    with mock.patch.object(InvitationService, "find", return_value=stale):  # simulates the stale pre-lock read
        response = accept(token)
    assert_coded(response, "INVITATION_ACCEPTED_ALREADY")
    assert AuditLog.objects.filter(action=AuditAction.USER_INVITATION_ACCEPTED).count() == 1


# ---- resend in every account state -------------------------------------------------------------------------------------


def test_resend_not_yet_set_up_issues_an_invite_link(admin, invited):
    user, first = invited
    response = client(admin).post(resend_url(user))
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["purpose"] == "invite" and data["email_sent"] is True
    user.refresh_from_db()
    assert not user.has_usable_password()
    assert APIClient().get(INFO.format(first)).json()["data"]["state"] == "revoked"
    assert APIClient().get(INFO.format(token_from_mail())).json()["data"]["state"] == "valid"
    row = AuditLog.objects.filter(action=AuditAction.USER_INVITED).order_by("-id").first()
    assert row.new_value == {"resent": True, "purpose": "invite"}


@pytest.mark.parametrize("how", ["expired", "revoked"])
def test_resend_after_expiry_or_revocation_behaves_like_an_invite(admin, invited, how):
    user, _ = invited
    if how == "expired":
        UserInvitation.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
    else:
        client(admin).post(f"/api/v1/dashboard/users/{user.pk}/revoke-invitation/")
    response = client(admin).post(resend_url(user))
    assert response.status_code == 200 and response.json()["data"]["purpose"] == "invite"
    assert accept(token_from_mail()).status_code == 200


def test_resend_for_accepted_user_sends_a_reset_link_and_keeps_the_old_password(admin, invited):
    user, token = invited
    assert accept(token, password="Old!Passphrase#77", password_confirm="Old!Passphrase#77").status_code == 200
    response = client(admin).post(resend_url(user))
    body = response.json()
    assert response.status_code == 200
    assert body["data"]["purpose"] == "reset" and body["data"]["email_sent"] is True
    assert "إعادة تعيين كلمة المرور" in body["message"] and "إعادة تعيين كلمة المرور" in mail.outbox[-1].body
    assert "كلمة مرورك الحالية تبقى صالحة" in mail.outbox[-1].body
    user.refresh_from_db()
    assert user.check_password("Old!Passphrase#77") and user.must_change_password is False  # unchanged until used
    assert (
        APIClient()
        .post("/api/v1/auth/login/", {"identifier": user.email, "password": "Old!Passphrase#77"}, format="json")
        .status_code
        == 200
    )
    invitation = UserInvitation.objects.order_by("-id").first()
    assert invitation is not None and invitation.purpose == "reset" and invitation.is_usable
    audit = AuditLog.objects.filter(action=AuditAction.USER_INVITED).order_by("-id").first()
    assert audit.new_value == {"resent": True, "purpose": "reset"}
    # the link is single-use and replaces the old password
    reset_token = token_from_mail()
    assert APIClient().get(INFO.format(reset_token)).json()["data"]["purpose"] == "reset"
    assert accept(reset_token, password="New!Passphrase#88", password_confirm="New!Passphrase#88").status_code == 200
    user.refresh_from_db()
    assert user.check_password("New!Passphrase#88") and not user.check_password("Old!Passphrase#77")
    assert_coded(accept(reset_token), "INVITATION_ACCEPTED_ALREADY")


def test_a_second_reset_revokes_the_first_pending_one(admin, invited):
    user, token = invited
    accept(token)
    client(admin).post(resend_url(user))
    first = token_from_mail()
    client(admin).post(resend_url(user))
    second = token_from_mail()
    assert_coded(accept(first), "INVITATION_REVOKED")
    assert accept(second).status_code == 200


def test_resend_refuses_inactive_user_with_code(admin, invited):
    user, _ = invited
    User.objects.filter(pk=user.pk).update(is_active=False)
    response = client(admin).post(resend_url(user))
    assert_coded(response, "USER_INACTIVE")


def test_resend_permission_negatives(admin, it, invited):
    user, _ = invited
    assert client(make(UserRole.PRINT_STAFF, 5)).post(resend_url(user)).status_code == 403
    assert client(admin).post(resend_url(it)).status_code == 403  # equal/higher role
    assert APIClient().post(resend_url(user)).status_code in {401, 403}


# ---- delivery ----------------------------------------------------------------------------------------------------------


def test_email_failure_still_creates_the_account_and_reports_it(admin):
    with mock.patch("apps.accounts.staff_onboarding.send_mail", side_effect=OSError("smtp down")):
        response = invite(admin)
    assert response.status_code == 201
    body = response.json()
    assert body["code"] == "INVITATION_EMAIL_FAILED" and body["data"]["email_sent"] is False
    assert "تعذّر إرسال البريد" in body["message"]
    user = User.objects.get(email="officer@example.test")
    invitation = user.invitations.get()
    assert invitation.last_send_status == "failed" and "smtp down" in invitation.last_send_error
    activity = client(admin).get(f"/api/v1/dashboard/users/{user.pk}/activity/").json()["data"]
    delivery = activity["invitation"]["delivery"]
    assert delivery["status"] == "failed" and "smtp down" in delivery["error"] and delivery["sent_at"]
    # a later resend that works replaces the failed status
    response = client(admin).post(resend_url(user))
    assert response.json()["data"]["email_sent"] is True
    delivery = (
        client(admin).get(f"/api/v1/dashboard/users/{user.pk}/activity/").json()["data"]["invitation"]["delivery"]
    )
    assert delivery["status"] == "ok" and delivery["error"] is None


def test_resend_email_failure_is_reported_but_succeeds(admin, invited):
    user, _ = invited
    with mock.patch("apps.accounts.staff_onboarding.send_mail", side_effect=OSError("boom")):
        response = client(admin).post(resend_url(user))
    assert response.status_code == 200
    assert response.json()["code"] == "INVITATION_EMAIL_FAILED" and response.json()["data"]["email_sent"] is False
    assert response.json()["data"]["purpose"] == "invite"


def test_successful_invite_reports_email_sent_and_delivery_ok(admin):
    response = invite(admin)
    assert response.json()["data"]["email_sent"] is True and response.json()["code"] == "USER_INVITED"
    user = User.objects.get(email="officer@example.test")
    assert user.invitations.get().last_send_status == "ok"


# ---- activity payload + labels -----------------------------------------------------------------------------------------


def test_activity_invitation_payload_for_an_accepted_user_and_pending_reset(admin, invited):
    user, token = invited
    accept(token)
    url = f"/api/v1/dashboard/users/{user.pk}/activity/"
    data = client(admin).get(url).json()["data"]["invitation"]
    assert data["state"] == "accepted" and data["purpose"] == "invite" and data["accepted_at"]
    assert data["password_set"] is True and data["delivery"]["status"] == "ok"
    client(admin).post(resend_url(user))
    data = client(admin).get(url).json()["data"]["invitation"]
    assert data["state"] == "pending" and data["purpose"] == "reset" and data["accepted_at"] and data["expires_at"]


def test_activity_states_expired_revoked_none(admin, invited):
    user, _ = invited
    url = f"/api/v1/dashboard/users/{user.pk}/activity/"
    UserInvitation.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
    assert client(admin).get(url).json()["data"]["invitation"]["state"] == "expired"
    client(admin).post(f"/api/v1/dashboard/users/{user.pk}/revoke-invitation/")
    UserInvitation.objects.update(expires_at=timezone.now() + timedelta(hours=1))
    assert client(admin).get(url).json()["data"]["invitation"]["state"] == "revoked"
    bare = make(UserRole.PRINT_STAFF, 7)
    assert (
        client(admin).get(f"/api/v1/dashboard/users/{bare.pk}/activity/").json()["data"]["invitation"]["state"]
        == "none"
    )


def test_every_audit_action_has_an_arabic_label():
    missing = [a.value for a in AuditAction if a.value not in ACTION_LABELS]
    assert not missing
    for value in ACTION_LABELS.values():
        assert re.search("[؀-ۿ]", value)
    assert action_label("user_invited", {"resent": True, "purpose": "reset"}) == "إرسال رابط إعادة تعيين كلمة المرور"
    assert action_label("not_a_real_action") == "not a real action"


def test_recent_actions_rows_carry_action_label(admin, invited):
    user, token = invited
    accept(token)
    rows = client(admin).get(f"/api/v1/dashboard/users/{user.pk}/activity/").json()["data"]["recent_actions"]
    labelled = {row["action"]: row["action_label"] for row in rows}
    assert labelled[AuditAction.USER_INVITATION_ACCEPTED] == ACTION_LABELS["user_invitation_accepted"]
