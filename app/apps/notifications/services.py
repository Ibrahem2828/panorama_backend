from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from collections.abc import Iterable
from datetime import timedelta
from functools import partial
from typing import Any
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from pywebpush import WebPushException, webpush

from . import categories
from .models import (
    DeviceToken,
    Notification,
    NotificationPreference,
    NotificationType,
    PushDelivery,
    PushDeliveryStatus,
    WebPushSubscription,
)
from .webpush_security import InvalidWebPushInput, validate_endpoint

logger = logging.getLogger(__name__)

#: Expo accepts at most 100 messages per request.
EXPO_BATCH_SIZE = 100
#: Ticket errors after which the token can never receive a push again; it is deactivated, never retried.
PERMANENT_TOKEN_ERRORS = frozenset({"DeviceNotRegistered"})
#: Ticket errors that are worth retrying later for that token only.
TRANSIENT_TICKET_ERRORS = frozenset({"MessageRateExceeded"})


#: Web Push answers meaning the subscription is gone for good: it is deactivated, never retried.
WEB_PUSH_GONE_STATUSES = frozenset({404, 410})
#: Web subscriptions one user can receive on (newest first); bounds the work of one delivery.
WEB_PUSH_MAX_SUBSCRIPTIONS_PER_USER = 20
#: How long a push service may hold the message for an offline browser.
WEB_PUSH_TTL_SECONDS = 24 * 60 * 60

#: Delivery channels a queued push targets. ``None`` (messages queued before channels existed) means every channel.
CHANNEL_EXPO = "expo"
CHANNEL_WEB = "web"


def web_push_configured() -> bool:
    return bool(getattr(settings, "WEB_PUSH_ENABLED", False))


def push_channels() -> list[str]:
    """Channels switched on by configuration. Expo keeps its ``PUSH_NOTIFICATIONS_ENABLED`` switch; browser push is
    on whenever the VAPID settings are complete, independent of Expo."""

    channels = []
    if getattr(settings, "PUSH_NOTIFICATIONS_ENABLED", False):
        channels.append(CHANNEL_EXPO)
    if web_push_configured():
        channels.append(CHANNEL_WEB)
    return channels


def _push_data(notification: Notification, data: dict | None) -> dict:
    """Data sent with a push: the caller's data plus what a client needs to route the tap."""

    merged: dict[str, Any] = dict(data or {})
    merged.setdefault("notification_id", notification.pk)
    merged.setdefault("type", notification.type)
    category = notification.category or categories.category_for(notification.type, merged)
    merged.setdefault("category", category)
    merged.setdefault("channel_id", categories.android_channel_for(category))
    if notification.deep_link:
        merged.setdefault("deep_link", notification.deep_link)
    # Arabic stays in the push ``title``/``body``; the localized variants let a client pick the user's language.
    for field in ("title_ar", "title_en", "body_ar", "body_en"):
        value = getattr(notification, field, "")
        if value:
            merged.setdefault(field, value)
    return merged


class PushDeliveryTransientError(RuntimeError):
    """Part of a push could not be delivered for a retryable reason (timeout, 5xx, 429, rate limit).

    ``tokens`` are exactly the tokens that still need a retry, so a retry never re-sends to recipients that
    already got the message.
    """

    def __init__(self, tokens: list[str], reason: str):
        super().__init__(f"push delivery incomplete ({reason}) for {len(tokens)} token(s)")
        self.tokens = tokens
        self.reason = reason


#: Seconds during which further pushes of a collapsed (staff inbox) category to one person are folded into the first.
BURST_WINDOW_SECONDS = 5


def burst_window_seconds() -> int:
    """Burst window, tunable without a deploy through the ``PUSH_BURST_WINDOW_SECONDS`` setting (0 disables folding)."""

    try:
        return max(0, int(getattr(settings, "PUSH_BURST_WINDOW_SECONDS", BURST_WINDOW_SECONDS)))
    except (TypeError, ValueError):
        return BURST_WINDOW_SECONDS


