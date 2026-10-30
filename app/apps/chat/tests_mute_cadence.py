"""Timed mute, the all-chats preference list, the community switch and the push cadence of chat notifications."""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

import pytest
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chat.models import GroupChatPreference, Message
from apps.chat.notify import ChatNotificationService
from apps.chat.tests_handshake_tickets import _build_chat_fixture
from apps.chat.tests_ticks_reactions import _second_student
from apps.groups.models import Group, GroupMembership, GroupMembershipStatus
from apps.notifications.models import Notification, NotificationPreference
from apps.notifications.tasks import deliver_push_notification
from apps.universities.models import AcademicYear, Faculty, Semester, Subject


def _client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


def _url(group) -> str:
    return f"/api/v1/groups/{group.pk}/chat/preferences/"


def _put(user, group, body):
    return _client(user).put(_url(group), body, format="json")


def _make_subject_group(student, admin, group) -> Group:
    faculty = Faculty.objects.create(university=group.university, name="Faculty M")
    year = AcademicYear.objects.create(name="Year M", order=41)
    semester = Semester.objects.create(name="Sem M", order=41)
    subject = Subject.objects.create(faculty=faculty, academic_year=year, semester=semester, name="Algebra")
    subject_group = Group.objects.create(
        name="Algebra group", university=group.university, subject=subject, created_by=admin
    )
    GroupMembership.objects.create(group=subject_group, user=student, status=GroupMembershipStatus.APPROVED)
    return subject_group


@pytest.fixture
def push_spy():
    with override_settings(PUSH_NOTIFICATIONS_ENABLED=True):
        with mock.patch.object(deliver_push_notification, "delay") as spy:
            yield spy


# --- timed mute --------------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_mute_for_8h_and_1w_set_muted_until_and_forever_has_none():
    _admin, student, group = _build_chat_fixture()
    now = timezone.now()
    with mock.patch("django.utils.timezone.now", return_value=now):
        eight = _put(student, group, {"is_muted": True, "mute_for": "8h"}).data["data"]
        week = _put(student, group, {"is_muted": True, "mute_for": "1w"}).data["data"]
        forever = _put(student, group, {"is_muted": True, "mute_for": "forever"}).data["data"]
        default = _put(student, group, {"is_muted": True}).data["data"]  # legacy body = forever
    assert eight["is_muted"] is True and week["is_muted"] is True
    assert eight["muted_until"] == now + timedelta(hours=8)
    assert week["muted_until"] == now + timedelta(weeks=1)
    assert forever == {"is_muted": True, "muted_until": None}
    assert default == {"is_muted": True, "muted_until": None}


@pytest.mark.django_db
def test_unmute_clears_the_timer_and_ignores_mute_for():
    _admin, student, group = _build_chat_fixture()
    _put(student, group, {"is_muted": True, "mute_for": "8h"})
    data = _put(student, group, {"is_muted": False, "mute_for": "1w"}).data["data"]
    assert data == {"is_muted": False, "muted_until": None}
    preference = GroupChatPreference.objects.get(group=group, user=student)
    assert preference.is_muted is False and preference.muted_until is None


@pytest.mark.django_db
def test_invalid_mute_for_is_rejected_and_legacy_body_still_works():
    _admin, student, group = _build_chat_fixture()
    assert _put(student, group, {"is_muted": True, "mute_for": "5y"}).status_code == 400
    assert _put(student, group, {"mute_for": "8h"}).status_code == 400
    assert _put(student, group, {"is_muted": True}).status_code == 200
    assert _client(student).get(_url(group)).data["data"] == {"is_muted": True, "muted_until": None}


@pytest.mark.django_db
def test_an_expired_mute_reads_as_unmuted_and_is_normalised_lazily():
    _admin, student, group = _build_chat_fixture()
    start = timezone.now()
    with mock.patch("django.utils.timezone.now", return_value=start):
        _put(student, group, {"is_muted": True, "mute_for": "8h"})
        assert _client(student).get(_url(group)).data["data"]["is_muted"] is True
    with mock.patch("django.utils.timezone.now", return_value=start + timedelta(hours=8, minutes=1)):
        data = _client(student).get(_url(group)).data["data"]
    assert data == {"is_muted": False, "muted_until": None}
    preference = GroupChatPreference.objects.get(group=group, user=student)
    assert preference.is_muted is False and preference.muted_until is None


