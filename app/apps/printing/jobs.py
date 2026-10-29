"""Printing an order's files from the dashboard: queue a job per file, prepare the print-ready PDF, follow its status."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError

from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService

from .models import (
    Printer,
    PrintJob,
    PrintJobStatus,
    PrintOrder,
    PrintOrderItem,
    PrintOrderSource,
    PrintOrderStatus,
    PrintSides,
)
from .pdf_engine import build_print_ready


class PrintJobConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "This print job cannot move to that status."
    default_code = "print_job_conflict"


#: Orders in these states may be sent to the printer (a delivered, cancelled or rejected order is history).
PRINTABLE_ORDER_STATUSES = (
    PrintOrderStatus.SUBMITTED,
    PrintOrderStatus.UNDER_REVIEW,
    PrintOrderStatus.ACCEPTED,
    PrintOrderStatus.PRINTING,
    PrintOrderStatus.READY,
)

_ALLOWED = {
    PrintJobStatus.QUEUED: {PrintJobStatus.PRINTING, PrintJobStatus.DONE, PrintJobStatus.FAILED},
    PrintJobStatus.PRINTING: {PrintJobStatus.DONE, PrintJobStatus.FAILED},
    PrintJobStatus.FAILED: {PrintJobStatus.QUEUED},
    PrintJobStatus.DONE: set(),
    # A cancelled job is final: the agent cannot pick it up or finish it (its PATCH gets a 409), staff queue a new one.
    PrintJobStatus.CANCELLED: set(),
}


def item_source(item: PrintOrderItem):
    """The stored file of an item (an upload, the library file, or a ready product's file); None when there is none."""

    if item.uploaded_file:
        return item.uploaded_file
    if item.source_file and item.source_file.file:
        return item.source_file.file
    return None


def item_options(item: PrintOrderItem) -> dict:
    """Everything staff (and the silent-print agent) need to print the item exactly as ordered."""

    snapshot = item.pricing_snapshot or {}
    return {
        "paper_size": item.paper_size,
        "color_mode": item.color_mode,
        "double_sided": item.sides == PrintSides.DOUBLE_SIDED,
        "duplex_flip": item.duplex_flip,
        "orientation": item.orientation,
        "pages_per_sheet": item.pages_per_sheet,
        "n_up_direction": item.n_up_direction,
        "page_range": item.page_range,
        "selected_pages": item.selected_pages or item.pages_count,
        "copies": item.copies,
        "collate": item.collate,
        "binding": item.binding,
        "binding_kind": snapshot.get("binding_kind", ""),
        "cover": item.cover,
        "wire_grams_per_copy": snapshot.get("wire_grams_per_copy"),
    }


def render_item(item: PrintOrderItem) -> tuple[bytes, dict]:
    source = item_source(item)
    if source is None:
        raise ValidationError({"item": "This item has no file to print."})
    opts = item_options(item)
    source.open("rb")
    try:
        return build_print_ready(
            source,
            source.name,
            paper_size=opts["paper_size"],
            page_range=item.page_range,
            pages_per_sheet=item.pages_per_sheet,
            double_sided=opts["double_sided"],
            copies=item.copies,
            collate=item.collate,
            orientation=item.orientation,
            direction=item.n_up_direction,
        )
    finally:
        source.close()


def printer_for_name(name: str) -> Printer | None:
    """The registered printer a job's free-text ``printer_name`` refers to (exact name), if there is one."""

    name = (name or "").strip()
    if not name:
        return None
    return Printer.objects.filter(name=name, is_deleted=False).first()


class PrintJobService:
    @staticmethod
    @transaction.atomic
    def create_for_order(
        order: PrintOrder, actor, *, item_ids: list[int] | None = None, printer_name: str = "", request=None
    ):
        order = PrintOrder.objects.select_for_update().get(pk=order.pk, is_deleted=False)
        if order.source == PrintOrderSource.COUNTER:
            raise ValidationError(
                {"order": "A counter order is printed from the customer's own file, not from the system."}
            )
        if order.status not in PRINTABLE_ORDER_STATUSES:
            raise ValidationError({"order": "This order is finished: it can no longer be sent to the printer."})
        items = list(order.items.filter(is_deleted=False))
        if item_ids:
            items = [item for item in items if item.pk in set(item_ids)]
            if len(items) != len(set(item_ids)):
                raise ValidationError({"item_ids": "Some files do not belong to this order."})
        printable = [item for item in items if item_source(item) is not None]
        if not printable:
            raise ValidationError({"order": "There is no file to print in this order."})
        jobs = []
        for item in printable:
            _data, summary = render_item(item)  # proves the file can be prepared before anything is queued
            jobs.append(
                PrintJob.objects.create(
                    order=order,
                    item=item,
                    created_by=actor,
                    printer_name=printer_name.strip(),
                    printer=printer_for_name(printer_name),
                    sides_printed=summary["faces"],
                    sheets=summary["sheets"],
                    options={**item_options(item), "summary": summary},
                )
            )
        AuditLogService.log(
            actor=actor,
            action=AuditAction.PRINT_JOB_CREATED,
            target=order,
            new_value={"jobs": [job.pk for job in jobs]},
            request=request,
        )
        return jobs

    @staticmethod
    @transaction.atomic
    def set_status(
        job: PrintJob, new_status: str, actor, *, printer_name: str = "", error: str = "", request=None
    ) -> PrintJob:
        job = PrintJob.objects.select_for_update().select_related("order").get(pk=job.pk, is_deleted=False)
        if new_status != job.status and new_status not in _ALLOWED.get(PrintJobStatus(job.status), set()):
            raise PrintJobConflict()
        old = job.status
        job.status = new_status
        now = timezone.now()
        if new_status == PrintJobStatus.PRINTING:
            job.started_at = now
            job.error = ""
        elif new_status == PrintJobStatus.DONE:
            job.started_at = job.started_at or now
            job.finished_at = now
            job.error = ""
        elif new_status == PrintJobStatus.FAILED:
            job.finished_at = now
            job.error = error.strip()[:255]
        elif new_status == PrintJobStatus.QUEUED:
            job.started_at = job.finished_at = None
            job.error = ""
        if printer_name:
            job.printer_name = printer_name.strip()[:150]
            job.printer = printer_for_name(job.printer_name) or job.printer
        job.save()
        order = job.order
        # The first job that starts moves an accepted order to "printing" so the customer sees progress.
        if new_status in (PrintJobStatus.PRINTING, PrintJobStatus.DONE) and order.status == PrintOrderStatus.ACCEPTED:
            from .services import PrintStatusService

            PrintStatusService.change_status(
                order,
                PrintOrderStatus.PRINTING,
                actor,
                public_note="بدأت طباعة طلبك.",
                internal_note="Print job started",
                request=request,
            )
        AuditLogService.log(
            actor=actor,
            action=AuditAction.PRINT_JOB_STATUS_CHANGED,
            target=job,
            old_value={"status": old},
            new_value={"status": new_status, "printer": job.printer_name},
            request=request,
        )
        return job

    @staticmethod
    @transaction.atomic
    def cancel(job: PrintJob, actor, *, reason: str = "", request=None) -> PrintJob:
        """Stop a job that has not finished. The order is left alone, exactly as when a job fails: staff decide what
        happens to the order (print again, deliver by hand, cancel it)."""

        job = PrintJob.objects.select_for_update().get(pk=job.pk, is_deleted=False)
        if job.status == PrintJobStatus.CANCELLED:
            return job  # cancelling twice is harmless
        if job.status not in (PrintJobStatus.QUEUED, PrintJobStatus.PRINTING):
            raise PrintJobConflict("Only a queued or printing job can be cancelled.")
        old = job.status
        job.status = PrintJobStatus.CANCELLED
        job.finished_at = timezone.now()
        job.error = reason.strip()[:255]
        job.save()
        AuditLogService.log(
            actor=actor,
            action=AuditAction.PRINT_JOB_CANCELLED,
            target=job,
            old_value={"status": old},
            new_value={"status": job.status, "reason": job.error, "printer": job.printer_name},
            request=request,
        )
        return job
