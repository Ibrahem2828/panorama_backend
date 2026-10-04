from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

import pytest
from asgiref.sync import async_to_sync
from asgiref.testing import ApplicationCommunicator
from channels.db import database_sync_to_async
from channels.routing import URLRouter
from django.core.cache import cache
from django.db.models import F
from django.test import override_settings
from django.utils import timezone
from redis.exceptions import ConnectionError as RedisConnectionError
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.authentication import SessionVersionRefreshToken
from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.chat.routing import websocket_urlpatterns
from apps.chat.services import ChatHandshakeTicketService
from apps.common.throttles import ChatTicketRateThrottle
from apps.groups.models import Group, GroupMembership, GroupMembershipStatus
from apps.universities.models import University


def _build_chat_fixture() -> tuple[User, User, Group]:
    admin = User.objects.create_user(
        full_name="Chat administrator",
        email="chat-admin@example.test",
        phone_number="+963990000001",
        password="StrongPass123!",
        role=UserRole.ADMIN,
    )
    student = User.objects.create_user(
        full_name="Approved chat student",
        email="chat-student@example.test",
        phone_number="+963990000002",
        password="StrongPass123!",
        role=UserRole.STUDENT,
    )
    university = University.objects.create(name="Chat Ticket University", code="CTU")
    StudentProfile.objects.create(
        user=student,
        university=university,
        verification_status=StudentVerificationStatus.APPROVED,
    )
    group = Group.objects.create(name="Ticket protected chat", university=university, created_by=admin)
    GroupMembership.objects.create(group=group, user=student, status=GroupMembershipStatus.APPROVED)
    return admin, student, group


def _issue_via_api(user: User, group: Group) -> dict:
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post(f"/api/v1/groups/{group.pk}/chat-ticket/", format="json")
    assert response.status_code == status.HTTP_201_CREATED, response.data
    return response.data["data"]


async def _connect(path: str):
    route, _, raw_query = path.partition("?")
    communicator = ApplicationCommunicator(
        URLRouter(websocket_urlpatterns),
        {
            "type": "websocket",
            "path": route,
            "query_string": raw_query.encode("ascii"),
            "headers": [],
            "subprotocols": [],
        },
    )
    await communicator.send_input({"type": "websocket.connect"})
    output = await communicator.receive_output(timeout=1)
    if output["type"] == "websocket.accept":
        await communicator.send_input({"type": "websocket.disconnect", "code": 1000})
        await communicator.wait(timeout=1)
    return output


async def _connection_rejected_after_membership_block(path: str, membership_id: int):
    return await _connection_rejected_after_state_change(
        path,
        lambda: GroupMembership.objects.filter(pk=membership_id).update(status=GroupMembershipStatus.BLOCKED),
    )


async def _connection_rejected_after_state_change(path: str, state_change: Callable[[], object]):
    route, _, raw_query = path.partition("?")
    communicator = ApplicationCommunicator(
        URLRouter(websocket_urlpatterns),
        {
            "type": "websocket",
            "path": route,
            "query_string": raw_query.encode("ascii"),
            "headers": [],
            "subprotocols": [],
        },
    )
    await communicator.send_input({"type": "websocket.connect"})
    assert await communicator.receive_output(timeout=1) == {"type": "websocket.accept", "subprotocol": None}
    await database_sync_to_async(state_change)()
    await communicator.send_input({"type": "websocket.receive", "text": '{"type":"typing","is_typing":true}'})
    closed = await communicator.receive_output(timeout=1)
    await communicator.wait(timeout=1)
    return closed