def push_suppressed_reason(
    preference: NotificationPreference | None, category: str, user_id: int, now=None, *, collapse: bool = False
) -> str | None:
    """Why a push must not be sent right now (the in-app notification is always kept), or None when it may go out.

    Reasons: ``push_off`` (member turned push off), ``channel_off`` (that channel is switched off), ``quiet_hours``,
    ``burst`` (an earlier push of the same inbox category went out a moment ago and covers this one).
    """

    if preference is not None:
        if not preference.push_enabled:
            return "push_off"
        if not preference.channel_enabled(categories.preference_channel_for(category)):
            return "channel_off"
        if preference.in_quiet_hours(now):
            return "quiet_hours"
    if collapse:
        window = burst_window_seconds()
        if window > 0:
            try:
                if not cache.add(f"push:burst:{user_id}:{category}", True, timeout=window):
                    return "burst"
            except Exception:  # the cache is a courtesy: without it every push goes out
                logger.warning("push burst cache unavailable")
    return None


class NotificationService:
    @staticmethod
    def create_notification(
        user, title: str, body: str, type: str = NotificationType.SYSTEM, data: dict | None = None, **kwargs
    ):
        preference, _ = NotificationPreference.objects.get_or_create(user=user)
        if not preference.allows(type):
            return None
        category = kwargs.pop("category", "") or categories.category_for(type, data)
        notification = Notification.objects.create(
            user=user,
            title=title,
            body=body,
            type=type,
            category=category,
            data=data or {},
            **kwargs,
        )
        channels = push_channels()
        collapse = str((data or {}).get("kind") or "") in categories.STAFF_KINDS
        if channels and push_suppressed_reason(preference, category, user.pk, collapse=collapse) is None:
            from .tasks import deliver_push_notification

            push_data = _push_data(notification, data)
            transaction.on_commit(
                lambda: deliver_push_notification.delay(user.id, title, body, push_data, channels=channels),
                robust=True,
            )
        return notification

    @staticmethod
    def create_bulk_notifications(
        users: Iterable,
        title: str,
        body: str,
        type: str = NotificationType.SYSTEM,
        data: dict | None = None,
    ):
        users = list(users)
        preferences = {item.user_id: item for item in NotificationPreference.objects.filter(user__in=users)}
        category = categories.category_for(type, data)
        notifications = [
            Notification(user=user, title=title, body=body, type=type, category=category, data=data or {})
            for user in users
            if preferences.get(user.id, None) is None or preferences[user.id].allows(type)
        ]
        created = Notification.objects.bulk_create(notifications)
        channels = push_channels()
        if channels:
            from .tasks import deliver_push_notification

            created_by_user = {notification.user_id: notification for notification in created}
            created_user_ids = set(created_by_user)
            for user in users:
                if user.id not in created_user_ids:
                    continue
                preference = preferences.get(user.id)
                if push_suppressed_reason(preference, category, user.id) is not None:
                    continue  # the single-user path honours this; campaigns must too
                transaction.on_commit(
                    partial(
                        deliver_push_notification.delay,
                        user.id,
                        title,
                        body,
                        _push_data(created_by_user[user.id], data),
                        channels=channels,
                    ),
                    robust=True,
                )
        return created


