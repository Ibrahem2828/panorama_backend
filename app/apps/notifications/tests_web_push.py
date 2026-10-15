from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from unittest import mock

import pytest
import requests
from celery.exceptions import Retry
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.test import override_settings
from pywebpush import WebPushException
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.common.throttles import WebPushRateThrottle

from .models import DeviceToken, Notification, NotificationPreference, WebPushSubscription
from .services import (
    NotificationService,
    PushDeliveryTransientError,
    PushNotificationService,
    WebPushService,
    push_channels,
)
from .tasks import deliver_push_notification
from .webpush_security import InvalidWebPushInput, validate_endpoint, validate_key

pytestmark = pytest.mark.django_db

WEBPUSH = "apps.notifications.services.webpush"
URL_PREFIX = "/api/v1/notifications/web-push/"
ENDPOINT = "https://fcm.googleapis.com/fcm/send/endpoint-one"
ENDPOINT_2 = "https://updates.push.services.mozilla.com/wpush/v2/endpoint-two"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _keys() -> dict[str, str]:
    return {"p256dh": _b64(b"\x04" + bytes(range(64))), "auth": _b64(bytes(range(16)))}


def _vapid_pair() -> tuple[str, str]:
    private = ec.generate_private_key(ec.SECP256R1())
    raw_private = private.private_numbers().private_value.to_bytes(32, "big")
    raw_public = private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return _b64(raw_private), _b64(raw_public)


PRIVATE_KEY, PUBLIC_KEY = _vapid_pair()
VAPID = {
    "WEB_PUSH_VAPID_PUBLIC_KEY": PUBLIC_KEY,
    "WEB_PUSH_VAPID_PRIVATE_KEY": PRIVATE_KEY,
    "WEB_PUSH_VAPID_SUBJECT": "mailto:ops@example.test",
    "WEB_PUSH_ENABLED": True,
}


@pytest.fixture(autouse=True)
def _vapid_configured():
    with override_settings(**VAPID):
        yield


def _make_user(n: int) -> User:
    return User.objects.create_user(
        full_name=f"Web Push {n}",
        email=f"webpush{n}@example.test",
        phone_number=f"+96395590{n:04d}",
        password="StrongPass123!",
    )


@pytest.fixture
def user():
    return _make_user(1)


@pytest.fixture
def other():
    return _make_user(2)


@pytest.fixture
def client(user):
    api_client = APIClient()
    api_client.force_authenticate(user)
    return api_client


def _subscription(user, endpoint=ENDPOINT, **extra) -> WebPushSubscription:
    keys = _keys()
    return WebPushSubscription.objects.create(
        user=user, endpoint=endpoint, p256dh=keys["p256dh"], auth=keys["auth"], **extra
    )


def _push_error(status: int | None) -> WebPushException:
    response = SimpleNamespace(status_code=status, headers={}, text="") if status is not None else None
    return WebPushException("Push failed", response=response)


# --------------------------------------------------------------------------------------------- public key


def test_public_key_requires_authentication():
    assert APIClient().get(f"{URL_PREFIX}public-key/").status_code == 401


def test_public_key_enabled_returns_key(client):
    response = client.get(f"{URL_PREFIX}public-key/")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"] == {"enabled": True, "public_key": PUBLIC_KEY}
    assert PRIVATE_KEY not in response.content.decode()


@pytest.mark.parametrize(
    "missing", ["WEB_PUSH_VAPID_PUBLIC_KEY", "WEB_PUSH_VAPID_PRIVATE_KEY", "WEB_PUSH_VAPID_SUBJECT"]
)
def test_public_key_disabled_when_any_setting_missing(client, missing):
    # WEB_PUSH_ENABLED is derived from the three settings in base.py; mirror that derivation here.
    values = {**VAPID, missing: ""}
    values["WEB_PUSH_ENABLED"] = all(values[name] for name in list(VAPID)[:3])
    with override_settings(**values):
        data = client.get(f"{URL_PREFIX}public-key/").json()["data"]
    assert data == {"enabled": False, "public_key": None}