def _concurrently_consume_ticket(token: str, group_id: int) -> list[dict[str, int] | None]:
    barrier = Barrier(2)

    def consume() -> dict[str, int] | None:
        barrier.wait(timeout=2)
        return ChatHandshakeTicketService.consume(token, group_id=group_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        return list(executor.map(lambda _attempt: consume(), range(2)))


@pytest.mark.django_db(transaction=True)
def test_chat_ticket_is_short_lived_opaque_and_allows_one_authorized_connection():
    _admin, student, group = _build_chat_fixture()
    payload = _issue_via_api(student, group)

    assert set(payload) == {"ticket", "expires_at", "websocket_path"}
    assert len(payload["ticket"]) >= 43
    assert "." not in payload["ticket"]
    assert payload["websocket_path"] == f"/ws/v1/groups/{group.pk}/chat/"
    assert 0 < (payload["expires_at"] - timezone.now()).total_seconds() <= 45

    accepted = async_to_sync(_connect)(f"{payload['websocket_path']}?ticket={payload['ticket']}")
    assert accepted == {"type": "websocket.accept", "subprotocol": None}

    rejected = async_to_sync(_connect)(f"{payload['websocket_path']}?ticket={payload['ticket']}")
    assert rejected == {"type": "websocket.close", "code": 4401}


@pytest.mark.django_db(transaction=True)
def test_chat_ticket_concurrent_consumption_has_exactly_one_winner_in_cache_unit_test():
    _admin, student, group = _build_chat_fixture()
    ticket = ChatHandshakeTicketService.issue(user=student, group=group)

    results = _concurrently_consume_ticket(ticket.token, group.pk)

    assert sum(result is not None for result in results) == 1
    assert sum(result is None for result in results) == 1


@pytest.mark.redis
@pytest.mark.django_db(transaction=True)
def test_chat_ticket_concurrent_consumption_has_exactly_one_winner_with_redis():
    """Exercise the production RedisCache ``SET ... NX`` primitive under a real race.

    This test is intentionally opt-in locally. CI supplies a disposable Redis URL;
    staging must run the same marker against its Redis topology before release.
    """

    redis_url = os.environ.get("PANORAMA_REDIS_TEST_URL")
    if not redis_url:
        pytest.skip("Set PANORAMA_REDIS_TEST_URL to run the real Redis concurrency test.")

    _admin, student, group = _build_chat_fixture()
    redis_cache = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": redis_url,
            "KEY_PREFIX": f"panorama-chat-ticket-test-{student.pk}",
        }
    }
    with override_settings(CACHES=redis_cache):
        ticket = ChatHandshakeTicketService.issue(user=student, group=group)
        results = _concurrently_consume_ticket(ticket.token, group.pk)
        cache.delete(ChatHandshakeTicketService._cache_key(ticket.token))
        cache.delete(ChatHandshakeTicketService._consumed_key(ticket.token))

    assert sum(result is not None for result in results) == 1
    assert sum(result is None for result in results) == 1


@pytest.mark.django_db(transaction=True)
def test_chat_ticket_is_bound_to_its_group_and_invalidated_by_session_revocation():
    admin, student, group = _build_chat_fixture()
    second_group = Group.objects.create(name="Other chat", university=group.university, created_by=admin)
    GroupMembership.objects.create(group=second_group, user=student, status=GroupMembershipStatus.APPROVED)
    payload = _issue_via_api(student, group)

    rejected = async_to_sync(_connect)(f"/ws/v1/groups/{second_group.pk}/chat/?ticket={payload['ticket']}")
    assert rejected == {"type": "websocket.close", "code": 4401}

    # A group-binding mismatch is rejected before the atomic reservation, so it
    # cannot let another route burn a valid ticket for its intended group.
    accepted = async_to_sync(_connect)(f"{payload['websocket_path']}?ticket={payload['ticket']}")
    assert accepted == {"type": "websocket.accept", "subprotocol": None}

    payload = _issue_via_api(student, group)
    student.invalidate_sessions()
    rejected = async_to_sync(_connect)(f"{payload['websocket_path']}?ticket={payload['ticket']}")
    assert rejected == {"type": "websocket.close", "code": 4401}


@pytest.mark.django_db(transaction=True)
def test_chat_ticket_rechecks_account_and_membership_state_after_issuance():
    _admin, student, group = _build_chat_fixture()
    payload = _issue_via_api(student, group)
    student.is_active = False
    student.save(update_fields=["is_active", "updated_at"])
    rejected = async_to_sync(_connect)(f"{payload['websocket_path']}?ticket={payload['ticket']}")
    assert rejected == {"type": "websocket.close", "code": 4401}

    student.is_active = True
    student.save(update_fields=["is_active", "updated_at"])
    membership = GroupMembership.objects.get(group=group, user=student)
    membership.status = GroupMembershipStatus.BLOCKED
    membership.save(update_fields=["status", "updated_at"])
    client = APIClient()
    client.force_authenticate(student)
    assert (
        client.post(f"/api/v1/groups/{group.pk}/chat-ticket/", format="json").status_code == status.HTTP_403_FORBIDDEN
    )


@pytest.mark.django_db(transaction=True)
def test_open_chat_socket_closes_on_next_action_after_membership_is_blocked():
    _admin, student, group = _build_chat_fixture()
    membership = GroupMembership.objects.get(group=group, user=student)
    payload = _issue_via_api(student, group)

    closed = async_to_sync(_connection_rejected_after_membership_block)(
        f"{payload['websocket_path']}?ticket={payload['ticket']}", membership.pk
    )
    assert closed == {"type": "websocket.close", "code": 4403}


