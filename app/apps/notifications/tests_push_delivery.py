from __future__ import annotations

import json
import logging
from datetime import timedelta
from email.message import Message
from unittest import mock
from urllib.error import HTTPError, URLError

import pytest
from celery.exceptions import Retry
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.notifications.models import DeviceToken, Notification
from apps.notifications.services import PushDeliveryTransientError, PushNotificationService
from apps.notifications.tasks import deliver_push_notification
from apps.product.models import DeviceInstallation

pytestmark = pytest.mark.django_db

URLOPEN = "apps.notifications.services.urllib_request.urlopen"
SETTINGS = {
    "EXPO_PUSH_ENDPOINT": "https://exp.host/--/api/v2/push/send",
    "EXPO_PUSH_ALLOWED_HOSTS": frozenset({"exp.host"}),
}
TOKEN_A = "ExponentPushToken[aaaaaaaaaaaaaaaaaaaaaa]"
TOKEN_B = "ExponentPushToken[bbbbbbbbbbbbbbbbbbbbbb]"
TOKEN_C = "ExponentPushToken[cccccccccccccccccccccc]"


class FakeResponse:
    def __init__(self, payload=None, status=200, raw=None):
        self.status = status
        self._raw = raw if raw is not None else json.dumps(payload).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def ok(n=1):
    return [{"status": "ok", "id": f"id-{i}"} for i in range(n)]


def not_registered():
    return {"status": "error", "message": "gone", "details": {"error": "DeviceNotRegistered"}}


@pytest.fixture
def user():
    return User.objects.create_user(
        full_name="Push User", email="push@example.test", phone_number="+963955800001", password="StrongPass123!"
    )


def _device_token(user, token, **extra):
    return DeviceToken.objects.create(user=user, token=token, platform="android", **extra)


def _installation(user, token, n=1):
    return DeviceInstallation.objects.create(
        user=user,
        installation_id=f"00000000-0000-4000-8000-{n:012d}",
        platform="android",
        push_token=token,
    )


@override_settings(**SETTINGS)
def test_device_not_registered_deactivates_token_in_both_stores_and_keeps_good_ones(user):
    dead = _device_token(user, TOKEN_A)
    good = _device_token(user, TOKEN_B)
    installation = _installation(user, TOKEN_A.replace("aaaa", "dddd"))
    dead_install_token = installation.push_token
    tokens = PushNotificationService.active_tokens([user.pk], limit=100)
    assert set(tokens) == {TOKEN_A, TOKEN_B, dead_install_token}
    tickets = [not_registered() if t in {TOKEN_A, dead_install_token} else ok()[0] for t in tokens]
    with mock.patch(URLOPEN, return_value=FakeResponse({"data": tickets})):
        assert PushNotificationService._send_expo(tokens, "T", "B") == 1
    dead.refresh_from_db()
    good.refresh_from_db()
    installation.refresh_from_db()
    assert dead.is_active is False and good.is_active is True
    assert installation.push_token is None and installation.revoked_at is None  # installation itself is kept
    assert PushNotificationService.active_tokens([user.pk], limit=100) == [TOKEN_B]


@override_settings(**SETTINGS)
def test_other_ticket_errors_are_logged_without_tokens_and_do_not_deactivate(user, caplog):
    token = _device_token(user, TOKEN_A)
    ticket = {"status": "error", "message": f"{TOKEN_A} too big", "details": {"error": "MessageTooBig"}}
    with caplog.at_level(logging.WARNING, logger="apps.notifications.services"):
        with mock.patch(URLOPEN, return_value=FakeResponse({"data": [ticket]})):
            assert PushNotificationService._send_expo([TOKEN_A], "T", "B") == 0
    token.refresh_from_db()
    assert token.is_active is True
    record = next(r for r in caplog.records if r.getMessage() == "Push delivery finished with errors")
    assert record.error_codes == {"MessageTooBig": 1}
    assert TOKEN_A not in caplog.text and TOKEN_A not in str(record.__dict__)


@override_settings(**SETTINGS)
def test_partial_failure_in_a_batch_counts_only_accepted(user):
    _device_token(user, TOKEN_A)
    _device_token(user, TOKEN_B)
    _device_token(user, TOKEN_C)
    tickets = [ok()[0], not_registered(), ok()[0]]
    with mock.patch(URLOPEN, return_value=FakeResponse({"data": tickets})):
        assert PushNotificationService._send_expo([TOKEN_A, TOKEN_B, TOKEN_C], "T", "B") == 2
    assert list(DeviceToken.objects.filter(is_active=False).values_list("token", flat=True)) == [TOKEN_B]


@override_settings(**SETTINGS)
def test_tokens_are_sent_in_batches_of_100():
    tokens = [f"ExponentPushToken[{i:022d}]" for i in range(250)]

    def respond(request, timeout):
        return FakeResponse({"data": ok(len(json.loads(request.data)))})

    with mock.patch(URLOPEN, side_effect=respond) as urlopen:
        assert PushNotificationService._send_expo(tokens, "T", "B") == 250
    assert [len(json.loads(call.args[0].data)) for call in urlopen.call_args_list] == [100, 100, 50]


@override_settings(**SETTINGS)
@pytest.mark.parametrize(
    "failure",
    [
        HTTPError("https://exp.host", 503, "unavailable", Message(), None),
        HTTPError("https://exp.host", 429, "slow down", Message(), None),
        URLError("dns"),
        TimeoutError("timed out"),
    ],
)
def test_transient_http_failures_raise_with_exactly_the_tokens_to_retry(failure):
    with mock.patch(URLOPEN, side_effect=failure):
        with pytest.raises(PushDeliveryTransientError) as excinfo:
            PushNotificationService._send_expo([TOKEN_A, TOKEN_B], "T", "B")
    assert excinfo.value.tokens == [TOKEN_A, TOKEN_B]