def test_settings_derive_enabled_only_when_all_three_are_set():
    from config.settings import base

    assert base.WEB_PUSH_ENABLED is bool(
        base.WEB_PUSH_VAPID_PUBLIC_KEY and base.WEB_PUSH_VAPID_PRIVATE_KEY and base.WEB_PUSH_VAPID_SUBJECT
    )


# ------------------------------------------------------------------------------------------ subscriptions


def _body(endpoint=ENDPOINT, **extra):
    return {"endpoint": endpoint, "keys": _keys(), **extra}


def test_subscribe_creates_subscription_without_echoing_secrets(client, user):
    response = client.post(f"{URL_PREFIX}subscriptions/", _body(user_agent="Mozilla/5.0 Test"), format="json")
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True and body["code"] == "WEB_PUSH_SUBSCRIBED"
    assert set(body["data"]) == {"id", "user_agent", "is_active", "last_used_at", "created_at", "updated_at"}
    text = response.content.decode()
    assert "fcm.googleapis.com" not in text and _keys()["auth"] not in text
    subscription = WebPushSubscription.objects.get()
    assert subscription.user == user and subscription.is_active and subscription.user_agent == "Mozilla/5.0 Test"
    assert subscription.p256dh == _keys()["p256dh"]


def test_subscribe_falls_back_to_the_user_agent_header(client):
    client.post(f"{URL_PREFIX}subscriptions/", _body(), format="json", HTTP_USER_AGENT="HeaderAgent/1")
    assert WebPushSubscription.objects.get().user_agent == "HeaderAgent/1"


def test_subscribe_is_an_upsert_by_endpoint_and_reactivates(client, user):
    first = client.post(f"{URL_PREFIX}subscriptions/", _body(), format="json").json()["data"]["id"]
    WebPushSubscription.objects.update(is_active=False, failure_count=4)
    second = client.post(f"{URL_PREFIX}subscriptions/", _body(), format="json").json()["data"]["id"]
    assert first == second and WebPushSubscription.objects.count() == 1
    subscription = WebPushSubscription.objects.get()
    assert subscription.is_active is True and subscription.failure_count == 0


def test_subscribe_reassigns_the_endpoint_to_the_current_user(client, user, other):
    _subscription(other)
    response = client.post(f"{URL_PREFIX}subscriptions/", _body(), format="json")
    assert response.status_code == 201
    assert WebPushSubscription.objects.get().user == user
    assert not WebPushSubscription.objects.filter(user=other).exists()


def test_subscribe_revives_a_soft_deleted_subscription(client, user):
    _subscription(user, is_deleted=True, is_active=False)
    assert client.post(f"{URL_PREFIX}subscriptions/", _body(), format="json").status_code == 201
    subscription = WebPushSubscription.objects.get()
    assert subscription.is_deleted is False and subscription.is_active is True


def test_subscribe_requires_authentication():
    assert APIClient().post(f"{URL_PREFIX}subscriptions/", _body(), format="json").status_code == 401


def test_subscribe_is_unavailable_when_not_configured(client):
    with override_settings(WEB_PUSH_ENABLED=False):
        response = client.post(f"{URL_PREFIX}subscriptions/", _body(), format="json")
    assert response.status_code == 503
    assert response.json()["code"] == "WEB_PUSH_NOT_CONFIGURED"
    assert not WebPushSubscription.objects.exists()


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://fcm.googleapis.com/fcm/send/x",  # not https
        "https://127.0.0.1/push",
        "https://[::1]/push",
        "https://10.0.0.5/push",
        "https://169.254.169.254/latest/meta-data",
        "https://localhost/push",
        "https://intranet.internal/push",
        "https://evil.example.com/push",  # not an allowlisted push service
        "https://fcm.googleapis.com.evil.example.com/push",  # suffix trick
        "https://evilfcm.googleapis.com.attacker.test/push",
        "https://user:pass@fcm.googleapis.com/push",
        "https://fcm.googleapis.com:8443/push",
        "https://fcm.googleapis.com/push#frag",
        "ftp://fcm.googleapis.com/push",
        "not a url",
        "https://fcm.googleapis.com/" + "a" * 1100,
        "",
    ],
)
def test_subscribe_rejects_unsafe_or_unsupported_endpoints(client, endpoint):
    response = client.post(f"{URL_PREFIX}subscriptions/", _body(endpoint=endpoint), format="json")
    assert response.status_code == 400
    assert response.json()["success"] is False
    assert not WebPushSubscription.objects.exists()