class PushNotificationService:
    """Small provider adapter for Expo push tokens; failures never break business transactions.

    Device tokens live in two stores that are never merged in the database: ``notifications.DeviceToken`` (the
    ``/notifications/device-tokens/`` endpoint) and ``product.DeviceInstallation.push_token`` (mobile device
    registration). Sending always reads the union of both, de-duplicated; a dead token is deactivated in both.
    """

    @staticmethod
    def _endpoint() -> str | None:
        endpoint = getattr(settings, "EXPO_PUSH_ENDPOINT", "https://exp.host/--/api/v2/push/send")
        try:
            parsed_endpoint = urlsplit(endpoint)
        except ValueError:
            logger.error("Push notification endpoint is malformed")
            return None
        allowed_hosts = {str(host).strip().lower() for host in getattr(settings, "EXPO_PUSH_ALLOWED_HOSTS", ())}
        if (
            parsed_endpoint.scheme != "https"
            or not parsed_endpoint.hostname
            or parsed_endpoint.hostname.lower() not in allowed_hosts
            or parsed_endpoint.username
            or parsed_endpoint.password
        ):
            logger.error("Push notification endpoint is not an allowlisted HTTPS Expo host")
            return None
        return endpoint

    @staticmethod
    def _post_batch(endpoint: str, messages: list[dict]) -> tuple[int, list | None]:
        """POST one batch. Returns (http_status, tickets); tickets is None when the body is not a usable list."""

        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        access_token = getattr(settings, "EXPO_ACCESS_TOKEN", "")
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        req = urllib_request.Request(
            endpoint, data=json.dumps(messages).encode("utf-8"), headers=headers, method="POST"
        )
        # The endpoint is constrained to an HTTPS host allowlist by ``_endpoint``.
        with urllib_request.urlopen(req, timeout=5) as response:  # nosec B310
            status = response.status
            if not 200 <= status < 300:
                return status, None
            try:
                payload = json.loads(response.read())
            except (AttributeError, TypeError, ValueError):
                return status, None
        tickets = payload.get("data") if isinstance(payload, dict) else None
        return status, tickets if isinstance(tickets, list) else None

    @staticmethod
    def _send_expo(tokens: list[str], title: str, body: str, data: dict | None = None) -> int:
        """Send to Expo in batches of 100 and return how many messages Expo accepted.

        Each returned ticket is matched to its token: ``DeviceNotRegistered`` deactivates that token (soft), other
        ticket errors are only counted and logged by code (never with the token). Timeouts, HTTP 429/5xx and
        ``MessageRateExceeded`` raise ``PushDeliveryTransientError`` *after* the whole list was attempted, carrying
        only the tokens that need a retry. Other HTTP errors (4xx) are permanent and logged.
        """

        endpoint = PushNotificationService._endpoint()
        if endpoint is None:
            return 0
        accepted = 0
        dead: list[str] = []
        retry: list[str] = []
        sent_rows: list[PushDelivery] = []
        error_codes: Counter[str] = Counter()
        for start in range(0, len(tokens), EXPO_BATCH_SIZE):
            batch = tokens[start : start + EXPO_BATCH_SIZE]
            messages = [
                {
                    "to": token,
                    "sound": "default",
                    "title": title[:200],
                    "body": body[:1000],
                    "data": data or {},
                    # An Android notification channel chosen by the sender (the app creates "chat", "printing"...).
                    **({"channelId": data["channel_id"]} if data and data.get("channel_id") else {}),
                }
                for token in batch
            ]
            try:
                status, tickets = PushNotificationService._post_batch(endpoint, messages)
            except HTTPError as exc:
                if exc.code == 429 or exc.code >= 500:
                    retry.extend(batch)
                    error_codes[f"http_{exc.code}"] += len(batch)
                else:
                    logger.error("Push provider rejected the request", extra={"http_status": exc.code})
                    error_codes[f"http_{exc.code}"] += len(batch)
                continue
            except (URLError, TimeoutError, OSError) as exc:
                logger.warning("Push provider unreachable", extra={"failure_class": type(exc).__name__})
                retry.extend(batch)
                error_codes["unreachable"] += len(batch)
                continue
            if not 200 <= status < 300:
                error_codes[f"http_{status}"] += len(batch)
                continue
            if tickets is None or len(tickets) != len(batch):
                # Accepted by HTTP but receipts unusable: count as sent, change nothing about the tokens.
                logger.warning("Push provider response had no usable tickets", extra={"batch_size": len(batch)})
                accepted += len(batch)
                continue
            for token, ticket in zip(batch, tickets, strict=True):
                if isinstance(ticket, dict) and ticket.get("status") == "ok":
                    accepted += 1
                    sent_rows.append(PushNotificationService._delivery_row(token, ticket, data))
                    continue
                details = ticket.get("details") if isinstance(ticket, dict) else None
                code = str(details.get("error")) if isinstance(details, dict) and details.get("error") else "unknown"
                error_codes[code] += 1
                if code in PERMANENT_TOKEN_ERRORS:
                    dead.append(token)
                    sent_rows.append(
                        PushNotificationService._delivery_row(token, None, data, PushDeliveryStatus.DEAD, code)
                    )
                elif code in TRANSIENT_TICKET_ERRORS:
                    retry.append(token)
                else:
                    sent_rows.append(
                        PushNotificationService._delivery_row(token, None, data, PushDeliveryStatus.FAILED, code)
                    )
        if sent_rows:
            PushNotificationService._store_rows(sent_rows)
        if dead:
            PushNotificationService.deactivate_tokens(dead)
        if error_codes:
            logger.warning(
                "Push delivery finished with errors",
                extra={
                    "event": "push_delivery_errors",
                    "accepted": accepted,
                    "deactivated": len(dead),
                    "retry": len(retry),
                    "error_codes": dict(error_codes),
                },
            )
        if retry:
            raise PushDeliveryTransientError(retry, ",".join(sorted(error_codes)))
        return accepted

    @staticmethod
    def _delivery_row(
        token: str,
        ticket: dict | None,
        data: dict | None,
        status: str = PushDeliveryStatus.SENT,
        error_code: str = "",
    ) -> PushDelivery:
        notification_id = (data or {}).get("notification_id")
        return PushDelivery(
            notification_id=notification_id if isinstance(notification_id, int) else None,
            channel=CHANNEL_EXPO,
            category=str((data or {}).get("category") or ""),
            # The raw token is only needed while a receipt is pending (to switch a dead device off).
            token=token if status == PushDeliveryStatus.SENT else "",
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            ticket_id=str((ticket or {}).get("id") or "")[:64],
            status=status,
            error_code=error_code[:64],
        )

    @staticmethod
    def _store_rows(rows: list[PushDelivery]) -> None:
        try:
            PushDelivery.objects.bulk_create(rows)
        except Exception:  # metrics must never break a delivery that already went out
            logger.exception("push_delivery_record_failed")

    @staticmethod
    def deactivate_tokens(tokens: list[str]) -> None:
        """Soft-deactivate dead tokens in both stores. The installation row itself stays; the app registers a fresh
        token on its next launch."""

        from apps.product.models import DeviceInstallation

        now = timezone.now()
        DeviceToken.objects.filter(token__in=tokens, is_active=True).update(is_active=False, updated_at=now)
        DeviceInstallation.objects.filter(push_token__in=tokens).update(push_token=None, updated_at=now)

    @staticmethod
    def active_tokens(user_ids: list[int], *, limit: int, only: Iterable[str] | None = None) -> list[str]:
        """De-duplicated union of the active tokens of both stores (``DeviceToken`` first), at most ``limit``."""

        from apps.product.models import DeviceInstallation

        device_tokens = DeviceToken.objects.filter(user_id__in=user_ids, is_active=True, is_deleted=False).values_list(
            "token", flat=True
        )[:limit]
        installation_tokens = (
            DeviceInstallation.objects.filter(
                user_id__in=user_ids, notifications_enabled=True, revoked_at__isnull=True, is_deleted=False
            )
            .exclude(push_token__isnull=True)
            .exclude(push_token="")
            .values_list("push_token", flat=True)[:limit]
        )
        union = dict.fromkeys(str(token) for token in [*device_tokens, *installation_tokens] if token)
        tokens = list(union)
        if only is not None:
            wanted = set(only)
            tokens = [token for token in tokens if token in wanted]
        return tokens[:limit]

    @staticmethod
    def send_to_user(
        user,
        title: str,
        body: str,
        data: dict | None = None,
        *,
        only_tokens: Iterable[str] | None = None,
        channels: Iterable[str] | None = None,
    ) -> bool:
        """Deliver to the user's Expo tokens and browser subscriptions.

        ``only_tokens`` restricts a retry to exactly the recipients (Expo tokens or web endpoints) that still need
        it. A transient failure in either channel is raised only after both were attempted, carrying the combined
        list, so a recipient that already got the message is never retried.
        """

        wanted = None if channels is None else set(channels)
        only = None if only_tokens is None else list(only_tokens)
        delivered = False
        retry: list[str] = []
        reasons: list[str] = []
        if wanted is None or CHANNEL_EXPO in wanted:
            tokens = PushNotificationService.active_tokens([user.pk], limit=100, only=only)
            if tokens:
                try:
                    sent = PushNotificationService._send_expo(tokens, title, body, data)
                except PushDeliveryTransientError as exc:
                    retry.extend(exc.tokens)
                    reasons.append(exc.reason)
                else:
                    if sent:
                        DeviceToken.objects.filter(token__in=tokens, is_active=True).update(last_used_at=timezone.now())
                    delivered = sent > 0
        if (wanted is None or CHANNEL_WEB in wanted) and web_push_configured():
            try:
                delivered = WebPushService.send_to_user(user, title, body, data, only=only) > 0 or delivered
            except PushDeliveryTransientError as exc:
                retry.extend(exc.tokens)
                reasons.append(exc.reason)
        if retry:
            raise PushDeliveryTransientError(retry, ",".join(sorted(set(reasons))))
        return delivered

    @staticmethod
    def send_to_users(users, title: str, body: str, data: dict | None = None) -> int:
        tokens = PushNotificationService.active_tokens([user.pk for user in users], limit=1000)
        if not tokens:
            return 0
        sent = PushNotificationService._send_expo(tokens, title, body, data)
        if sent:
            DeviceToken.objects.filter(token__in=tokens, is_active=True).update(last_used_at=timezone.now())
        return sent