@pytest.mark.django_db(transaction=True)
def test_open_chat_socket_closes_with_4401_on_next_action_after_session_revocation():
    _admin, student, group = _build_chat_fixture()
    payload = _issue_via_api(student, group)

    closed = async_to_sync(_connection_rejected_after_state_change)(
        f"{payload['websocket_path']}?ticket={payload['ticket']}",
        lambda: User.objects.filter(pk=student.pk).update(session_version=F("session_version") + 1),
    )

    assert closed == {"type": "websocket.close", "code": 4401}


@pytest.mark.django_db(transaction=True)
def test_open_chat_socket_closes_with_4401_on_next_action_after_account_deactivation():
    _admin, student, group = _build_chat_fixture()
    payload = _issue_via_api(student, group)

    closed = async_to_sync(_connection_rejected_after_state_change)(
        f"{payload['websocket_path']}?ticket={payload['ticket']}",
        lambda: User.objects.filter(pk=student.pk).update(is_active=False),
    )

    assert closed == {"type": "websocket.close", "code": 4401}


@pytest.mark.django_db(transaction=True)
def test_open_chat_socket_closes_with_4403_on_next_action_after_group_deactivation():
    _admin, student, group = _build_chat_fixture()
    payload = _issue_via_api(student, group)

    closed = async_to_sync(_connection_rejected_after_state_change)(
        f"{payload['websocket_path']}?ticket={payload['ticket']}",
        lambda: Group.objects.filter(pk=group.pk).update(is_active=False),
    )

    assert closed == {"type": "websocket.close", "code": 4403}


@pytest.mark.django_db(transaction=True)
def test_expired_or_malformed_chat_ticket_never_authenticates():
    _admin, student, group = _build_chat_fixture()
    ticket = ChatHandshakeTicketService.issue(user=student, group=group)
    cache.delete(ChatHandshakeTicketService._cache_key(ticket.token))

    rejected = async_to_sync(_connect)(f"/ws/v1/groups/{group.pk}/chat/?ticket={ticket.token}")
    assert rejected == {"type": "websocket.close", "code": 4401}
    rejected = async_to_sync(_connect)(f"/ws/v1/groups/{group.pk}/chat/?ticket=" + "a" * 257)
    assert rejected == {"type": "websocket.close", "code": 4401}


@pytest.mark.django_db
def test_chat_ticket_storage_failure_returns_a_safe_retryable_error():
    _admin, student, group = _build_chat_fixture()
    client = APIClient()
    client.force_authenticate(student)
    with (
        patch("apps.chat.views.ChatTicketRateThrottle.allow_request", return_value=True),
        patch("apps.chat.services.cache.set", side_effect=RedisConnectionError("unavailable")),
    ):
        response = client.post(f"/api/v1/groups/{group.pk}/chat-ticket/", format="json")

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.data["code"] == "CHAT_TICKET_SERVICE_UNAVAILABLE"


@pytest.mark.django_db(transaction=True)
def test_chat_ticket_storage_failure_during_handshake_fails_closed():
    _admin, _student, group = _build_chat_fixture()

    with patch("apps.chat.services.cache.get", side_effect=RedisConnectionError("unavailable")):
        rejected = async_to_sync(_connect)(f"/ws/v1/groups/{group.pk}/chat/?ticket=opaque-ticket")

    assert rejected == {"type": "websocket.close", "code": 4401}


@pytest.mark.django_db
def test_chat_ticket_endpoint_enforces_its_user_throttle():
    _admin, student, group = _build_chat_fixture()
    cache.clear()
    client = APIClient()
    client.force_authenticate(student)

    with patch.object(ChatTicketRateThrottle, "THROTTLE_RATES", {"chat_ticket": "1/min"}):
        first = client.post(f"/api/v1/groups/{group.pk}/chat-ticket/", format="json")
        limited = client.post(f"/api/v1/groups/{group.pk}/chat-ticket/", format="json")

    assert first.status_code == status.HTTP_201_CREATED
    assert limited.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert limited.data["code"] == "RATE_LIMITED"


@override_settings(WEBSOCKET_LEGACY_JWT_QUERY_AUTH_ENABLED=True)
@pytest.mark.django_db(transaction=True)
def test_legacy_query_jwt_is_temporarily_supported_only_when_explicitly_enabled():
    _admin, student, group = _build_chat_fixture()
    access = str(SessionVersionRefreshToken.for_user(student).access_token)

    accepted = async_to_sync(_connect)(f"/ws/v1/groups/{group.pk}/chat/?token={access}")
    assert accepted == {"type": "websocket.accept", "subprotocol": None}
