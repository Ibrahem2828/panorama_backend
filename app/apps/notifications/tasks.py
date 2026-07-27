from __future__ import annotations

from celery import shared_task

from apps.accounts.models import User

from .services import PushNotificationService


@shared_task(
    bind=True,
    autoretry_for=(OSError, TimeoutError),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=4,
    ignore_result=True,
)
def deliver_push_notification(self, user_id: int, title: str, body: str, data: dict | None = None):
    user = User.objects.filter(pk=user_id, is_active=True, is_deleted=False).first()
    if user:
        PushNotificationService.send_to_user(user, title, body, data)