class WebPushService:
    """Browser Web Push (VAPID) delivery through ``pywebpush``; one HTTP request per subscription.

    Outcomes per subscription: 2xx delivered (failure counter reset); 404/410 the subscription is gone, so it is
    deactivated; 429, 5xx, timeouts and connection errors are transient (the subscription's endpoint is returned in
    ``PushDeliveryTransientError.tokens`` so only it is retried); any other 4xx counts a consecutive failure and
    deactivates the subscription after ``WEB_PUSH_MAX_CONSECUTIVE_FAILURES``. Endpoints and keys are never logged.
    """

    @staticmethod
    def build_payload(title: str, body: str, data: dict | None = None) -> str:
        data = dict(data or {})
        url = str(data.get("url") or data.get("deep_link") or "/notifications")
        if not url.startswith("/") or url.startswith("//"):
            url = "/notifications"  # the service worker only opens same-origin paths
        notification_id = data.get("notification_id")
        kind = str(data.get("type") or "") or "notification"
        tag = f"panorama-{kind}-{notification_id}" if notification_id else f"panorama-{kind}"
        payload = {
            "title": title[:200],
            "body": body[:1000],
            "data": {**data, "url": url, "notification_id": notification_id, "type": data.get("type", "")},
            "tag": tag[:100],
        }
        return json.dumps(payload, ensure_ascii=False, default=str)

    @staticmethod
    def active_subscriptions(user_id: int, *, only: Iterable[str] | None = None) -> list[WebPushSubscription]:
        queryset = WebPushSubscription.objects.filter(user_id=user_id, is_active=True, is_deleted=False)
        if only is not None:
            queryset = queryset.filter(endpoint__in=list(only))
        return list(queryset.order_by("-updated_at")[:WEB_PUSH_MAX_SUBSCRIPTIONS_PER_USER])

    @staticmethod
    def _session() -> requests.Session:
        session = requests.Session()
        session.max_redirects = 0  # a push service must never bounce us to another host (SSRF)
        session.trust_env = False
        return session

    @staticmethod
    def _post(subscription: WebPushSubscription, payload: str, session: requests.Session) -> None:
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
            },
            data=payload,
            vapid_private_key=settings.WEB_PUSH_VAPID_PRIVATE_KEY,
            vapid_claims={"sub": settings.WEB_PUSH_VAPID_SUBJECT},  # fresh dict: pywebpush mutates it
            ttl=WEB_PUSH_TTL_SECONDS,
            timeout=getattr(settings, "WEB_PUSH_TIMEOUT_SECONDS", 5),
            requests_session=session,
        )

    @staticmethod
    def send_to_user(
        user, title: str, body: str, data: dict | None = None, *, only: Iterable[str] | None = None
    ) -> int:
        """Send to the user's active subscriptions; returns how many accepted the message."""

        subscriptions = WebPushService.active_subscriptions(user.pk, only=only)
        if not subscriptions:
            return 0
        payload = WebPushService.build_payload(title, body, data)
        session = WebPushService._session()
        retry: list[str] = []
        gone: list[int] = []
        succeeded: list[int] = []
        failed: list[int] = []
        codes: Counter[str] = Counter()
        try:
            for subscription in subscriptions:
                try:
                    validate_endpoint(subscription.endpoint)  # re-check: the allowlist setting may have changed
                except InvalidWebPushInput:
                    gone.append(subscription.pk)
                    codes["endpoint_rejected"] += 1
                    continue
                try:
                    WebPushService._post(subscription, payload, session)
                except WebPushException as exc:
                    status = exc.status_code
                    if status in WEB_PUSH_GONE_STATUSES:
                        gone.append(subscription.pk)
                    elif status is not None and (status == 429 or status >= 500):
                        retry.append(subscription.endpoint)
                    else:
                        failed.append(subscription.pk)
                    codes[f"http_{status}" if status is not None else "rejected"] += 1
                except requests.exceptions.TooManyRedirects:
                    failed.append(subscription.pk)
                    codes["redirect"] += 1
                except (requests.exceptions.RequestException, TimeoutError, OSError) as exc:
                    retry.append(subscription.endpoint)
                    codes["unreachable"] += 1
                    logger.warning("Web push service unreachable", extra={"failure_class": type(exc).__name__})
                except (ValueError, TypeError) as exc:  # malformed key material
                    failed.append(subscription.pk)
                    codes["invalid_subscription"] += 1
                    logger.warning("Web push subscription unusable", extra={"failure_class": type(exc).__name__})
                else:
                    succeeded.append(subscription.pk)
        finally:
            session.close()
        WebPushService._record_outcomes(succeeded=succeeded, gone=gone, failed=failed)
        if codes:
            logger.warning(
                "Web push delivery finished with errors",
                extra={
                    "event": "web_push_delivery_errors",
                    "accepted": len(succeeded),
                    "deactivated": len(gone),
                    "retry": len(retry),
                    "error_codes": dict(codes),
                },
            )
        if retry:
            raise PushDeliveryTransientError(retry, ",".join(sorted(codes)))
        return len(succeeded)

    @staticmethod
    def _record_outcomes(*, succeeded: list[int], gone: list[int], failed: list[int]) -> None:
        now = timezone.now()
        if succeeded:
            WebPushSubscription.objects.filter(pk__in=succeeded).update(
                last_used_at=now, failure_count=0, updated_at=now
            )
        if gone:
            WebPushSubscription.objects.filter(pk__in=gone).update(is_active=False, updated_at=now)
        if failed:
            WebPushSubscription.objects.filter(pk__in=failed).update(
                failure_count=F("failure_count") + 1, updated_at=now
            )
            limit = max(1, int(getattr(settings, "WEB_PUSH_MAX_CONSECUTIVE_FAILURES", 5)))
            WebPushSubscription.objects.filter(pk__in=failed, failure_count__gte=limit).update(
                is_active=False, updated_at=now
            )


