from __future__ import annotations

import hashlib
import logging
from datetime import timedelta

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from django.utils import timezone

from apps.accounts.models import User

from .models import PushDelivery, PushDeliveryStatus
from .services import (
    CHANNEL_EXPO,
    PushDeliveryTransientError,
    PushNotificationService,
    PushReceiptService,
)

logger = logging.getLogger(__name__)

_RETRY_BASE_SECONDS = 30


def _record_gave_up(tokens: list[str], data: dict | None, reason: str) -> None:
    """Every retry failed: keep a trace per recipient so the health page counts them as failures."""

    notification_id = (data or {}).get("notification_id")
    try:
        PushDelivery.objects.bulk_create(
            [
                PushDelivery(
                    notification_id=notification_id if isinstance(notification_id, int) else None,
                    channel=CHANNEL_EXPO,
                    category=str((data or {}).get("category") or ""),
                    token_hash=hashlib.sha256(token.encode()).hexdigest(),
                    status=PushDeliveryStatus.FAILED,
                    error_code=(reason or "retries_exhausted")[:64],
                    attempts=_RETRY_ATTEMPTS,
                )
                for token in tokens
            ]
        )
    except Exception:
        logger.exception("push_give_up_record_failed")


_RETRY_ATTEMPTS = 5


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
        try:
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
        except MaxRetriesExceededError:
            _record_gave_up(exc.tokens, data, "retries_exhausted")
            raise


@shared_task(ignore_result=True)
def check_push_receipts() -> dict:
    """Every few minutes: settle the receipts of pushes sent 15+ minutes ago and switch dead devices off."""

    return PushReceiptService.check_pending()


@shared_task(ignore_result=True)
def purge_old_push_deliveries(days: int = 30) -> int:
    """Delivery rows are only needed for the health page: keep a month."""

    deleted, _ = PushDelivery.objects.filter(created_at__lt=timezone.now() - timedelta(days=days)).delete()
    return deleted