@pytest.mark.parametrize(
    "keys",
    [
        {"p256dh": "not base64!", "auth": _keys()["auth"]},
        {"p256dh": _keys()["p256dh"], "auth": "###"},
        {"p256dh": _b64(b"\x04" * 10), "auth": _keys()["auth"]},  # wrong length
        {"p256dh": _keys()["p256dh"], "auth": _b64(b"x" * 32)},  # wrong length
        {"p256dh": _keys()["p256dh"]},
        {},
    ],
)
def test_subscribe_validates_keys(client, keys):
    response = client.post(f"{URL_PREFIX}subscriptions/", {"endpoint": ENDPOINT, "keys": keys}, format="json")
    assert response.status_code == 400
    assert not WebPushSubscription.objects.exists()


def test_subscribe_requires_keys_object(client):
    assert client.post(f"{URL_PREFIX}subscriptions/", {"endpoint": ENDPOINT}, format="json").status_code == 400


def test_subscribe_accepts_padded_base64url_keys(client):
    keys = {name: value + "=" * (-len(value) % 4) for name, value in _keys().items()}
    response = client.post(f"{URL_PREFIX}subscriptions/", {"endpoint": ENDPOINT, "keys": keys}, format="json")
    assert response.status_code == 201
    assert WebPushSubscription.objects.get().auth == _keys()["auth"]  # stored unpadded


def test_new_push_vendor_can_be_allowed_by_setting(client):
    endpoint = "https://push.newvendor.test/send/abc"
    assert client.post(f"{URL_PREFIX}subscriptions/", _body(endpoint), format="json").status_code == 400
    with override_settings(WEB_PUSH_ALLOWED_HOST_SUFFIXES=("newvendor.test", "fcm.googleapis.com")):
        assert client.post(f"{URL_PREFIX}subscriptions/", _body(endpoint), format="json").status_code == 201


def test_default_allowlist_covers_the_major_push_services():
    from config.settings import base

    for url in (
        "https://fcm.googleapis.com/fcm/send/a",
        "https://updates.push.services.mozilla.com/wpush/v2/a",
        "https://web.push.apple.com/a",
        "https://wns2-par02p.notify.windows.com/w/?token=a",
    ):
        with override_settings(WEB_PUSH_ALLOWED_HOST_SUFFIXES=base.WEB_PUSH_ALLOWED_HOST_SUFFIXES):
            assert validate_endpoint(url) == url


def test_empty_allowlist_still_blocks_private_targets():
    with override_settings(WEB_PUSH_ALLOWED_HOST_SUFFIXES=()):
        assert validate_endpoint("https://push.example.org/abc")
        for bad in (
            "https://192.168.1.1/x",
            "https://localhost/x",
            "https://printer.local/x",
            "http://push.example.org",
        ):
            with pytest.raises(InvalidWebPushInput):
                validate_endpoint(bad)


def test_validate_key_rejects_garbage():
    with pytest.raises(InvalidWebPushInput):
        validate_key("a", raw_length=16, field="auth")  # invalid base64 length
    with pytest.raises(InvalidWebPushInput):
        validate_key("", raw_length=16, field="auth")


def test_subscribe_is_throttled(client, monkeypatch):
    monkeypatch.setattr(WebPushRateThrottle, "THROTTLE_RATES", {"web_push": "2/min"})
    codes = [client.post(f"{URL_PREFIX}subscriptions/", _body(), format="json").status_code for _ in range(3)]
    assert codes == [201, 201, 429]


def test_unsubscribe_removes_only_own_subscription(client, user, other):
    _subscription(user)
    mine = client.delete(f"{URL_PREFIX}subscriptions/", {"endpoint": ENDPOINT}, format="json")
    assert mine.status_code == 200 and mine.json()["code"] == "WEB_PUSH_UNSUBSCRIBED"
    subscription = WebPushSubscription.objects.get()
    assert subscription.is_deleted is True and subscription.is_active is False
    assert WebPushService.active_subscriptions(user.pk) == []
    # A second delete finds nothing.
    assert client.delete(f"{URL_PREFIX}subscriptions/", {"endpoint": ENDPOINT}, format="json").status_code == 404


