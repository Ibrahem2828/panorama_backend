from __future__ import annotations

from celery import shared_task

from .notify import ChatNotificationService


@shared_task(
    autoretry_for=(OSError, TimeoutError),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    ignore_result=True,
)
def notify_chat_message(message_id: int) -> None:
    """Fan a new chat message out to the other members as in-app notifications and pushes."""

    ChatNotificationService.notify(message_id)
