"""REST-originated chat changes reach WebSocket clients; staff can list and resolve message reports."""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, patch

import pytest
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.audit.models import AuditAction, AuditLog
from apps.chat.consumers import AUTHORIZATION_CACHE_TTL_SECONDS, GroupChatConsumer
from apps.chat.models import Message, MessageReport, MessageReportStatus
from apps.chat.tests_consumer_cost import _counted_authorization, _drain, _finish, _open
from apps.chat.tests_handshake_tickets import _build_chat_fixture
from apps.groups.models import GroupMembership, GroupMembershipStatus


def _client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


def _frames(outputs: list[dict]) -> list[dict]:
    return [json.loads(item["text"]) for item in outputs if item.get("type") == "websocket.send"]


# --------------------------------------------------------------------------------------------------------------------
# REST -> WebSocket
# --------------------------------------------------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_rest_created_message_is_broadcast_to_connected_sockets():
    admin, student, group = _build_chat_fixture()

    async def scenario():
        communicator = await _open(student, group)
        response = await database_sync_to_async(
            lambda: _client(admin).post(
                f"/api/v1/groups/{group.pk}/messages/", {"content": "hello from REST"}, format="json"
            )
        )()
        assert response.status_code == status.HTTP_201_CREATED
        frame = json.loads((await communicator.receive_output(timeout=2))["text"])
        await _finish(communicator)
        return frame, response.data["data"]["id"]

    frame, message_id = async_to_sync(scenario)()
    assert frame["type"] == "message"
    assert frame["data"]["id"] == message_id
    assert frame["data"]["content"] == "hello from REST"
    assert frame["data"]["group"] == group.pk
    assert frame["data"]["sender_detail"]["id"] == admin.pk


@pytest.mark.django_db(transaction=True)
def test_idempotent_rest_replay_is_not_broadcast_twice():
    admin, student, group = _build_chat_fixture()

    async def scenario():
        communicator = await _open(student, group)

        def post():
            client = _client(admin)
            return [
                client.post(
                    f"/api/v1/groups/{group.pk}/messages/",
                    {"content": "once"},
                    format="json",
                    HTTP_IDEMPOTENCY_KEY="chat-key-000001",
                )
                for _ in range(2)
            ]

        await database_sync_to_async(post)()
        outputs = await _drain(communicator)
        await _finish(communicator)
        return outputs

    assert len(_frames(async_to_sync(scenario)())) == 1
    assert Message.objects.filter(group=group, content="once").count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("url_style", ["detail", "delete"])
def test_deleted_message_is_broadcast(url_style):
    admin, student, group = _build_chat_fixture()
    message = Message.objects.create(group=group, sender=admin, content="to remove")
    url = (
        f"/api/v1/groups/{group.pk}/messages/{message.pk}/"
        if url_style == "detail"
        else f"/api/v1/groups/{group.pk}/messages/{message.pk}/delete/"
    )

    async def scenario():
        communicator = await _open(student, group)
        response = await database_sync_to_async(lambda: _client(admin).delete(url))()
        assert response.status_code == status.HTTP_200_OK
        frame = json.loads((await communicator.receive_output(timeout=2))["text"])
        await _finish(communicator)
        return frame

    assert async_to_sync(scenario)() == {"type": "message.deleted", "data": {"id": message.pk, "group_id": group.pk}}
    assert AuditLog.objects.filter(action=AuditAction.MESSAGE_DELETED, target_id=str(message.pk)).count() == 1


@pytest.mark.django_db(transaction=True)
def test_failed_rest_write_is_never_broadcast():
    _admin, student, group = _build_chat_fixture()

    async def scenario():
        communicator = await _open(student, group)
        response = await database_sync_to_async(
            lambda: _client(student).post(f"/api/v1/groups/{group.pk}/messages/", {"content": ""}, format="json")
        )()
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        outputs = await _drain(communicator)
        await _finish(communicator)
        return outputs

    assert _frames(async_to_sync(scenario)()) == []


@pytest.mark.django_db(transaction=True)
def test_broadcast_does_not_hit_the_database_per_frame_inside_the_authorization_cache():
    admin, student, group = _build_chat_fixture()

    async def scenario():
        with _counted_authorization() as auth_check:
            communicator = await _open(student, group)
            for index in range(5):
                await database_sync_to_async(
                    lambda index=index: _client(admin).post(
                        f"/api/v1/groups/{group.pk}/messages/", {"content": f"m{index}"}, format="json"
                    )
                )()
            outputs = await _drain(communicator)
            await _finish(communicator)
            return auth_check.await_count, outputs

    checks, outputs = async_to_sync(scenario)()
    assert len(_frames(outputs)) == 5
    assert checks == 1  # only the connect check primed the cache


