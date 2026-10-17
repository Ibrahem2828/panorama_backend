from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.core.files import File
from django.utils import timezone
from pypdf import PdfReader

from apps.lectures.document_pipeline import DocumentPipelineError, _scan_if_enabled, convert_to_pdf

from .models import PrintConversion, PrintConversionStatus

logger = logging.getLogger(__name__)


def _fail(conversion: PrintConversion, message: str) -> None:
    conversion.status = PrintConversionStatus.FAILED
    conversion.failure_message = message[:255]
    conversion.save(update_fields=["status", "failure_message", "updated_at"])
    if conversion.input_file:
        conversion.input_file.delete(save=True)


@shared_task(bind=True, soft_time_limit=240, time_limit=270)
def convert_print_document(self, conversion_id: int) -> str:
    """Convert an uploaded Word/PowerPoint file to PDF on the conversion queue (the only place with LibreOffice)."""

    conversion = PrintConversion.objects.filter(pk=conversion_id, is_deleted=False).first()
    if not conversion or conversion.status != PrintConversionStatus.PENDING or not conversion.input_file:
        return "skipped"
    try:
        with tempfile.TemporaryDirectory(prefix="panorama-print-") as directory:
            workspace = Path(directory)
            source = workspace / f"input{Path(conversion.original_name).suffix.lower()}"
            with conversion.input_file.open("rb") as src, source.open("wb") as dst:
                for chunk in iter(lambda: src.read(1024 * 1024), b""):
                    dst.write(chunk)
            _scan_if_enabled(source)
            pdf = convert_to_pdf(source, source.suffix, workspace)
            try:
                pages = len(PdfReader(str(pdf), strict=False).pages)
            except Exception as exc:  # noqa: BLE001
                raise DocumentPipelineError("Converted PDF is unreadable.") from exc
            if not 1 <= pages <= getattr(settings, "PRINT_CONVERSION_MAX_PAGES", 300):
                raise DocumentPipelineError("Document page count is outside the permitted limit.")
            with pdf.open("rb") as handle:
                conversion.pdf_file.save(conversion.pdf_name, File(handle), save=False)
        conversion.pages_count = pages
        conversion.status = PrintConversionStatus.READY
        conversion.save(update_fields=["pdf_file", "pages_count", "status", "updated_at"])
        conversion.input_file.delete(save=True)
        return "ready"
    except DocumentPipelineError as exc:
        _fail(conversion, str(exc))
        logger.warning("print_conversion_failed", extra={"conversion_id": conversion_id})
        return "failed"
    except Exception:
        _fail(conversion, "Document conversion failed.")
        logger.exception("print_conversion_unexpected_failure", extra={"conversion_id": conversion_id})
        return "failed"


@shared_task
def purge_expired_print_conversions() -> int:
    """Delete expired conversions and their files (originals and converted PDFs are short-lived by design)."""

    count = 0
    for conversion in PrintConversion.objects.filter(expires_at__lt=timezone.now()):
        if conversion.input_file:
            conversion.input_file.delete(save=False)
        if conversion.pdf_file:
            conversion.pdf_file.delete(save=False)
        conversion.delete()
        count += 1
    return count
