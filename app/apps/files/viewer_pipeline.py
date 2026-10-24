"""Library file -> protected page images, reusing the lecture rendering engine (sandboxed LibreOffice + poppler)."""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from PIL import Image, ImageOps
from pypdf import PdfReader

from apps.lectures.document_pipeline import (
    DocumentPipelineError,
    _copy_field_file_to_path,
    _render_page,
    _render_thumbnail,
    _scan_if_enabled,
    convert_to_pdf,
)

from .models import FilePage, FileResource

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
THUMBNAIL_WIDTH = 240
#: A phone never needs more than this many pixels of a screenshot-like image; keeps pages small and fast.
MAX_IMAGE_SIDE = 3000


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def _image_page(source: Path) -> tuple[bytes, bytes, int, int]:
    """Re-encode an uploaded image as PNG (drops EXIF/metadata) plus its thumbnail."""

    try:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened)
            image = image.convert("RGB")
    except Exception as exc:  # noqa: BLE001
        raise DocumentPipelineError("The image is unreadable.") from exc
    image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    width, height = image.size
    thumb = image.copy()
    thumb.thumbnail((THUMBNAIL_WIDTH, THUMBNAIL_WIDTH * 4))
    return _png_bytes(image), _png_bytes(thumb), width, height


def _delete_existing_pages(resource: FileResource) -> None:
    for page in resource.pages.all():
        if page.rendered_file:
            page.rendered_file.delete(save=False)
        if page.thumbnail:
            page.thumbnail.delete(save=False)
    resource.pages.all().delete()


def _png_size(data: bytes) -> tuple[int, int]:
    try:
        with Image.open(io.BytesIO(data)) as image:
            return image.size
    except Exception:  # noqa: BLE001
        return 0, 0


def build_file_pages(resource: FileResource, status_callback=None) -> int:
    """Render every page of ``resource`` into private ``FilePage`` rows. Temporary data is removed on return."""

    def report(status: str) -> None:
        if status_callback:
            status_callback(status)

    with tempfile.TemporaryDirectory(prefix="panorama-file-") as temp_directory:
        workspace = Path(temp_directory)
        extension = Path(resource.file.name).suffix.lower()
        source = workspace / f"input{extension}"
        _copy_field_file_to_path(resource.file, source)
        report("scanning")
        _scan_if_enabled(source)

        if extension in IMAGE_EXTENSIONS:
            report("rendering")
            image, thumbnail, width, height = _image_page(source)
            _delete_existing_pages(resource)
            FilePage.objects.create(
                file_resource=resource,
                page_number=1,
                rendered_file=ContentFile(image, name="page-1.png"),
                thumbnail=ContentFile(thumbnail, name="thumbnail-1.png"),
                text_content="",
                width=width,
                height=height,
            )
            resource.pages_count = 1
            resource.save(update_fields=["pages_count", "updated_at"])
            return 1

        report("converting")
        pdf = convert_to_pdf(source, extension, workspace)
        report("extracting")
        try:
            reader = PdfReader(str(pdf), strict=False)
            page_count = len(reader.pages)
        except Exception as exc:  # noqa: BLE001
            raise DocumentPipelineError("Converted PDF is unreadable.") from exc
        if not 1 <= page_count <= getattr(settings, "LECTURE_MAX_PAGES", 500):
            raise DocumentPipelineError("Document page count is outside the permitted limit.")
        _delete_existing_pages(resource)
        report("rendering")
        for page_number, pdf_page in enumerate(reader.pages, start=1):
            image = _render_page(pdf, page_number, workspace)
            thumbnail = _render_thumbnail(pdf, page_number, workspace)
            width, height = _png_size(image)
            FilePage.objects.create(
                file_resource=resource,
                page_number=page_number,
                rendered_file=ContentFile(image, name=f"page-{page_number}.png"),
                thumbnail=ContentFile(thumbnail, name=f"thumbnail-{page_number}.png"),
                text_content=(pdf_page.extract_text() or "")[:200_000],
                width=width,
                height=height,
            )
        resource.pages_count = page_count
        resource.save(update_fields=["pages_count", "updated_at"])
        return page_count
