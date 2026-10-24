"""Message ticks, reactions, unread counters, mute, staff titles and the dashboard chat console."""

from __future__ import annotations

import json

import pytest
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.chat import presence
from apps.chat.models import GroupChatPreference, Message, MessageReport
from apps.chat.notify import ChatNotificationService
from apps.chat.tests_consumer_cost import _finish, _open
from apps.chat.tests_handshake_tickets import _build_chat_fixture
from apps.groups.models import GroupMembership, GroupMembershipStatus
from apps.notifications.models import Notification


def _client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


def _rows(response) -> list[dict]:
    data = response.data["data"]
    return data["results"] if isinstance(data, dict) and "results" in data else data


def _second_student(group, n: int = 3) -> User:
    student = User.objects.create_user(
        full_name=f"Second student {n}",
        email=f"second{n}@example.test",
        phone_number=f"+96399000010{n}",
        password="StrongPass123!",
        role=UserRole.STUDENT,
    )
    StudentProfile.objects.create(
        user=student, university=group.university, verification_status=StudentVerificationStatus.APPROVED
    )
    GroupMembership.objects.create(group=group, user=student, status=GroupMembershipStatus.APPROVED)
    return student


def _send(user, group, text="hi") -> dict:
    response = _client(user).post(f"/api/v1/groups/{group.pk}/messages/", {"content": text}, format="json")
    assert response.status_code == status.HTTP_201_CREATED, response.data
    return response.data["data"]


def _as_seen_by(user, group, message_id) -> dict:
    listing = _client(user).get(f"/api/v1/groups/{group.pk}/messages/")
    assert listing.status_code == 200
    return next(item for item in _rows(listing) if item["id"] == message_id)


def _receipt(user, group, **body):
    return _client(user).post(f"/api/v1/groups/{group.pk}/chat/receipts/", body, format="json")


def _unread(user, group_id) -> int:
    rows = _client(user).get("/api/v1/chat/unread/").data["data"]["groups"]
    return {row["group_id"]: row["unread"] for row in rows}[group_id]


@pytest.mark.django_db
def test_ticks_go_from_sent_to_delivered_to_read():
    admin, student, group = _build_chat_fixture()
    message = _send(student, group)
    assert _as_seen_by(student, group, message["id"])["delivery"]["state"] == "sent"

    assert _receipt(admin, group, delivered_up_to=message["id"]).status_code == 200
    delivery = _as_seen_by(student, group, message["id"])["delivery"]
    assert delivery["state"] == "delivered"
    assert delivery["delivered_count"] == 1 and delivery["read_count"] == 0

    _receipt(admin, group, read_up_to=message["id"])
    assert _as_seen_by(student, group, message["id"])["delivery"]["state"] == "read"


@pytest.mark.django_db
def test_receipt_cursor_never_moves_backwards_and_is_capped_at_the_latest_message():
    admin, student, group = _build_chat_fixture()
    first = _send(student, group, "one")
    second = _send(student, group, "two")
    _receipt(admin, group, read_up_to=second["id"])
    response = _receipt(admin, group, read_up_to=first["id"], delivered_up_to=10**9)
    assert response.data["data"]["read_up_to"] == second["id"]
    assert response.data["data"]["delivered_up_to"] == second["id"]


@pytest.mark.django_db
def test_delivery_is_only_shown_to_the_sender():
    admin, student, group = _build_chat_fixture()
    message = _send(student, group)
    assert _as_seen_by(admin, group, message["id"])["delivery"] is None


@pytest.mark.django_db
def test_outsider_cannot_post_receipts_or_react():
    _admin, student, group = _build_chat_fixture()
    message = _send(student, group)
    outsider = User.objects.create_user(
        full_name="Outsider",
        email="out@example.test",
        phone_number="+963990000999",
        password="StrongPass123!",
        role=UserRole.STUDENT,
    )
    assert _receipt(outsider, group, read_up_to=1).status_code == 403
    url = f"/api/v1/groups/{group.pk}/messages/{message['id']}/reaction/"
    assert _client(outsider).put(url, {"emoji": "👍"}, format="json").status_code == 403


@pytest.mark.django_db
def test_one_reaction_per_member_replaced_and_removed():
    admin, student, group = _build_chat_fixture()
    other = _second_student(group)
    message = _send(student, group)
    url = f"/api/v1/groups/{group.pk}/messages/{message['id']}/reaction/"

    assert _client(admin).put(url, {"emoji": "👍"}, format="json").data["data"]["counts"] == {"👍": 1}
    assert _client(other).put(url, {"emoji": "👍"}, format="json").data["data"]["counts"] == {"👍": 2}
    assert _client(admin).put(url, {"emoji": "❤️"}, format="json").data["data"]["counts"] == {"👍": 1, "❤️": 1}
    assert _client(admin).delete(url).data["data"]["counts"] == {"👍": 1}

    shown = _as_seen_by(other, group, message["id"])
    assert shown["reactions"] == [{"emoji": "👍", "count": 1}]
    assert shown["my_reaction"] == "👍"


@pytest.mark.django_db
def test_reaction_must_be_from_the_allowed_set():
    admin, student, group = _build_chat_fixture()
    message = _send(student, group)
    url = f"/api/v1/groups/{group.pk}/messages/{message['id']}/reaction/"
    assert _client(admin).put(url, {"emoji": "💣"}, format="json").status_code == 400