class PushReceiptService:
    """Expo answers a push twice: a ticket right away ("accepted") and a receipt later ("delivered to FCM" or an error).

    ``check_pending`` asks for the receipts of pushes older than ``RECEIPT_MIN_AGE`` (Expo prepares them after about
    15 minutes) and settles each: ok -> delivered, DeviceNotRegistered -> dead (the device is switched off in both token
    stores), any other error -> failed. Pushes whose receipt never arrives are failed after ``RECEIPT_MAX_AGE``.
    """

    RECEIPT_MIN_AGE = timedelta(minutes=15)
    RECEIPT_MAX_AGE = timedelta(hours=24)
    BATCH = 300

    @staticmethod
    def _receipts_endpoint() -> str | None:
        endpoint = PushNotificationService._endpoint()
        if endpoint is None:
            return None
        return endpoint.rsplit("/", 1)[0] + "/getReceipts"

    @staticmethod
    def _fetch(endpoint: str, ids: list[str]) -> dict | None:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        access_token = getattr(settings, "EXPO_ACCESS_TOKEN", "")
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        req = urllib_request.Request(
            endpoint, data=json.dumps({"ids": ids}).encode("utf-8"), headers=headers, method="POST"
        )
        with urllib_request.urlopen(req, timeout=10) as response:  # nosec B310 - host allowlist in _endpoint
            if not 200 <= response.status < 300:
                return None
            try:
                payload = json.loads(response.read())
            except (AttributeError, TypeError, ValueError):
                return None
        data = payload.get("data") if isinstance(payload, dict) else None
        return data if isinstance(data, dict) else None

    @classmethod
    def check_pending(cls, now=None) -> dict[str, int]:
        now = now or timezone.now()
        result = {"checked": 0, "delivered": 0, "failed": 0, "dead": 0, "expired": 0}
        expired = PushDelivery.objects.filter(
            status=PushDeliveryStatus.SENT, channel=CHANNEL_EXPO, created_at__lt=now - cls.RECEIPT_MAX_AGE
        )
        result["expired"] = expired.update(
            status=PushDeliveryStatus.FAILED, error_code="receipt_expired", token="", receipt_checked_at=now
        )
        endpoint = cls._receipts_endpoint()
        if endpoint is None:
            return result
        pending = list(
            PushDelivery.objects.filter(
                status=PushDeliveryStatus.SENT, channel=CHANNEL_EXPO, created_at__lt=now - cls.RECEIPT_MIN_AGE
            )
            .exclude(ticket_id="")
            .order_by("created_at")[: cls.BATCH]
        )
        if not pending:
            return result
        try:
            receipts = cls._fetch(endpoint, [row.ticket_id for row in pending])
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            logger.warning("Push receipts unreachable", extra={"failure_class": type(exc).__name__})
            return result
        if receipts is None:
            return result
        dead_tokens: list[str] = []
        for row in pending:
            receipt = receipts.get(row.ticket_id)
            if not isinstance(receipt, dict):
                continue  # not ready yet: asked again next run
            result["checked"] += 1
            row.receipt_checked_at = now
            if receipt.get("status") == "ok":
                row.status = PushDeliveryStatus.DELIVERED
                result["delivered"] += 1
            else:
                details = receipt.get("details")
                code = str(details.get("error")) if isinstance(details, dict) and details.get("error") else "unknown"
                row.error_code = code[:64]
                if code in PERMANENT_TOKEN_ERRORS:
                    row.status = PushDeliveryStatus.DEAD
                    dead_tokens.append(row.token)
                    result["dead"] += 1
                else:
                    row.status = PushDeliveryStatus.FAILED
                    result["failed"] += 1
            row.token = ""
        PushDelivery.objects.bulk_update(
            [row for row in pending if row.receipt_checked_at],
            ["status", "error_code", "token", "receipt_checked_at", "updated_at"],
        )
        dead_tokens = [token for token in dead_tokens if token]
        if dead_tokens:
            PushNotificationService.deactivate_tokens(dead_tokens)
        return result