def test_unsubscribe_cannot_touch_another_users_subscription(client, other):
    _subscription(other, ENDPOINT_2)
    response = client.delete(f"{URL_PREFIX}subscriptions/", {"endpoint": ENDPOINT_2}, format="json")
    assert response.status_code == 404
    subscription = WebPushSubscription.objects.get()
    assert subscription.is_active is True and subscription.is_deleted is False


def test_unsubscribe_requires_endpoint_and_authentication(client):
    assert client.delete(f"{URL_PREFIX}subscriptions/", {}, format="json").status_code == 400
    assert APIClient().delete(f"{URL_PREFIX}subscriptions/", {"endpoint": ENDPOINT}, format="json").status_code == 401


# ------------------------------------------------------------------------------------------------ delivery


def test_payload_contains_title_body_data_tag_and_a_same_origin_url():
    payload = json.loads(
        WebPushService.build_payload(
            "عنوان", "نص", {"deep_link": "/verification", "notification_id": 7, "type": "verification", "x": 1}
        )
    )
    assert payload["title"] == "عنوان" and payload["body"] == "نص"
    assert payload["tag"] == "panorama-verification-7"
    assert payload["data"] == {
        "deep_link": "/verification",
        "url": "/verification",
        "notification_id": 7,
        "type": "verification",
        "x": 1,
    }


@pytest.mark.parametrize("link", ["https://evil.example/x", "//evil.example", "javascript:alert(1)", ""])
def test_payload_url_never_leaves_the_origin(link):
    payload = json.loads(WebPushService.build_payload("t", "b", {"deep_link": link}))
    assert payload["data"]["url"] == "/notifications"
    assert payload["tag"] == "panorama-notification"


def test_delivery_success_calls_pywebpush_with_vapid_and_updates_the_subscription(user):
    subscription = _subscription(user, failure_count=3)
    with mock.patch(WEBPUSH) as push:
        assert PushNotificationService.send_to_user(user, "T", "B", {"notification_id": 1, "type": "system"}) is True
    kwargs = push.call_args.kwargs
    assert kwargs["subscription_info"] == {
        "endpoint": ENDPOINT,
        "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
    }
    assert kwargs["vapid_private_key"] == PRIVATE_KEY
    assert kwargs["vapid_claims"] == {"sub": "mailto:ops@example.test"}
    assert kwargs["ttl"] > 0 and kwargs["timeout"] == 5
    assert kwargs["requests_session"].max_redirects == 0
    assert json.loads(kwargs["data"])["title"] == "T"
    subscription.refresh_from_db()
    assert subscription.last_used_at is not None and subscription.failure_count == 0


def test_delivery_really_encrypts_and_signs_for_a_real_browser_key(user):
    """No pywebpush mock: only the HTTP layer is replaced, so key handling, aes128gcm and VAPID are exercised."""

    browser_key = ec.generate_private_key(ec.SECP256R1())
    p256dh = _b64(
        browser_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    )
    WebPushSubscription.objects.create(user=user, endpoint=ENDPOINT, p256dh=p256dh, auth=_b64(bytes(range(16))))
    response = mock.Mock(status_code=201, text="", headers={})
    with mock.patch("requests.Session.post", return_value=response) as post:
        assert WebPushService.send_to_user(user, "Title", "Body", {"type": "system"}) == 1
    assert post.call_args.args[0] == ENDPOINT
    headers = post.call_args.kwargs["headers"]
    assert headers["content-encoding"] == "aes128gcm"
    assert headers["authorization"].startswith("vapid t=")
    assert post.call_args.kwargs["timeout"] == 5
    assert PRIVATE_KEY not in json.dumps({k: str(v) for k, v in headers.items()})


@pytest.mark.parametrize("status", [404, 410])
def test_gone_subscriptions_are_deactivated_and_not_retried(user, status):
    gone = _subscription(user, ENDPOINT)
    alive = _subscription(user, ENDPOINT_2)

    def fake(**kwargs):
        if kwargs["subscription_info"]["endpoint"] == ENDPOINT:
            raise _push_error(status)

    with mock.patch(WEBPUSH, side_effect=fake):
        assert PushNotificationService.send_to_user(user, "T", "B") is True  # no PushDeliveryTransientError
    gone.refresh_from_db()
    alive.refresh_from_db()
    assert gone.is_active is False and alive.is_active is True and alive.last_used_at is not None


