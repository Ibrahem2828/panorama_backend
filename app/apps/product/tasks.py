from celery import shared_task

from .services import AccountDeletionService


@shared_task(
    bind=True, autoretry_for=(OSError,), retry_backoff=True, retry_jitter=True, max_retries=3, ignore_result=True
)
def execute_due_account_deletions(self) -> int:
    """Run from Beat; individual rows are locked and remain idempotent across retries."""

    return AccountDeletionService.execute_due()


IDEMPOTENCY_PURGE_BATCH_SIZE = 1000


@shared_task(ignore_result=True)
def purge_expired_idempotency_records(batch_size: int = IDEMPOTENCY_PURGE_BATCH_SIZE) -> int:
    """Delete expired idempotency rows in bounded batches (kept off the request hot path).

    Returns the number of rows deleted. Each batch is its own short transaction so the purge never holds a long
    lock on a hot table.
    """

    from django.utils import timezone

    from .models import IdempotencyRecord

    total = 0
    while True:
        ids = list(
            IdempotencyRecord.objects.filter(expires_at__lte=timezone.now())
            .order_by("pk")
            .values_list("pk", flat=True)[:batch_size]
        )
        if not ids:
            return total
        deleted, _ = IdempotencyRecord.objects.filter(pk__in=ids).delete()
        total += deleted
        if deleted == 0:  # rows vanished concurrently; avoid spinning
            return total
