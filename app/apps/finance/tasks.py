from celery import shared_task

from .services import PurchaseReminderService


@shared_task
def notify_due_purchase_reminders() -> int:
    """Daily: tells finance staff which "buy again" reminders (ink, cleaning supplies...) have come due."""

    return PurchaseReminderService.notify_due()
