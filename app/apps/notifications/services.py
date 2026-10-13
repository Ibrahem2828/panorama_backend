from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Iterable
from functools import partial
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import DeviceToken, Notification, NotificationPreference, NotificationType

logger = logging.getLogger(__name__)

#: Expo accepts at most 100 messages per request.
EXPO_BATCH_SIZE = 100
#: Ticket errors after which the token can never receive a push again; it is deactivated, never retried.
PERMANENT_TOKEN_ERRORS = frozenset({"DeviceNotRegistered"})
#: Ticket errors that are worth retrying later for that token only.
TRANSIENT_TICKET_ERRORS = frozenset({"MessageRateExceeded"})


class PushDeliveryTransientError(RuntimeError):
    """Part of a push could not be delivered for a retryable reason (timeout, 5xx, 429, rate limit).

    ``tokens`` are exactly the tokens that still need a retry, so a retry never re-sends to recipients that
    already got the message.
    """

    def __init__(self, tokens: list[str], reason: str):
        super().__init__(f"push delivery incomplete ({reason}) for {len(tokens)} token(s)")
        self.tokens = tokens
        self.reason = reason


class NotificationService:
    @staticmethod
    def create_notification(
        user, title: str, body: str, type: str = NotificationType.SYSTEM, data: dict | None = None, **kwargs
    ):
        preference, _ = NotificationPreference.objects.get_or_create(user=user)
        if not preference.allows(type):
            return None
        notification = Notification.objects.create(
            user=user,
            title=title,
            body=body,
            type=type,
            data=data or {},
            **kwargs,
        )
        if getattr(settings, "PUSH_NOTIFICATIONS_ENABLED", False) and preference.push_enabled:
            from .tasks import deliver_push_notification

            transaction.on_commit(
                lambda: deliver_push_notification.delay(user.id, title, body, data or {}),
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
        notifications = [
            Notification(user=user, title=title, body=body, type=type, data=data or {})
            for user in users
            if preferences.get(user.id, None) is None or preferences[user.id].allows(type)
        ]
        created = Notification.objects.bulk_create(notifications)
        if getattr(settings, "PUSH_NOTIFICATIONS_ENABLED", False):
            from .tasks import deliver_push_notification

            created_user_ids = {notification.user_id for notification in created}
            for user in users:
                if user.id not in created_user_ids:
                    continue
                preference = preferences.get(user.id)
                if preference is not None and not preference.push_enabled:
                    continue  # the single-user path honours this; campaigns must too
                transaction.on_commit(
                    partial(deliver_push_notification.delay, user.id, title, body, data or {}), robust=True
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
        error_codes: Counter[str] = Counter()
        for start in range(0, len(tokens), EXPO_BATCH_SIZE):
            batch = tokens[start : start + EXPO_BATCH_SIZE]
            messages = [
                {"to": token, "sound": "default", "title": title[:200], "body": body[:1000], "data": data or {}}
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
                    continue
                details = ticket.get("details") if isinstance(ticket, dict) else None
                code = str(details.get("error")) if isinstance(details, dict) and details.get("error") else "unknown"
                error_codes[code] += 1
                if code in PERMANENT_TOKEN_ERRORS:
                    dead.append(token)
                elif code in TRANSIENT_TICKET_ERRORS:
                    retry.append(token)
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
        user, title: str, body: str, data: dict | None = None, *, only_tokens: Iterable[str] | None = None
    ) -> bool:
        tokens = PushNotificationService.active_tokens([user.pk], limit=100, only=only_tokens)
        if not tokens:
            return False
        sent = PushNotificationService._send_expo(tokens, title, body, data)
        if sent:
            DeviceToken.objects.filter(token__in=tokens, is_active=True).update(last_used_at=timezone.now())
        return sent > 0

    @staticmethod
    def send_to_users(users, title: str, body: str, data: dict | None = None) -> int:
        tokens = PushNotificationService.active_tokens([user.pk for user in users], limit=1000)
        if not tokens:
            return 0
        sent = PushNotificationService._send_expo(tokens, title, body, data)
        if sent:
            DeviceToken.objects.filter(token__in=tokens, is_active=True).update(last_used_at=timezone.now())
        return sent
