from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import AuditLog

logger = logging.getLogger(__name__)

AUDIT_PURGE_BATCH_SIZE = 1000


@shared_task(ignore_result=True)
def purge_expired_audit_logs(batch_size: int = AUDIT_PURGE_BATCH_SIZE) -> int:
    """Delete audit rows older than ``AUDIT_LOG_RETENTION_DAYS`` in bounded batches.

    ``0`` or a negative value keeps the trail forever. The cutoff is computed once, so rows written while the task
    runs are never touched, and a row newer than the window is never deleted. Deletion goes through the one explicit
    manager method; a single summary line is logged (never one audit row per purged row). Returns rows deleted.
    """

    retention_days = int(getattr(settings, "AUDIT_LOG_RETENTION_DAYS", 0) or 0)
    if retention_days <= 0:
        logger.info(
            "audit_retention_purge_skipped", extra={"event": "audit_retention_purge_skipped", "reason": "keep_forever"}
        )
        return 0
    cutoff = timezone.now() - timedelta(days=retention_days)
    total = 0
    batches = 0
    while True:
        deleted = AuditLog.objects.purge_older_than(cutoff, limit=batch_size)
        if deleted == 0:
            break
        total += deleted
        batches += 1
    logger.info(
        "audit_retention_purge_completed",
        extra={
            "event": "audit_retention_purge_completed",
            "deleted": total,
            "batches": batches,
            "retention_days": retention_days,
            "cutoff": cutoff.isoformat(),
        },
    )
    return total
