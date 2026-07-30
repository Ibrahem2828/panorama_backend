from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import DeviceToken, Notification, NotificationType

logger = logging.getLogger(__name__)


class NotificationService:
    @staticmethod
    def create_notification(user, title: str, body: str, type: str = NotificationType.SYSTEM, data: dict | None = None):
        notification = Notification.objects.create(
            user=user,
            title=title,
            body=body,
            type=type,
            data=data or {},
        )
        if getattr(settings, "PUSH_NOTIFICATIONS_ENABLED", False):
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
        notifications = [
            Notification(user=user, title=title, body=body, type=type, data=data or {})
            for user in users
        ]
        created = Notification.objects.bulk_create(notifications)
        if getattr(settings, "PUSH_NOTIFICATIONS_ENABLED", False):
            from .tasks import deliver_push_notification

            for user in users:
                transaction.on_commit(
                    lambda user_id=user.id: deliver_push_notification.delay(user_id, title, body, data or {}),
                    robust=True,
                )
        return created


class PushNotificationService:
    """Small provider adapter for Expo push tokens; failures never break business transactions."""

    @staticmethod
    def _send_expo(tokens: list[str], title: str, body: str, data: dict | None = None) -> int:
        endpoint = getattr(settings, "EXPO_PUSH_ENDPOINT", "https://exp.host/--/api/v2/push/send")
        try:
            parsed_endpoint = urlsplit(endpoint)
        except ValueError:
            logger.error("Push notification endpoint is malformed")
            return 0
        allowed_hosts = {str(host).strip().lower() for host in getattr(settings, "EXPO_PUSH_ALLOWED_HOSTS", ())}
        if (
            parsed_endpoint.scheme != "https"
            or not parsed_endpoint.hostname
            or parsed_endpoint.hostname.lower() not in allowed_hosts
            or parsed_endpoint.username
            or parsed_endpoint.password
        ):
            logger.error("Push notification endpoint is not an allowlisted HTTPS Expo host")
            return 0

        messages = [
            {
                "to": token,
                "sound": "default",
                "title": title[:200],
                "body": body[:1000],
                "data": data or {},
            }
            for token in tokens
        ]
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        access_token = getattr(settings, "EXPO_ACCESS_TOKEN", "")
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        req = urllib_request.Request(
            endpoint,
            data=json.dumps(messages).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            # The endpoint is constrained to an HTTPS host allowlist above.
            with urllib_request.urlopen(req, timeout=5) as response:  # nosec B310
                if not 200 <= response.status < 300:
                    return 0
            return len(tokens)
        except (HTTPError, URLError, TimeoutError, ValueError):
            logger.exception("Push notification delivery failed")
            return 0

    @staticmethod
    def send_to_user(user, title: str, body: str, data: dict | None = None) -> bool:
        tokens = list(
            DeviceToken.objects.filter(user=user, is_active=True, is_deleted=False)
            .values_list("token", flat=True)[:100]
        )
        if not tokens:
            return False
        sent = PushNotificationService._send_expo(tokens, title, body, data)
        if sent:
            DeviceToken.objects.filter(token__in=tokens).update(last_used_at=timezone.now())
        return sent > 0

    @staticmethod
    def send_to_users(users, title: str, body: str, data: dict | None = None) -> int:
        user_ids = [user.pk for user in users]
        tokens = list(
            DeviceToken.objects.filter(user_id__in=user_ids, is_active=True, is_deleted=False)
            .values_list("token", flat=True)[:1000]
        )
        if not tokens:
            return 0
        sent = PushNotificationService._send_expo(tokens, title, body, data)
        if sent:
            DeviceToken.objects.filter(token__in=tokens).update(last_used_at=timezone.now())
        return sent