@pytest.mark.parametrize(
    "failure",
    [
        _push_error(429),
        _push_error(500),
        _push_error(503),
        requests.exceptions.ConnectTimeout("slow"),
        requests.exceptions.ConnectionError("down"),
        TimeoutError("timed out"),
    ],
)
def test_transient_failures_retry_only_the_failed_endpoint(user, failure):
    failing = _subscription(user, ENDPOINT)
    fine = _subscription(user, ENDPOINT_2)

    def fake(**kwargs):
        if kwargs["subscription_info"]["endpoint"] == ENDPOINT:
            raise failure

    with mock.patch(WEBPUSH, side_effect=fake):
        with pytest.raises(PushDeliveryTransientError) as excinfo:
            PushNotificationService.send_to_user(user, "T", "B")
    assert excinfo.value.tokens == [ENDPOINT]
    failing.refresh_from_db()
    fine.refresh_from_db()
    assert failing.is_active is True and failing.failure_count == 0  # transient: not counted
    assert fine.last_used_at is not None


def test_task_retries_only_failed_web_endpoint_with_backoff_and_no_duplicates(user):
    _subscription(user, ENDPOINT)
    _subscription(user, ENDPOINT_2)

    def flaky(**kwargs):
        if kwargs["subscription_info"]["endpoint"] == ENDPOINT:
            raise _push_error(503)

    with mock.patch(WEBPUSH, side_effect=flaky):
        with mock.patch.object(deliver_push_notification, "retry", side_effect=Retry()) as retry:
            with pytest.raises(Retry):
                deliver_push_notification.run(user.id, "T", "B", {"k": 1}, channels=["web"])
    kwargs = retry.call_args.kwargs
    assert kwargs["kwargs"]["only_tokens"] == [ENDPOINT]
    assert kwargs["kwargs"]["channels"] == ["web"]
    assert kwargs["countdown"] == 30

    with mock.patch(WEBPUSH) as push:
        deliver_push_notification.run(user.id, "T", "B", {"k": 1}, only_tokens=[ENDPOINT], channels=["web"])
    assert [c.kwargs["subscription_info"]["endpoint"] for c in push.call_args_list] == [ENDPOINT]


@pytest.mark.parametrize("status", [400, 401, 403, 413])
def test_other_4xx_count_failures_and_deactivate_after_the_limit(user, status):
    subscription = _subscription(user)
    with override_settings(WEB_PUSH_MAX_CONSECUTIVE_FAILURES=3):
        with mock.patch(WEBPUSH, side_effect=_push_error(status)):
            for attempt in range(1, 4):
                assert WebPushService.send_to_user(user, "T", "B") == 0  # permanent: never raises
                subscription.refresh_from_db()
                assert subscription.failure_count == attempt
                assert subscription.is_active is (attempt < 3)


def test_a_success_resets_the_consecutive_failure_count(user):
    subscription = _subscription(user, failure_count=2)
    with mock.patch(WEBPUSH):
        assert WebPushService.send_to_user(user, "T", "B") == 1
    subscription.refresh_from_db()
    assert subscription.failure_count == 0 and subscription.is_active is True


def test_redirects_and_invalid_key_material_count_as_failures_not_retries(user):
    subscription = _subscription(user)
    with mock.patch(WEBPUSH, side_effect=requests.exceptions.TooManyRedirects("redirect")):
        assert WebPushService.send_to_user(user, "T", "B") == 0
    with mock.patch(WEBPUSH, side_effect=ValueError("bad key")):
        assert WebPushService.send_to_user(user, "T", "B") == 0
    with mock.patch(WEBPUSH, side_effect=_push_error(None)):
        assert WebPushService.send_to_user(user, "T", "B") == 0
    subscription.refresh_from_db()
    assert subscription.failure_count == 3