@pytest.mark.django_db
def test_notify_skips_active_timed_and_forever_mutes_but_not_expired_ones():
    admin, student, group = _build_chat_fixture()
    timed, forever, expired = (_second_student(group, n) for n in (4, 5, 6))
    now = timezone.now()
    GroupChatPreference.objects.create(group=group, user=timed, is_muted=True, muted_until=now + timedelta(hours=1))
    GroupChatPreference.objects.create(group=group, user=forever, is_muted=True)
    GroupChatPreference.objects.create(group=group, user=expired, is_muted=True, muted_until=now - timedelta(minutes=1))
    message = Message.objects.create(group=group, sender=admin, content="hello")

    assert ChatNotificationService.notify(message.pk) == 2  # `student` and the member whose mute ran out
    notified = set(Notification.objects.values_list("user_id", flat=True))
    assert notified == {student.pk, expired.pk}


# --- GET chat/preferences/ ---------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_preference_list_shape_kind_order_and_effective_mute():
    admin, student, community = _build_chat_fixture()
    community.name = "Zeta community"
    community.save()
    subject_group = _make_subject_group(student, admin, community)
    other = Group.objects.create(name="Not mine", university=community.university, created_by=admin)
    pending = Group.objects.create(name="Pending one", university=community.university, created_by=admin)
    GroupMembership.objects.create(group=pending, user=student, status=GroupMembershipStatus.PENDING)
    GroupChatPreference.objects.create(group=subject_group, user=student, is_muted=True)
    GroupChatPreference.objects.create(
        group=community, user=student, is_muted=True, muted_until=timezone.now() - timedelta(hours=1)
    )

    response = _client(student).get("/api/v1/chat/preferences/")

    assert response.status_code == 200
    rows = response.data["data"]
    assert rows == [
        {
            "group_id": subject_group.pk,
            "name": "Algebra group",
            "kind": "subject",
            "is_muted": True,
            "muted_until": None,
        },
        {
            "group_id": community.pk,
            "name": "Zeta community",
            "kind": "community",
            "is_muted": False,
            "muted_until": None,
        },
    ]
    assert other.pk not in {row["group_id"] for row in rows}


@pytest.mark.django_db
def test_preference_list_has_a_constant_query_count():
    admin, student, group = _build_chat_fixture()

    def count() -> int:
        with CaptureQueriesContext(connection) as ctx:
            assert _client(student).get("/api/v1/chat/preferences/").status_code == 200
        return len(ctx)

    baseline = count()
    for n in range(5):
        extra = Group.objects.create(name=f"Extra {n}", university=group.university, created_by=admin)
        GroupMembership.objects.create(group=extra, user=student, status=GroupMembershipStatus.APPROVED)
        GroupChatPreference.objects.create(group=extra, user=student, is_muted=True)
    assert count() == baseline


@pytest.mark.django_db
def test_preference_list_requires_authentication_and_shows_only_own_state():
    _admin, student, group = _build_chat_fixture()
    assert APIClient().get("/api/v1/chat/preferences/").status_code in (401, 403)
    other = _second_student(group, 7)
    GroupChatPreference.objects.create(group=group, user=student, is_muted=True)
    rows = _client(other).get("/api/v1/chat/preferences/").data["data"]
    assert rows[0]["group_id"] == group.pk and rows[0]["is_muted"] is False


@pytest.mark.django_db
def test_non_member_cannot_read_or_write_a_group_preference():
    _admin, _student, group = _build_chat_fixture()
    outsider = User.objects.create_user(
        full_name="Outsider", email="out@example.test", phone_number="+963990000999", password="StrongPass123!"
    )
    assert _client(outsider).get(_url(group)).status_code == 403
    assert _put(outsider, group, {"is_muted": True, "mute_for": "8h"}).status_code == 403
    assert not GroupChatPreference.objects.filter(user=outsider).exists()