@pytest.mark.django_db
def test_administrators_appear_by_position_and_only_staff_can_see_the_real_name():
    admin, student, group = _build_chat_fixture()
    message = _send(admin, group, "from staff")

    seen_by_student = _as_seen_by(student, group, message["id"])["sender_detail"]
    assert seen_by_student["full_name"] == "بانوراما أدمن"
    assert seen_by_student["is_staff"] is True
    assert "real_name" not in seen_by_student
    assert admin.full_name not in json.dumps(seen_by_student, ensure_ascii=False)

    assert _as_seen_by(admin, group, message["id"])["sender_detail"]["real_name"] == admin.full_name


@pytest.mark.django_db
def test_cursor_filters_return_only_what_is_missing():
    admin, student, group = _build_chat_fixture()
    ids = [_send(student, group, f"m{i}")["id"] for i in range(4)]
    response = _client(admin).get(f"/api/v1/groups/{group.pk}/messages/?after_id={ids[1]}")
    assert [item["id"] for item in _rows(response)] == ids[2:]
    assert _client(admin).get(f"/api/v1/groups/{group.pk}/messages/?after_id=abc").status_code == 400


@pytest.mark.django_db
def test_unread_counts_ignore_own_messages_and_drop_after_reading():
    admin, student, group = _build_chat_fixture()
    ids = [_send(admin, group, f"a{i}")["id"] for i in range(3)]
    _send(student, group, "mine")

    assert _unread(student, group.pk) == 3
    _receipt(student, group, read_up_to=ids[1])
    assert _unread(student, group.pk) == 1


@pytest.mark.django_db
def test_mute_preference_roundtrip():
    _admin, student, group = _build_chat_fixture()
    url = f"/api/v1/groups/{group.pk}/chat/preferences/"
    assert _client(student).get(url).data["data"]["is_muted"] is False
    assert _client(student).put(url, {"is_muted": True}, format="json").status_code == 200
    assert _client(student).get(url).data["data"]["is_muted"] is True
    assert GroupChatPreference.objects.get(group=group, user=student).is_muted


@pytest.mark.django_db
def test_notification_skips_sender_muted_members_and_members_reading_the_chat():
    admin, student, group = _build_chat_fixture()
    reader = _second_student(group, 4)
    muted = _second_student(group, 5)
    GroupChatPreference.objects.create(group=group, user=muted, is_muted=True)
    presence.mark_present(group.pk, reader.pk)
    message = Message.objects.create(group=group, sender=admin, content="exam moved")

    notified = ChatNotificationService.notify(message.pk)

    assert notified == 1  # only `student`
    assert Notification.objects.filter(user=student).exists()
    assert not Notification.objects.filter(user__in=[reader, muted, admin]).exists()
    presence.mark_absent(group.pk, reader.pk)


@pytest.mark.django_db
def test_a_second_message_updates_one_notification_instead_of_adding_another():
    admin, student, group = _build_chat_fixture()
    first = Message.objects.create(group=group, sender=admin, content="one")
    second = Message.objects.create(group=group, sender=admin, content="two")
    ChatNotificationService.notify(first.pk)
    ChatNotificationService.notify(second.pk)
    assert Notification.objects.filter(user=student).count() == 1


@pytest.mark.django_db
def test_dashboard_group_list_needs_group_management_and_shows_reports():
    admin, student, group = _build_chat_fixture()
    message = Message.objects.create(group=group, sender=student, content="bad words")
    MessageReport.objects.create(message=message, reported_by=admin, reason="abuse")

    assert _client(student).get("/api/v1/dashboard/chat/groups/").status_code == 403
    response = _client(admin).get("/api/v1/dashboard/chat/groups/")
    assert response.status_code == 200
    row = next(item for item in _rows(response) if item["id"] == group.pk)
    assert row["open_reports"] == 1
    assert row["members"] == 1
    assert row["last_message_excerpt"] == "bad words"


@pytest.mark.django_db(transaction=True)
def test_socket_receipt_frame_updates_cursors_and_is_broadcast():
    admin, student, group = _build_chat_fixture()
    message = Message.objects.create(group=group, sender=student, content="hello")

    async def scenario():
        sender_socket = await _open(student, group)
        reader_socket = await _open(admin, group)
        await reader_socket.send_input(
            {"type": "websocket.receive", "text": json.dumps({"type": "receipt", "read_up_to": message.pk})}
        )
        frame = json.loads((await sender_socket.receive_output(timeout=2))["text"])
        await _finish(sender_socket)
        await _finish(reader_socket)
        return frame

    frame = async_to_sync(scenario)()
    assert frame["type"] == "receipt"
    assert frame["data"]["user_id"] == admin.pk
    assert frame["data"]["read_up_to"] == message.pk
    assert frame["data"]["delivered_up_to"] == message.pk


@pytest.mark.django_db(transaction=True)
def test_reaction_over_rest_reaches_sockets():
    admin, student, group = _build_chat_fixture()
    message = Message.objects.create(group=group, sender=student, content="hello")

    async def scenario():
        socket = await _open(student, group)
        await database_sync_to_async(
            lambda: _client(admin).put(
                f"/api/v1/groups/{group.pk}/messages/{message.pk}/reaction/", {"emoji": "🙏"}, format="json"
            )
        )()
        frame = json.loads((await socket.receive_output(timeout=2))["text"])
        await _finish(socket)
        return frame

    frame = async_to_sync(scenario)()
    assert frame["type"] == "reaction"
    assert frame["data"]["counts"] == {"🙏": 1}