def test_failures_are_logged_without_endpoints_or_keys(user, caplog):
    subscription = _subscription(user)
    with mock.patch(WEBPUSH, side_effect=_push_error(400)):
        with caplog.at_level("WARNING", logger="apps.notifications.services"):
            WebPushService.send_to_user(user, "T", "B")
    assert "web_push_delivery_errors" in str([r.__dict__.get("event") for r in caplog.records])
    assert "fcm.googleapis.com" not in caplog.text
    assert subscription.auth not in caplog.text and PRIVATE_KEY not in caplog.text


def test_endpoint_that_no_longer_passes_the_allowlist_is_deactivated_not_called(user):
    subscription = _subscription(user)
    with override_settings(WEB_PUSH_ALLOWED_HOST_SUFFIXES=("only.example.test",)):
        with mock.patch(WEBPUSH) as push:
            assert WebPushService.send_to_user(user, "T", "B") == 0
    push.assert_not_called()
    subscription.refresh_from_db()
    assert subscription.is_active is False


def test_ssrf_endpoints_in_the_database_are_never_posted_to(user):
    for _, endpoint in enumerate(("https://127.0.0.1/x", "https://169.254.169.254/x", "http://fcm.googleapis.com/x")):
        _subscription(user, endpoint)
    with mock.patch(WEBPUSH) as push, mock.patch("requests.Session.post") as post:
        assert WebPushService.send_to_user(user, "T", "B") == 0
    push.assert_not_called()
    post.assert_not_called()
    assert WebPushSubscription.objects.filter(is_active=True).count() == 0


def test_only_the_users_own_active_subscriptions_receive_the_push(user, other):
    _subscription(user, ENDPOINT)
    _subscription(user, ENDPOINT_2, is_active=False)
    _subscription(other, "https://fcm.googleapis.com/fcm/send/other")
    _subscription(user, "https://fcm.googleapis.com/fcm/send/deleted", is_deleted=True)
    with mock.patch(WEBPUSH) as push:
        WebPushService.send_to_user(user, "T", "B")
    assert [c.kwargs["subscription_info"]["endpoint"] for c in push.call_args_list] == [ENDPOINT]


def test_web_delivery_is_skipped_when_not_configured(user):
    _subscription(user)
    with override_settings(WEB_PUSH_ENABLED=False):
        with mock.patch(WEBPUSH) as push:
            assert PushNotificationService.send_to_user(user, "T", "B") is False
    push.assert_not_called()


# ------------------------------------------------------------------------------- gating, preferences, expo


def test_channels_follow_configuration():
    with override_settings(PUSH_NOTIFICATIONS_ENABLED=False, WEB_PUSH_ENABLED=False):
        assert push_channels() == []
    with override_settings(PUSH_NOTIFICATIONS_ENABLED=False, WEB_PUSH_ENABLED=True):
        assert push_channels() == ["web"]
    with override_settings(PUSH_NOTIFICATIONS_ENABLED=True, WEB_PUSH_ENABLED=False):
        assert push_channels() == ["expo"]
    with override_settings(PUSH_NOTIFICATIONS_ENABLED=True, WEB_PUSH_ENABLED=True):
        assert push_channels() == ["expo", "web"]


def test_web_push_is_queued_even_when_expo_is_disabled(user, django_capture_on_commit_callbacks):
    with override_settings(PUSH_NOTIFICATIONS_ENABLED=False):
        with mock.patch("apps.notifications.tasks.deliver_push_notification.delay") as delay:
            with django_capture_on_commit_callbacks(execute=True):
                notification = NotificationService.create_notification(
                    user, "T", "B", type="verification", data={"status": "approved"}, deep_link="/verification"
                )
    delay.assert_called_once()
    assert delay.call_args.args[:3] == (user.id, "T", "B")
    assert delay.call_args.args[3] == {
        "status": "approved",
        "notification_id": notification.pk,
        "type": "verification",
        "deep_link": "/verification",
    }
    assert delay.call_args.kwargs == {"channels": ["web"]}