# --- community switch --------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_notification_preference_exposes_mute_communities():
    _admin, student, _group = _build_chat_fixture()
    client = _client(student)
    assert client.get("/api/v1/notifications/preferences/").data["data"]["mute_communities"] is False
    response = client.patch("/api/v1/notifications/preferences/", {"mute_communities": True}, format="json")
    assert response.status_code == 200 and response.data["data"]["mute_communities"] is True
    assert NotificationPreference.objects.get(user=student).mute_communities is True
    unrelated = client.patch("/api/v1/notifications/preferences/", {"in_app_enabled": True}, format="json")
    assert unrelated.data["data"]["mute_communities"] is True  # an unrelated PATCH leaves it alone


@pytest.mark.django_db
def test_mute_communities_silences_communities_only(push_spy):
    admin, student, community = _build_chat_fixture()
    subject_group = _make_subject_group(student, admin, community)
    NotificationPreference.objects.update_or_create(user=student, defaults={"mute_communities": True})
    community_message = Message.objects.create(group=community, sender=admin, content="community news")
    subject_message = Message.objects.create(group=subject_group, sender=admin, content="subject news")

    assert ChatNotificationService.notify(community_message.pk) == 0
    assert not Notification.objects.filter(user=student).exists()  # nothing kept, same as a per-chat mute
    assert ChatNotificationService.notify(subject_message.pk) == 1
    assert Notification.objects.get(user=student).data["group_id"] == subject_group.pk


@pytest.mark.django_db
def test_mute_communities_off_keeps_community_notifications():
    admin, student, community = _build_chat_fixture()
    NotificationPreference.objects.update_or_create(user=student, defaults={"mute_communities": False})
    message = Message.objects.create(group=community, sender=admin, content="community news")
    assert ChatNotificationService.notify(message.pk) == 1


# --- cadence ------------------------------------------------------------------------------------------------------


def test_default_cadence_values():
    from apps.chat import notify
    from apps.notifications import services

    assert notify.PUSH_INTERVAL_SECONDS == 8
    assert services.BURST_WINDOW_SECONDS == 5
    assert notify.push_interval_seconds() == 8
    assert services.burst_window_seconds() == 5


@override_settings(CHAT_PUSH_INTERVAL_SECONDS=30, PUSH_BURST_WINDOW_SECONDS=2)
def test_cadence_is_overridable_through_settings():
    from apps.chat import notify
    from apps.notifications import services

    assert notify.push_interval_seconds() == 30
    assert services.burst_window_seconds() == 2


@pytest.mark.django_db
def test_rapid_messages_fold_into_one_push_and_spaced_messages_each_ring(push_spy, django_capture_on_commit_callbacks):
    admin, student, group = _build_chat_fixture()
    first, second, third = (Message.objects.create(group=group, sender=admin, content=str(i)) for i in range(3))
    with django_capture_on_commit_callbacks(execute=True):
        ChatNotificationService.notify(first.pk)
        ChatNotificationService.notify(second.pk)  # inside the interval: folded
    assert push_spy.call_count == 1

    cache.delete(f"chat:push:{group.pk}:{student.pk}")  # the interval elapsed
    with django_capture_on_commit_callbacks(execute=True):
        ChatNotificationService.notify(third.pk)
    assert push_spy.call_count == 2


@pytest.mark.django_db
@override_settings(CHAT_PUSH_INTERVAL_SECONDS=0)
def test_interval_zero_pushes_every_message(push_spy, django_capture_on_commit_callbacks):
    admin, _student, group = _build_chat_fixture()
    with django_capture_on_commit_callbacks(execute=True):
        for i in range(3):
            ChatNotificationService.notify(Message.objects.create(group=group, sender=admin, content=str(i)).pk)
    assert push_spy.call_count == 3


@pytest.mark.django_db
@pytest.mark.parametrize("change", [{"push_enabled": False}, {"channels": {"chat": False}}])
def test_chat_push_is_suppressed_by_push_off_and_channel_off_but_notification_is_kept(
    change, push_spy, django_capture_on_commit_callbacks
):
    admin, student, group = _build_chat_fixture()
    NotificationPreference.objects.update_or_create(user=student, defaults=change)
    with django_capture_on_commit_callbacks(execute=True):
        ChatNotificationService.notify(Message.objects.create(group=group, sender=admin, content="x").pk)
    assert Notification.objects.filter(user=student).exists()
    assert push_spy.call_count == 0