@pytest.mark.django_db(transaction=True)
def test_revoked_member_stops_receiving_broadcasts_once_the_cache_expires():
    admin, student, group = _build_chat_fixture()
    membership = GroupMembership.objects.get(group=group, user=student)

    async def scenario():
        communicator = await _open(student, group)
        await database_sync_to_async(
            lambda: GroupMembership.objects.filter(pk=membership.pk).update(status=GroupMembershipStatus.BLOCKED)
        )()
        offset = AUTHORIZATION_CACHE_TTL_SECONDS + 1
        with patch("apps.chat.consumers._monotonic", side_effect=lambda: time.monotonic() + offset):
            await database_sync_to_async(
                lambda: _client(admin).post(
                    f"/api/v1/groups/{group.pk}/messages/", {"content": "secret"}, format="json"
                )
            )()
            closed = await communicator.receive_output(timeout=2)
        await communicator.wait(timeout=2)
        return closed

    assert async_to_sync(scenario)() == {"type": "websocket.close", "code": 4403}


@pytest.mark.django_db(transaction=True)
def test_channel_layer_failure_does_not_fail_the_rest_request():
    admin, _student, group = _build_chat_fixture()
    with patch("apps.chat.services.get_channel_layer", side_effect=RuntimeError("redis down")):
        response = _client(admin).post(
            f"/api/v1/groups/{group.pk}/messages/", {"content": "still saved"}, format="json"
        )
    assert response.status_code == status.HTTP_201_CREATED
    assert Message.objects.filter(group=group, content="still saved").exists()


@pytest.mark.django_db(transaction=True)
def test_socket_created_messages_are_not_duplicated():
    _admin, student, group = _build_chat_fixture()

    async def scenario():
        with patch.object(GroupChatConsumer, "access_close_code", new=AsyncMock(return_value=None)):
            communicator = await _open(student, group)
            await communicator.send_input(
                {"type": "websocket.receive", "text": json.dumps({"type": "message", "content": "via ws"})}
            )
            outputs = await _drain(communicator)
            await _finish(communicator)
            return outputs

    assert len(_frames(async_to_sync(scenario)())) == 1


# --------------------------------------------------------------------------------------------------------------------
# Staff report handling
# --------------------------------------------------------------------------------------------------------------------


def _staff(role: str, n: int) -> User:
    return User.objects.create_user(
        full_name=f"Staff {role}",
        email=f"staff-{n}@example.test",
        phone_number=f"+96399100{n:04d}",
        password="StrongPass123!",
        role=role,
    )


def _report(group, admin, student, content="offensive text", reason="abusive") -> MessageReport:
    message = Message.objects.create(group=group, sender=admin, content=content)
    return MessageReport.objects.create(message=message, reported_by=student, reason=reason)


@pytest.mark.django_db
def test_report_list_filters_paginates_and_shows_excerpt():
    admin, student, group = _build_chat_fixture()
    long = _report(group, admin, student, content="x" * 500)
    done = _report(group, admin, student)
    MessageReport.objects.filter(pk=done.pk).update(status=MessageReportStatus.DISMISSED)
    manager = _staff(UserRole.CONTENT_MANAGER, 1)

    response = _client(manager).get("/api/v1/dashboard/chat/reports/?status=open")
    assert response.status_code == status.HTTP_200_OK
    rows = response.data["data"]["results"]
    assert [row["id"] for row in rows] == [long.pk]
    row = rows[0]
    assert len(row["message_excerpt"]) == 200
    assert row["reporter_id"] == student.pk
    assert row["group_id"] == group.pk and row["group_name"] == group.name
    assert row["status"] == "open"

    dismissed = _client(manager).get("/api/v1/dashboard/chat/reports/?status=dismissed")
    assert [r["id"] for r in dismissed.data["data"]["results"]] == [done.pk]
    assert _client(manager).get("/api/v1/dashboard/chat/reports/?status=bogus").status_code == 400
    paged = _client(manager).get("/api/v1/dashboard/chat/reports/?page_size=1")
    assert paged.data["data"]["count"] == 2 and len(paged.data["data"]["results"]) == 1
    assert _client(manager).get("/api/v1/dashboard/chat/reports/?group=999999").data["data"]["count"] == 0


@pytest.mark.django_db
def test_report_endpoints_require_the_group_moderation_capability():
    admin, student, group = _build_chat_fixture()
    report = _report(group, admin, student)
    for user in (student, _staff(UserRole.SUPPORT_STAFF, 2), _staff(UserRole.PRINT_STAFF, 3)):
        client = _client(user)
        assert client.get("/api/v1/dashboard/chat/reports/").status_code == status.HTTP_403_FORBIDDEN
        resolve = client.post(
            f"/api/v1/dashboard/chat/reports/{report.pk}/resolve/", {"action": "dismiss"}, format="json"
        )
        assert resolve.status_code == status.HTTP_403_FORBIDDEN
    anonymous = APIClient()
    assert anonymous.get("/api/v1/dashboard/chat/reports/").status_code == status.HTTP_401_UNAUTHORIZED
    report.refresh_from_db()
    assert report.status == MessageReportStatus.OPEN