@override_settings(**SETTINGS)
def test_permanent_http_error_is_not_retried_and_keeps_tokens(user, caplog):
    token = _device_token(user, TOKEN_A)
    error = HTTPError("https://exp.host", 400, "bad", Message(), None)
    with mock.patch(URLOPEN, side_effect=error):
        assert PushNotificationService._send_expo([TOKEN_A], "T", "B") == 0
    token.refresh_from_db()
    assert token.is_active is True


@override_settings(**SETTINGS)
def test_rate_exceeded_ticket_is_retried_for_that_token_only_after_dead_ones_are_cleaned(user):
    _device_token(user, TOKEN_A)
    _device_token(user, TOKEN_B)
    _device_token(user, TOKEN_C)
    tickets = [ok()[0], {"status": "error", "details": {"error": "MessageRateExceeded"}}, not_registered()]
    with mock.patch(URLOPEN, return_value=FakeResponse({"data": tickets})):
        with pytest.raises(PushDeliveryTransientError) as excinfo:
            PushNotificationService._send_expo([TOKEN_A, TOKEN_B, TOKEN_C], "T", "B")
    assert excinfo.value.tokens == [TOKEN_B]
    assert DeviceToken.objects.get(token=TOKEN_C).is_active is False
    assert DeviceToken.objects.get(token=TOKEN_B).is_active is True


@override_settings(**SETTINGS)
def test_unusable_response_body_is_treated_as_accepted_without_changing_tokens(user):
    token = _device_token(user, TOKEN_A)
    with mock.patch(URLOPEN, return_value=FakeResponse(raw=b"<html>not json</html>")):
        assert PushNotificationService._send_expo([TOKEN_A], "T", "B") == 1
    with mock.patch(URLOPEN, return_value=FakeResponse({"data": ok(5)})):  # length mismatch
        assert PushNotificationService._send_expo([TOKEN_A], "T", "B") == 1
    token.refresh_from_db()
    assert token.is_active is True


def test_sending_uses_one_deduplicated_union_of_both_token_stores(user):
    _device_token(user, TOKEN_A)
    _installation(user, TOKEN_A, n=1)  # same token registered in both stores
    _installation(user, TOKEN_B, n=2)
    muted = _installation(user, TOKEN_C, n=3)
    DeviceInstallation.objects.filter(pk=muted.pk).update(notifications_enabled=False)  # opted out: not sent to
    other = User.objects.create_user(
        full_name="Other", email="other-push@example.test", phone_number="+963955800002", password="StrongPass123!"
    )
    DeviceToken.objects.create(user=other, token="ExponentPushToken[other-user-token-xx]", platform="ios")
    with mock.patch.object(PushNotificationService, "_send_expo", return_value=2) as send:
        assert PushNotificationService.send_to_user(user, "T", "B") is True
    assert send.call_args.args[0] == [TOKEN_A, TOKEN_B]
    with mock.patch.object(PushNotificationService, "_send_expo", return_value=2) as send:
        PushNotificationService.send_to_users([user, other], "T", "B")
    assert sorted(send.call_args.args[0]) == sorted([TOKEN_A, TOKEN_B, "ExponentPushToken[other-user-token-xx]"])


@override_settings(**SETTINGS)
def test_task_retries_only_the_failed_tokens_with_backoff(user):
    _device_token(user, TOKEN_A)
    _device_token(user, TOKEN_B)

    def respond(request, timeout):
        sent = [m["to"] for m in json.loads(request.data)]
        tickets = [
            {"status": "error", "details": {"error": "MessageRateExceeded"}} if to == TOKEN_B else ok()[0]
            for to in sent
        ]
        return FakeResponse({"data": tickets})

    with mock.patch(URLOPEN, side_effect=respond):
        with mock.patch.object(deliver_push_notification, "retry", side_effect=Retry()) as retry:
            with pytest.raises(Retry):
                deliver_push_notification.run(user.id, "T", "B", {"k": 1})
    kwargs = retry.call_args.kwargs
    assert kwargs["kwargs"]["only_tokens"] == [TOKEN_B]
    assert kwargs["countdown"] == 30
    assert isinstance(kwargs["exc"], PushDeliveryTransientError)

    # The retry run only targets that token.
    with mock.patch(URLOPEN, return_value=FakeResponse({"data": ok()})) as urlopen:
        deliver_push_notification.run(user.id, "T", "B", {"k": 1}, only_tokens=[TOKEN_B])
    sent_to = [m["to"] for m in json.loads(urlopen.call_args.args[0].data)]
    assert sent_to == [TOKEN_B]


def test_unread_count_excludes_expired_notifications_like_the_list(user):
    client = APIClient()
    client.force_authenticate(user)
    now = timezone.now()
    Notification.objects.create(user=user, title="live", body="b")
    Notification.objects.create(user=user, title="future", body="b", expires_at=now + timedelta(days=1))
    Notification.objects.create(user=user, title="expired", body="b", expires_at=now - timedelta(minutes=1))
    count = client.get("/api/v1/notifications/unread-count/").json()["data"]["count"]
    listed = client.get("/api/v1/notifications/").json()["data"]["count"]
    assert count == 2 == listed
