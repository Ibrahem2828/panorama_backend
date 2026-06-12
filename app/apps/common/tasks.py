from celery import shared_task
from django.core.management import call_command


@shared_task(name="apps.common.tasks.cleanup_expired_otp")
def cleanup_expired_otp(retention_days: int = 1) -> None:
    call_command("cleanup_expired_otp", retention_days=retention_days)