@pytest.mark.django_db
def test_dismiss_resolves_only_that_report_and_audits_without_the_note_text():
    admin, student, group = _build_chat_fixture()
    report = _report(group, admin, student)
    response = _client(admin).post(
        f"/api/v1/dashboard/chat/reports/{report.pk}/resolve/",
        {"action": "dismiss", "note": "not a violation"},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.data["data"]["status"] == "dismissed"
    assert response.data["data"]["resolved_by_id"] == admin.pk
    report.refresh_from_db()
    assert report.resolution_note == "not a violation" and report.resolved_at
    report.message.refresh_from_db()
    assert report.message.is_deleted is False
    entry = AuditLog.objects.get(action=AuditAction.MESSAGE_REPORT_RESOLVED)
    assert entry.new_value["action"] == "dismiss" and entry.new_value["has_note"] is True
    assert "not a violation" not in json.dumps(entry.new_value)

    again = _client(admin).post(
        f"/api/v1/dashboard/chat/reports/{report.pk}/resolve/", {"action": "dismiss"}, format="json"
    )
    assert again.status_code == status.HTTP_409_CONFLICT


@pytest.mark.django_db
def test_resolve_validation_and_missing_report():
    admin, student, group = _build_chat_fixture()
    report = _report(group, admin, student)
    client = _client(admin)
    assert client.post(f"/api/v1/dashboard/chat/reports/{report.pk}/resolve/", {}, format="json").status_code == 400
    bad = client.post(f"/api/v1/dashboard/chat/reports/{report.pk}/resolve/", {"action": "ban"}, format="json")
    assert bad.status_code == status.HTTP_400_BAD_REQUEST
    assert (
        client.post("/api/v1/dashboard/chat/reports/999999/resolve/", {"action": "dismiss"}, format="json").status_code
        == status.HTTP_404_NOT_FOUND
    )


@pytest.mark.django_db(transaction=True)
def test_delete_message_action_deletes_closes_sibling_reports_and_broadcasts():
    admin, student, group = _build_chat_fixture()
    report = _report(group, admin, student)
    other = User.objects.create_user(
        full_name="Second reporter",
        email="reporter2@example.test",
        phone_number="+963990000077",
        password="StrongPass123!",
        role=UserRole.STUDENT,
    )
    sibling = MessageReport.objects.create(message=report.message, reported_by=other, reason="spam")

    async def scenario():
        communicator = await _open(student, group)
        response = await database_sync_to_async(
            lambda: _client(admin).post(
                f"/api/v1/dashboard/chat/reports/{report.pk}/resolve/",
                {"action": "delete_message", "note": "removed"},
                format="json",
                HTTP_IDEMPOTENCY_KEY="resolve-key-001",
            )
        )()
        replay = await database_sync_to_async(
            lambda: _client(admin).post(
                f"/api/v1/dashboard/chat/reports/{report.pk}/resolve/",
                {"action": "delete_message", "note": "removed"},
                format="json",
                HTTP_IDEMPOTENCY_KEY="resolve-key-001",
            )
        )()
        outputs = await _drain(communicator)
        await _finish(communicator)
        return response, replay, outputs

    response, replay, outputs = async_to_sync(scenario)()
    assert response.status_code == replay.status_code == status.HTTP_200_OK
    assert response.data["data"]["status"] == "message_deleted"
    report.message.refresh_from_db()
    assert report.message.is_deleted and report.message.deleted_by_id == admin.pk
    sibling.refresh_from_db()
    assert sibling.status == MessageReportStatus.MESSAGE_DELETED and sibling.resolved_by_id == admin.pk
    assert _frames(outputs) == [{"type": "message.deleted", "data": {"id": report.message_id, "group_id": group.pk}}]
    assert AuditLog.objects.filter(action=AuditAction.MESSAGE_DELETED).count() == 1
    assert AuditLog.objects.filter(action=AuditAction.MESSAGE_REPORT_RESOLVED).count() == 1


@pytest.mark.django_db
def test_delete_message_action_on_an_already_deleted_message_still_resolves():
    admin, student, group = _build_chat_fixture()
    report = _report(group, admin, student)
    report.message.soft_delete(admin)
    response = _client(admin).post(
        f"/api/v1/dashboard/chat/reports/{report.pk}/resolve/", {"action": "delete_message"}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.data["data"]["status"] == "message_deleted"
    assert not AuditLog.objects.filter(action=AuditAction.MESSAGE_DELETED).exists()
