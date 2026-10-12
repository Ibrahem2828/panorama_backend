"""Per-frame cost of the chat WebSocket: rate limit first, cheap validation, cached authorization for typing."""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest
from asgiref.sync import async_to_sync
from asgiref.testing import ApplicationCommunicator
from channels.db import database_sync_to_async
from channels.routing import URLRouter

from apps.chat.consumers import (
    AUTHORIZATION_CACHE_TTL_SECONDS,
    FRAME_BUCKET_CAPACITY,
    GroupChatConsumer,
)
from apps.chat.models import Message
from apps.chat.routing import websocket_urlpatterns
from apps.chat.tests_handshake_tickets import _build_chat_fixture, _issue_via_api
from apps.groups.models import GroupMembership, GroupMembershipStatus

TYPING = json.dumps({"type": "typing", "is_typing": True})


async def _open(student, group):
    payload = await database_sync_to_async(_issue_via_api)(student, group)
    communicator = ApplicationCommunicator(
        URLRouter(websocket_urlpatterns),
        {
            "type": "websocket",
            "path": payload["websocket_path"],
            "query_string": f"ticket={payload['ticket']}".encode("ascii"),
            "headers": [],
            "subprotocols": [],
        },
    )
    await communicator.send_input({"type": "websocket.connect"})
    assert await communicator.receive_output(timeout=2) == {"type": "websocket.accept", "subprotocol": None}
    return communicator


async def _finish(communicator):
    """Disconnect and wait so every previously queued frame has been fully processed."""
    await communicator.send_input({"type": "websocket.disconnect", "code": 1000})
    await communicator.wait(timeout=3)


async def _drain(communicator) -> list[dict]:
    outputs = []
    while not await communicator.receive_nothing(timeout=0.05):
        outputs.append(await communicator.receive_output(timeout=1))
    return outputs


@contextmanager
def _counted_authorization():
    """Count DB authorization checks by replacing the DB-backed check with a counting stub that passes."""
    stub = AsyncMock(return_value=None)
    with patch.object(GroupChatConsumer, "access_close_code", new=stub):
        yield stub


@pytest.mark.django_db(transaction=True)
def test_typing_storm_does_not_check_authorization_per_frame():
    _admin, student, group = _build_chat_fixture()

    async def scenario():
        with _counted_authorization() as auth_check:
            communicator = await _open(student, group)
            assert auth_check.await_count == 1  # the connect check primes the cache
            for _ in range(10):
                await communicator.send_input({"type": "websocket.receive", "text": TYPING})
            await _finish(communicator)
            return auth_check.await_count

    assert async_to_sync(scenario)() == 1


@pytest.mark.django_db(transaction=True)
def test_malformed_frames_are_rejected_without_any_database_or_cache_work():
    _admin, student, group = _build_chat_fixture()

    async def scenario():
        with (
            _counted_authorization() as auth_check,
            patch.object(GroupChatConsumer, "consume_rate_limit", new=AsyncMock(return_value=True)) as limiter,
        ):
            communicator = await _open(student, group)
            baseline_auth = auth_check.await_count
            frames = [
                {"bytes": b"\x00\x01"},
                {"text": "x" * (17 * 1024)},
                {"text": "{not json"},
                {"text": "[1, 2]"},
                {"text": json.dumps({"type": "nope"})},
            ]
            for frame in frames:
                await communicator.send_input({"type": "websocket.receive", **frame})
            await _finish(communicator)
            return auth_check.await_count - baseline_auth, limiter.await_count

    auth_after_connect, limiter_calls = async_to_sync(scenario)()
    assert auth_after_connect == 0
    assert limiter_calls == 0


@pytest.mark.django_db(transaction=True)
def test_frame_flood_is_dropped_before_any_database_access():
    _admin, student, group = _build_chat_fixture()
    flood = FRAME_BUCKET_CAPACITY * 4

    async def scenario():
        with (
            _counted_authorization() as auth_check,
            patch.object(GroupChatConsumer, "consume_rate_limit", new=AsyncMock(return_value=True)) as limiter,
        ):
            communicator = await _open(student, group)
            for _ in range(flood):
                await communicator.send_input({"type": "websocket.receive", "text": TYPING})
            await _finish(communicator)
            outputs = await _drain(communicator)
            return auth_check.await_count, limiter.await_count, outputs

    auth_calls, limiter_calls, outputs = async_to_sync(scenario)()
    assert auth_calls == 1  # connect only
    # The in-memory bucket stops the flood before it reaches the shared (cache) limiter: far fewer than `flood`.
    assert limiter_calls < flood // 2
    assert [o for o in outputs if o.get("text") and json.loads(o["text"]).get("code") == "RATE_LIMITED"]


@pytest.mark.django_db(transaction=True)
def test_revoked_membership_closes_on_the_first_typing_frame_after_the_cache_expires():
    _admin, student, group = _build_chat_fixture()
    membership = GroupMembership.objects.get(group=group, user=student)

    async def scenario():
        communicator = await _open(student, group)
        await database_sync_to_async(
            lambda: GroupMembership.objects.filter(pk=membership.pk).update(status=GroupMembershipStatus.BLOCKED)
        )()
        # Inside the TTL a typing frame is served from the cache: documented, bounded staleness.
        await communicator.send_input({"type": "websocket.receive", "text": TYPING})
        assert await communicator.receive_nothing(timeout=0.3)
        # After the TTL the next frame refreshes the authorization and the socket is closed with 4403.
        offset = AUTHORIZATION_CACHE_TTL_SECONDS + 1
        with patch("apps.chat.consumers._monotonic", side_effect=lambda: time.monotonic() + offset):
            await communicator.send_input({"type": "websocket.receive", "text": TYPING})
            closed = await communicator.receive_output(timeout=2)
        await communicator.wait(timeout=2)
        return closed

    assert async_to_sync(scenario)() == {"type": "websocket.close", "code": 4403}


@pytest.mark.django_db(transaction=True)
def test_sending_a_message_always_rechecks_authorization_even_inside_the_cache_window():
    _admin, student, group = _build_chat_fixture()
    membership = GroupMembership.objects.get(group=group, user=student)

    async def scenario():
        communicator = await _open(student, group)
        await database_sync_to_async(
            lambda: GroupMembership.objects.filter(pk=membership.pk).update(status=GroupMembershipStatus.BLOCKED)
        )()
        await communicator.send_input(
            {"type": "websocket.receive", "text": json.dumps({"type": "message", "content": "still here?"})}
        )
        closed = await communicator.receive_output(timeout=2)
        await communicator.wait(timeout=2)
        return closed

    assert async_to_sync(scenario)() == {"type": "websocket.close", "code": 4403}
    assert not Message.objects.filter(group=group, content="still here?").exists()


@pytest.mark.django_db(transaction=True)
def test_authorized_message_send_is_persisted_and_rechecked_each_time():
    _admin, student, group = _build_chat_fixture()

    async def scenario():
        original = GroupChatConsumer.__dict__["access_close_code"]
        calls: list[int] = []

        async def counting(self):
            calls.append(1)
            return await original.__get__(self, type(self))()

        with patch.object(GroupChatConsumer, "access_close_code", new=counting):
            communicator = await _open(student, group)
            for text in ("one", "two"):
                await communicator.send_input(
                    {"type": "websocket.receive", "text": json.dumps({"type": "message", "content": text})}
                )
            await _finish(communicator)
            return len(calls)

    # connect + one fresh check per send
    assert async_to_sync(scenario)() == 3
    assert set(Message.objects.filter(group=group).values_list("content", flat=True)) == {"one", "two"}
