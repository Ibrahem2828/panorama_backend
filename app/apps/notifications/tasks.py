from __future__ import annotations

from celery import shared_task

from apps.accounts.models import User

from .services import PushDeliveryTransientError, PushNotificationService

_RETRY_BASE_SECONDS = 30


@shared_task(
    bind=True,
    autoretry_for=(OSError, TimeoutError),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=4,
    ignore_result=True,
)
def deliver_push_notification(
    self,
    user_id: int,
    title: str,
    body: str,
    data: dict | None = None,
    only_tokens: list[str] | None = None,
    channels: list[str] | None = None,
):
    """Deliver one push to the user's mobile (Expo) tokens and browser (Web Push) subscriptions. On a transient
    provider failure only the recipients that did not get it (Expo tokens / web endpoints) are retried
    (exponential backoff, ``max_retries``), so recipients that already received it never get a duplicate.
    ``channels`` limits delivery to the channels enabled when it was queued (``None``: all configured)."""

    user = User.objects.filter(pk=user_id, is_active=True, is_deleted=False).first()
    if not user:
        return
    try:
        PushNotificationService.send_to_user(user, title, body, data, only_tokens=only_tokens, channels=channels)
    except PushDeliveryTransientError as exc:
        raise self.retry(
            exc=exc,
            countdown=_RETRY_BASE_SECONDS * (2**self.request.retries),
            kwargs={
                "user_id": user_id,
                "title": title,
                "body": body,
                "data": data,
                "only_tokens": exc.tokens,
                "channels": channels,
            },
        ) from exc
