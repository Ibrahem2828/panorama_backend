"""ensure_construction_engineering_community: creates/updates the faculty community group and link, idempotently."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from django.core.management import call_command
from django.core.management.base import CommandError
from rest_framework.exceptions import ValidationError

from apps.accounts.models import User
from apps.common.crypto import decrypt_text
from apps.groups.models import ExternalChannelType, Group, GroupExternalChannel
from apps.universities.models import Faculty, University

URL = "https://chat.whatsapp.com/EG1Jyzndt1WAAFsPVlYc08"


@pytest.fixture
def world(db, settings):
    settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")
    uni = University.objects.create(name="SPU", code="SPU")
    faculty = Faculty.objects.create(university=uni, name="كلية هندسة تكنولوجيا البناء والتشييد", code="7")
    Faculty.objects.create(university=uni, name="كلية الصيدلة", code="3")
    admin = User.objects.create_user(
        full_name="A", email="a@example.test", phone_number="+963900000099", password="x" * 12, role="admin"
    )
    return uni, faculty, admin


def _channel(group):
    return GroupExternalChannel.objects.get(group=group, channel_type=ExternalChannelType.WHATSAPP)


def test_creates_group_and_link_then_is_idempotent(world):
    _, faculty, _ = world
    call_command("ensure_construction_engineering_community")
    group = Group.objects.get(faculty=faculty, subject__isnull=True)
    assert decrypt_text(_channel(group).encrypted_url) == URL
    call_command("ensure_construction_engineering_community")
    assert Group.objects.filter(faculty=faculty).count() == 1
    assert GroupExternalChannel.objects.count() == 1


def test_reuses_existing_community_and_replaces_old_link(world):
    uni, faculty, admin = world
    group = Group.objects.create(name="موجود", university=uni, faculty=faculty, created_by=admin)
    from apps.groups.services import ExternalChannelService

    ExternalChannelService.set_whatsapp_channel(group, "https://chat.whatsapp.com/OLD", admin)
    call_command("ensure_construction_engineering_community")
    assert Group.objects.filter(faculty=faculty).count() == 1
    assert decrypt_text(_channel(group).encrypted_url) == URL


def test_dry_run_writes_nothing(world):
    call_command("ensure_construction_engineering_community", "--dry-run")
    assert Group.objects.count() == 0


def test_missing_or_ambiguous_faculty_stops(world):
    uni, faculty, _ = world
    Faculty.objects.create(university=uni, name="كلية التشييد الأخرى", code="9")
    with pytest.raises(CommandError):
        call_command("ensure_construction_engineering_community")
    faculty.delete()
    Faculty.objects.filter(code="9").delete()
    with pytest.raises(CommandError):
        call_command("ensure_construction_engineering_community")
    assert Group.objects.count() == 0


def test_rejects_non_whatsapp_url(world):
    with pytest.raises(ValidationError):
        call_command("ensure_construction_engineering_community", "--url", "https://evil.example/x")
