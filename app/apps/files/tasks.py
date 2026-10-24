from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from apps.lectures.document_pipeline import DocumentPipelineError, QuarantinedDocumentError

from .models import IN_FLIGHT_FILE_STATUSES, FileProcessingStatus, FileResource
from .viewer_pipeline import build_file_pages

logger = logging.getLogger(__name__)


def _set_status(resource: FileResource, status: str, *, failure_code: str = "", failure_message: str = "") -> None:
    resource.processing_status = status
    resource.failure_code = failure_code
    resource.failure_message = failure_message
    resource.save(update_fields=["processing_status", "failure_code", "failure_message", "updated_at"])


@shared_task(
    bind=True,
    autoretry_for=(OSError, TimeoutError),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
    soft_time_limit=900,
    time_limit=960,
)
def process_file_resource(self, file_id: int) -> str:
    """Render a library file into protected page images on the conversion queue. Idempotent once ready."""

    with transaction.atomic():
        resource = FileResource.objects.select_for_update().filter(pk=file_id, is_deleted=False).first()
        if resource is None:
            return "missing"
        if resource.processing_status == FileProcessingStatus.READY and resource.pages.exists():
            return "ready"
        if resource.processing_status == FileProcessingStatus.QUARANTINED:
            return "quarantined"
        resource.processing_status = FileProcessingStatus.SCANNING
        resource.processing_task_id = self.request.id or ""
        resource.failure_code = ""
        resource.failure_message = ""
        resource.save(
            update_fields=["processing_status", "processing_task_id", "failure_code", "failure_message", "updated_at"]
        )
    try:
        build_file_pages(resource, status_callback=lambda current: _set_status(resource, current))
    except QuarantinedDocumentError:
        _set_status(
            resource,
            FileProcessingStatus.QUARANTINED,
            failure_code="ANTIVIRUS_REJECTED",
            failure_message="The file was quarantined during security scanning.",
        )
        return "quarantined"
    except DocumentPipelineError as exc:
        _set_status(
            resource, FileProcessingStatus.FAILED, failure_code="PROCESSING_FAILED", failure_message=str(exc)[:255]
        )
        logger.warning("file_processing_failed", extra={"file_id": resource.pk})
        return "failed"
    except Exception:
        _set_status(
            resource,
            FileProcessingStatus.FAILED,
            failure_code="PROCESSING_FAILED",
            failure_message="File processing failed.",
        )
        logger.exception("file_processing_unexpected_failure", extra={"file_id": resource.pk})
        raise
    resource.processing_status = FileProcessingStatus.READY
    resource.failure_code = ""
    resource.failure_message = ""
    resource.processed_at = timezone.now()
    resource.save(update_fields=["processing_status", "failure_code", "failure_message", "processed_at", "updated_at"])
    return "ready"


@shared_task(ignore_result=True)
def fail_stuck_files(max_age_minutes: int = 30) -> int:
    """Mark files whose worker died mid-conversion as failed so staff can reprocess them."""

    cutoff = timezone.now() - timedelta(minutes=max_age_minutes)
    return FileResource.objects.filter(
        processing_status__in=IN_FLIGHT_FILE_STATUSES, updated_at__lt=cutoff, is_deleted=False
    ).update(
        processing_status=FileProcessingStatus.FAILED,
        failure_code="processing_timeout",
        failure_message="Processing did not finish. Reprocess the file.",
        updated_at=timezone.now(),
    )


def dispatch_file_processing(file_id: int) -> None:
    """Queue processing after commit; a broker outage must not fail the request (staff can reprocess)."""

    def send() -> None:
        try:
            process_file_resource.delay(file_id)
        except Exception:
            FileResource.objects.filter(pk=file_id, processing_status=FileProcessingStatus.QUEUED).update(
                processing_status=FileProcessingStatus.FAILED,
                failure_code="DISPATCH_FAILED",
                failure_message="Processing dispatch failed. Reprocess from the dashboard.",
            )
            logger.exception("file_processing_dispatch_failed", extra={"file_id": file_id})

    transaction.on_commit(send)
