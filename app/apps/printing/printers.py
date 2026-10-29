"""The registry of printers the silent-print agent drives: online state, job counts and the agent's heartbeat."""

from __future__ import annotations

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from .models import Printer, PrintJobStatus

#: A printer counts as online while its agent has reported within this many seconds.
ONLINE_WINDOW_SECONDS = 120
HEARTBEAT_STATUSES = ("idle", "busy", "error")


def is_online(printer: Printer, now=None) -> bool:
    if printer.last_seen_at is None:
        return False
    return (now or timezone.now()) - printer.last_seen_at <= timedelta(seconds=ONLINE_WINDOW_SECONDS)


def with_job_counts(queryset):
    """Annotate printers with the job counts the dashboard shows (queued, printing, failed in the last 24 hours)."""

    since = timezone.now() - timedelta(hours=24)
    live = Q(jobs__is_deleted=False)
    return queryset.annotate(
        jobs_queued=Count("jobs", filter=live & Q(jobs__status=PrintJobStatus.QUEUED)),
        jobs_printing=Count("jobs", filter=live & Q(jobs__status=PrintJobStatus.PRINTING)),
        jobs_failed_24h=Count(
            "jobs", filter=live & Q(jobs__status=PrintJobStatus.FAILED, jobs__finished_at__gte=since)
        ),
    )


class PrinterService:
    @staticmethod
    def clear_other_defaults(keep_pk: int | None) -> None:
        stale = Printer.objects.filter(is_default=True, is_deleted=False)
        if keep_pk is not None:
            stale = stale.exclude(pk=keep_pk)
        stale.update(is_default=False)

    @staticmethod
    def has_jobs(printer: Printer) -> bool:
        return printer.jobs.filter(is_deleted=False).exists()

    @classmethod
    @transaction.atomic
    def heartbeat(cls, printer_name: str, status: str, detail: str = "") -> Printer:
        """Record that the agent is alive for this printer; an unknown name registers a new, active printer."""

        name = printer_name.strip()
        now = timezone.now()
        printer = Printer.objects.select_for_update().filter(name=name, is_deleted=False).first()
        if printer is None:
            try:
                with transaction.atomic():
                    printer = Printer.objects.create(name=name, last_seen_at=now)
            except IntegrityError:  # two first heartbeats raced: the other one created it
                printer = Printer.objects.select_for_update().get(name=name, is_deleted=False)
        printer.last_seen_at = now
        printer.last_status = status
        printer.last_detail = detail.strip()[:255]
        printer.save(update_fields=["last_seen_at", "last_status", "last_detail", "updated_at"])
        return printer
