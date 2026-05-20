from collections.abc import Iterable

import logging

from django.conf import settings

from .models import Notification, NotificationType

logger = logging.getLogger(__name__)


class NotificationService:
    @staticmethod
    def create_notification(user, title: str, body: str, type: str = NotificationType.SYSTEM, data: dict | None = None):
        return Notification.objects.create(
            user=user,
            title=title,
            body=body,
            type=type,
            data=data or {},
        )

    @staticmethod
    def create_bulk_notifications(
        users: Iterable,
        title: str,
        body: str,
        type: str = NotificationType.SYSTEM,
        data: dict | None = None,
    ):
        notifications = [
            Notification(user=user, title=title, body=body, type=type, data=data or {})
            for user in users
        ]
        return Notification.objects.bulk_create(notifications)


class PushNotificationService:
    @staticmethod
    def send_to_user(user, title: str, body: str, data: dict | None = None) -> bool:
        if not getattr(settings, "FCM_SERVER_KEY", ""):
            logger.info("FCM is not configured; skipping push notification for user %s", getattr(user, "id", None))
            return False
        return False

    @staticmethod
    def send_to_users(users, title: str, body: str, data: dict | None = None) -> int:
        sent = 0
        for user in users:
            sent += int(PushNotificationService.send_to_user(user, title, body, data))
        return sent
