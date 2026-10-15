"""WhatsApp app launch: the client gets a whatsapp:// deep link, never the https invite link."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.audit.models import AuditAction, AuditLog
from apps.groups.models import Group, GroupMembership, GroupMembershipStatus
from apps.groups.services import ExternalChannelService
from apps.universities.models import University

INVITE = "https://chat.whatsapp.com/AbCdEfGhIjKlMnOp12"


def _student(email: str, phone: str, university) -> User:
    user = User.objects.create_user(
        full_name=email, email=email, phone_number=phone, password="StrongPass123!", role=UserRole.STUDENT
    )
    StudentProfile.objects.create(
        user=user,
        university=university,
        verification_status=StudentVerificationStatus.APPROVED,
        verified_at=timezone.now(),
    )
    return user


@pytest.fixture
def setup(db, settings):
    settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")
    uni = University.objects.create(name="Uni", code="U1")
    admin = User.objects.create_user(
        full_name="Admin", email="adm@example.test", phone_number="+963900000001", password="x" * 12, role="admin"
    )
    group = Group.objects.create(name="G", university=uni, created_by=admin)
    ExternalChannelService.set_whatsapp_channel(group, INVITE, admin)
    member = _student("m@example.test", "+963900000011", uni)
    outsider = _student("o@example.test", "+963900000012", uni)
    GroupMembership.objects.create(group=group, user=member, status=GroupMembershipStatus.APPROVED)
    return group, member, outsider


def _post(user, group):
    client = APIClient()
    client.force_authenticate(user)
    return client.post(f"/api/v1/groups/{group.id}/whatsapp-launch/")


@pytest.mark.parametrize(
    ("link", "expected"),
    [
        ("https://chat.whatsapp.com/AbCdEfGhIjKlMnOp12", "whatsapp://chat?code=AbCdEfGhIjKlMnOp12"),
        ("https://wa.me/963991234567", "whatsapp://send?phone=963991234567"),
        ("https://wa.me/+963991234567?text=hi", "whatsapp://send?phone=963991234567"),
        ("https://api.whatsapp.com/send?phone=963991234567&text=x", "whatsapp://send?phone=963991234567"),
        ("https://chat.whatsapp.com/", None),
        ("https://chat.whatsapp.com/short", None),
        ("https://wa.me/notdigits", None),
    ],
)
def test_deep_link_derivation(link, expected):
    assert ExternalChannelService.whatsapp_app_link(link) == expected


def test_member_receives_app_link_and_never_the_https_link(setup):
    group, member, _ = setup
    response = _post(member, group)
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["app_url"] == "whatsapp://chat?code=AbCdEfGhIjKlMnOp12"
    assert "https://" not in response.content.decode()
    assert "no-store" in response["Cache-Control"]
    assert AuditLog.objects.filter(action=AuditAction.GROUP_EXTERNAL_CHANNEL_OPENED, actor=member).exists()


def test_non_member_is_refused(setup):
    group, _, outsider = setup
    assert _post(outsider, group).status_code == 403


def test_unverified_or_anonymous_is_refused(setup):
    group, _, _ = setup
    assert APIClient().post(f"/api/v1/groups/{group.id}/whatsapp-launch/").status_code in {401, 403}


def test_group_without_channel_or_disabled_channel_is_a_validation_error(setup):
    group, member, _ = setup
    group.external_channels.update(is_active=False)
    assert _post(member, group).status_code == 400
