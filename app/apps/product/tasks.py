from celery import shared_task

from .services import AccountDeletionService


@shared_task(
    bind=True, autoretry_for=(OSError,), retry_backoff=True, retry_jitter=True, max_retries=3, ignore_result=True
)
def execute_due_account_deletions(self) -> int:
    """Run from Beat; individual rows are locked and remain idempotent across retries."""

    return AccountDeletionService.execute_due()