def test_nothing_is_queued_when_no_channel_is_configured(user, django_capture_on_commit_callbacks):
    with override_settings(PUSH_NOTIFICATIONS_ENABLED=False, WEB_PUSH_ENABLED=False):
        with mock.patch("apps.notifications.tasks.deliver_push_notification.delay") as delay:
            with django_capture_on_commit_callbacks(execute=True):
                NotificationService.create_notification(user, "T", "B")
                NotificationService.create_bulk_notifications([user], "T", "B")
    delay.assert_not_called()
    assert Notification.objects.filter(user=user).count() == 2  # in-app notifications are unaffected


def test_push_enabled_preference_is_honoured_for_web(user, django_capture_on_commit_callbacks):
    NotificationPreference.objects.create(user=user, push_enabled=False)
    with mock.patch("apps.notifications.tasks.deliver_push_notification.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            assert NotificationService.create_notification(user, "T", "B") is not None
    delay.assert_not_called()


def test_disabled_types_suppress_the_notification_and_its_push(user, django_capture_on_commit_callbacks):
    NotificationPreference.objects.create(user=user, disabled_types=["chat"])
    with mock.patch("apps.notifications.tasks.deliver_push_notification.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            assert NotificationService.create_notification(user, "T", "B", type="chat") is None
            assert NotificationService.create_notification(user, "T", "B", type="system") is not None
    assert delay.call_count == 1


def test_bulk_notifications_queue_web_push_per_allowed_user_with_their_own_notification_id(
    user, other, django_capture_on_commit_callbacks
):
    NotificationPreference.objects.create(user=other, push_enabled=False)
    with override_settings(PUSH_NOTIFICATIONS_ENABLED=False):
        with mock.patch("apps.notifications.tasks.deliver_push_notification.delay") as delay:
            with django_capture_on_commit_callbacks(execute=True):
                created = NotificationService.create_bulk_notifications([user, other], "T", "B", "announcement")
    assert len(created) == 2
    assert [call.args[0] for call in delay.call_args_list] == [user.id]
    mine = Notification.objects.get(user=user)
    assert delay.call_args.args[3] == {"notification_id": mine.pk, "type": "announcement"}
    assert delay.call_args.kwargs == {"channels": ["web"]}


def test_expo_and_web_both_receive_and_expo_only_retry_does_not_touch_web(user):
    DeviceToken.objects.create(user=user, token="ExponentPushToken[aaaaaaaaaaaaaaaaaaaaaa]", platform="android")
    _subscription(user)
    with mock.patch.object(PushNotificationService, "_send_expo", return_value=1) as expo, mock.patch(WEBPUSH) as push:
        assert PushNotificationService.send_to_user(user, "T", "B") is True
    expo.assert_called_once()
    push.assert_called_once()

    with mock.patch.object(PushNotificationService, "_send_expo", return_value=1) as expo, mock.patch(WEBPUSH) as push:
        PushNotificationService.send_to_user(user, "T", "B", only_tokens=["ExponentPushToken[aaaaaaaaaaaaaaaaaaaaaa]"])
    expo.assert_called_once()
    push.assert_not_called()


def test_expo_and_web_transient_failures_are_combined_into_one_retry(user):
    DeviceToken.objects.create(user=user, token="ExponentPushToken[aaaaaaaaaaaaaaaaaaaaaa]", platform="android")
    _subscription(user)
    expo_failure = PushDeliveryTransientError(["ExponentPushToken[aaaaaaaaaaaaaaaaaaaaaa]"], "http_503")
    with mock.patch.object(PushNotificationService, "_send_expo", side_effect=expo_failure):
        with mock.patch(WEBPUSH, side_effect=_push_error(429)):
            with pytest.raises(PushDeliveryTransientError) as excinfo:
                PushNotificationService.send_to_user(user, "T", "B")
    assert sorted(excinfo.value.tokens) == sorted(["ExponentPushToken[aaaaaaaaaaaaaaaaaaaaaa]", ENDPOINT])


def test_channels_limit_delivery(user):
    DeviceToken.objects.create(user=user, token="ExponentPushToken[aaaaaaaaaaaaaaaaaaaaaa]", platform="android")
    _subscription(user)
    with mock.patch.object(PushNotificationService, "_send_expo", return_value=1) as expo, mock.patch(WEBPUSH) as push:
        PushNotificationService.send_to_user(user, "T", "B", channels=["web"])
    expo.assert_not_called()
    push.assert_called_once()
