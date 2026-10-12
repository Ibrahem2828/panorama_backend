from __future__ import annotations

from celery import shared_task
from django.core.management import call_command


@shared_task(ignore_result=True)
def purge_expired_sensitive_data() -> None:
    """Daily retention run (see ``purge_expired_sensitive_data`` for what is removed)."""

    call_command("purge_expired_sensitive_data")


@shared_task(ignore_result=True)
def flush_expired_jwt_tokens() -> None:
    """Daily SimpleJWT housekeeping: drop expired outstanding/blacklisted tokens so the tables stay small."""

    call_command("flushexpiredtokens")
