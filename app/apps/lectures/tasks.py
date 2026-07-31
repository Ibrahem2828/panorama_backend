from __future__ import annotations

import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService

from .document_pipeline import DocumentPipelineError, QuarantinedDocumentError, create_viewer_assets
from .models import Lecture, LectureProcessingStatus

logger = logging.getLogger(__name__)


def _set_status(lecture: Lecture, status: str, *, failure_code: str = "", failure_message: str = "") -> None:
    lecture.status = status
    lecture.failure_code = failure_code
    lecture.failure_message = failure_message
    lecture.save(update_fields=["status", "failure_code", "failure_message", "updated_at"])


@shared_task(
    bind=True,
    autoretry_for=(OSError, TimeoutError),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
    soft_time_limit=150,
    time_limit=180,
)
def process_lecture_document(self, lecture_id: int) -> str:
    """Convert and render a lecture on the dedicated conversion queue.

    The task is idempotent after a successful result. Failures intentionally
    expose only a category in the database/API; converter stderr never leaves
    the worker process.
    """

    with transaction.atomic():
        lecture = (
            Lecture.objects.select_for_update()
            .select_related("uploaded_by")
            .filter(pk=lecture_id, is_deleted=False)
            .first()
        )
        if not lecture:
            return "missing"
        if lecture.status == LectureProcessingStatus.READY and lecture.viewer_pdf:
            return "ready"
        if lecture.status == LectureProcessingStatus.QUARANTINED:
            return "quarantined"
        lecture.status = LectureProcessingStatus.SCANNING
        lecture.processing_task_id = self.request.id or ""
        lecture.failure_code = ""
        lecture.failure_message = ""
        lecture.save(update_fields=["status", "processing_task_id", "failure_code", "failure_message", "updated_at"])

    try:
        page_count = create_viewer_assets(
            lecture,
            status_callback=lambda current_status: _set_status(lecture, current_status),
        )
        lecture.status = LectureProcessingStatus.READY
        lecture.failure_code = ""
        lecture.failure_message = ""
        if lecture.is_published and not lecture.published_at:
            lecture.published_at = timezone.now()
        lecture.save(
            update_fields=[
                "status",
                "failure_code",
                "failure_message",
                "published_at",
                "viewer_pdf",
                "page_count",
                "updated_at",
            ]
        )
        AuditLogService.log(
            actor=lecture.uploaded_by,
            action=AuditAction.LECTURE_PROCESSING_UPDATED,
            target=lecture,
            new_value={"status": lecture.status, "page_count": page_count},
        )
        return "ready"
    except QuarantinedDocumentError:
        _set_status(
            lecture,
            LectureProcessingStatus.QUARANTINED,
            failure_code="ANTIVIRUS_REJECTED",
            failure_message="The document was quarantined during security scanning.",
        )
        logger.warning("lecture_document_quarantined", extra={"lecture_id": lecture.id})
        return "quarantined"
    except DocumentPipelineError as exc:
        _set_status(
            lecture,
            LectureProcessingStatus.FAILED,
            failure_code="PROCESSING_FAILED",
            failure_message=str(exc)[:255],
        )
        logger.warning("lecture_document_processing_failed", extra={"lecture_id": lecture.id})
        return "failed"
    except Exception:
        _set_status(
            lecture,
            LectureProcessingStatus.FAILED,
            failure_code="PROCESSING_FAILED",
            failure_message="Document processing failed.",
        )
        logger.exception("lecture_document_processing_unexpected_failure", extra={"lecture_id": lecture.id})
        raise
