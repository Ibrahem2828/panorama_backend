from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.files.base import ContentFile
from pypdf import PdfReader

from apps.common.file_validation import LECTURE_DOCUMENT_POLICY, validate_uploaded_file
from apps.files.document_inspection import compute_sha256

from .models import Lecture, LecturePage

OLE_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
PDF_SIGNATURE = b"%PDF-"
ZIP_SIGNATURE = b"PK\x03\x04"
MIME_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


class DocumentPipelineError(RuntimeError):
    """A safe, user-independent document processing failure."""


class QuarantinedDocumentError(DocumentPipelineError):
    """The optional antivirus scanner rejected the upload."""


@dataclass(frozen=True)
class LectureDocumentInspection:
    extension: str
    mime_type: str
    sha256: str
    size: int


def _restore_position(uploaded_file, position: int | None) -> None:
    if position is not None and hasattr(uploaded_file, "seek"):
        uploaded_file.seek(position)


def _read_head(uploaded_file, size: int = 4096) -> bytes:
    position = uploaded_file.tell() if hasattr(uploaded_file, "tell") else None
    try:
        if hasattr(uploaded_file, "seek"):
            uploaded_file.seek(0)
        return uploaded_file.read(size)
    finally:
        _restore_position(uploaded_file, position)


def _validate_openxml(uploaded_file, extension: str) -> None:
    position = uploaded_file.tell() if hasattr(uploaded_file, "tell") else None
    try:
        if hasattr(uploaded_file, "seek"):
            uploaded_file.seek(0)
        with zipfile.ZipFile(uploaded_file) as archive:
            members = archive.infolist()
            if len(members) > 2_000:
                raise DocumentPipelineError("Document archive has too many entries.")
            uncompressed_size = sum(member.file_size for member in members)
            if uncompressed_size > getattr(settings, "LECTURE_MAX_UPLOAD_SIZE", 50 * 1024 * 1024) * 20:
                raise DocumentPipelineError("Document archive expands beyond the permitted limit.")
            names = set(archive.namelist())
            required_prefix = "word/" if extension == ".docx" else "ppt/"
            if "[Content_Types].xml" not in names or not any(name.startswith(required_prefix) for name in names):
                raise DocumentPipelineError("Document content does not match the declared format.")
    except zipfile.BadZipFile as exc:
        raise DocumentPipelineError("Document archive is invalid.") from exc
    finally:
        _restore_position(uploaded_file, position)


def inspect_lecture_document(uploaded_file) -> LectureDocumentInspection:
    """Validate type/signature and return metadata without persisting an upload."""

    validate_uploaded_file(uploaded_file, LECTURE_DOCUMENT_POLICY)
    extension = Path(str(getattr(uploaded_file, "name", ""))).suffix.lower()
    head = _read_head(uploaded_file)
    if extension == ".pdf" and not head.startswith(PDF_SIGNATURE):
        raise DocumentPipelineError("Document content does not match the declared format.")
    if extension in {".doc", ".ppt"} and not head.startswith(OLE_SIGNATURE):
        raise DocumentPipelineError("Document content does not match the declared format.")
    if extension in {".docx", ".pptx"}:
        if not head.startswith(ZIP_SIGNATURE):
            raise DocumentPipelineError("Document content does not match the declared format.")
        _validate_openxml(uploaded_file, extension)
    return LectureDocumentInspection(
        extension=extension,
        mime_type=MIME_BY_EXTENSION[extension],
        sha256=compute_sha256(uploaded_file),
        size=int(getattr(uploaded_file, "size", 0) or 0),
    )


def _copy_field_file_to_path(field_file, destination: Path) -> None:
    with field_file.open("rb") as source, destination.open("wb") as target:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            target.write(chunk)


def _scan_if_enabled(source: Path) -> None:
    if not getattr(settings, "DOCUMENT_ANTIVIRUS_ENABLED", False):
        return
    scanner = shutil.which("clamscan")
    if not scanner:
        raise DocumentPipelineError("Antivirus scanning is enabled but unavailable.")
    result = subprocess.run(
        [scanner, "--no-summary", str(source)],
        check=False,
        capture_output=True,
        shell=False,
        timeout=60,
    )
    if result.returncode == 1:
        raise QuarantinedDocumentError("Document failed antivirus scanning.")
    if result.returncode != 0:
        raise DocumentPipelineError("Antivirus scanner failed.")


def convert_to_pdf(source: Path, extension: str, workspace: Path) -> Path:
    if extension == ".pdf":
        destination = workspace / "viewer.pdf"
        shutil.copyfile(source, destination)
        return destination
    soffice = shutil.which("soffice")
    if not soffice:
        raise DocumentPipelineError("Document conversion capability is unavailable.")
    output_dir = workspace / "output"
    profile_dir = workspace / "profile"
    output_dir.mkdir()
    profile_dir.mkdir()
    result = subprocess.run(
        [
            soffice,
            "--headless",
            "--nologo",
            "--nodefault",
            "--nofirststartwizard",
            "--nolockcheck",
            f"-env:UserInstallation={profile_dir.as_uri()}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(output_dir),
            str(source),
        ],
        check=False,
        capture_output=True,
        shell=False,
        timeout=getattr(settings, "DOCUMENT_CONVERSION_TIME_LIMIT", 180),
        env={**os.environ, "HOME": str(profile_dir)},
    )
    output = output_dir / f"{source.stem}.pdf"
    if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        raise DocumentPipelineError("Document conversion failed.")
    return output


def _render_page(pdf: Path, page_number: int, workspace: Path) -> bytes:
    renderer = shutil.which("pdftoppm")
    if not renderer:
        raise DocumentPipelineError("Page rendering capability is unavailable.")
    output_prefix = workspace / f"page-{page_number}"
    result = subprocess.run(
        [
            renderer,
            "-f",
            str(page_number),
            "-l",
            str(page_number),
            "-png",
            "-singlefile",
            str(pdf),
            str(output_prefix),
        ],
        check=False,
        capture_output=True,
        shell=False,
        timeout=getattr(settings, "DOCUMENT_CONVERSION_TIME_LIMIT", 180),
    )
    output = output_prefix.with_suffix(".png")
    if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        raise DocumentPipelineError("Page rendering failed.")
    return output.read_bytes()


def _render_thumbnail(pdf: Path, page_number: int, workspace: Path) -> bytes:
    renderer = shutil.which("pdftoppm")
    if not renderer:
        raise DocumentPipelineError("Thumbnail rendering capability is unavailable.")
    output_prefix = workspace / f"thumbnail-{page_number}"
    result = subprocess.run(
        [
            renderer,
            "-f",
            str(page_number),
            "-l",
            str(page_number),
            "-scale-to-x",
            "240",
            "-scale-to-y",
            "-1",
            "-png",
            "-singlefile",
            str(pdf),
            str(output_prefix),
        ],
        check=False,
        capture_output=True,
        shell=False,
        timeout=getattr(settings, "DOCUMENT_CONVERSION_TIME_LIMIT", 180),
    )
    output = output_prefix.with_suffix(".png")
    if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        raise DocumentPipelineError("Thumbnail rendering failed.")
    return output.read_bytes()


def _delete_existing_page_files(lecture: Lecture) -> None:
    for page in lecture.pages.all():
        if page.rendered_file:
            page.rendered_file.delete(save=False)
        if page.thumbnail:
            page.thumbnail.delete(save=False)
    lecture.pages.all().delete()


def create_viewer_assets(lecture: Lecture, status_callback=None) -> int:
    """Create private viewer assets; temporary input/output is removed on return."""

    with tempfile.TemporaryDirectory(prefix="panorama-lecture-") as temp_directory:
        workspace = Path(temp_directory)
        source = workspace / f"input{Path(lecture.original_filename).suffix.lower()}"
        _copy_field_file_to_path(lecture.original_file, source)
        if status_callback:
            status_callback("scanning")
        _scan_if_enabled(source)
        if status_callback:
            status_callback("converting")
        pdf = convert_to_pdf(source, source.suffix, workspace)
        if status_callback:
            status_callback("extracting")
        try:
            reader = PdfReader(str(pdf), strict=False)
            page_count = len(reader.pages)
        except Exception as exc:  # noqa: BLE001
            raise DocumentPipelineError("Converted PDF is unreadable.") from exc
        if not 1 <= page_count <= getattr(settings, "LECTURE_MAX_PAGES", 500):
            raise DocumentPipelineError("Document page count is outside the permitted limit.")
        _delete_existing_page_files(lecture)
        with pdf.open("rb") as viewer_source:
            lecture.viewer_pdf.save("viewer.pdf", File(viewer_source), save=False)
        if status_callback:
            status_callback("rendering")
        for page_number, pdf_page in enumerate(reader.pages, start=1):
            image = _render_page(pdf, page_number, workspace)
            thumbnail = _render_thumbnail(pdf, page_number, workspace)
            text = pdf_page.extract_text() or ""
            LecturePage.objects.create(
                lecture=lecture,
                page_number=page_number,
                rendered_file=ContentFile(image, name=f"page-{page_number}.png"),
                thumbnail=ContentFile(thumbnail, name=f"thumbnail-{page_number}.png"),
                text_content=text[:200_000],
            )
        lecture.page_count = page_count
        lecture.save(update_fields=["viewer_pdf", "page_count", "updated_at"])
        return page_count


def document_pipeline_capabilities() -> dict[str, object]:
    return {
        "libreoffice_available": bool(shutil.which("soffice")),
        "libreoffice_path": shutil.which("soffice") or "",
        "poppler_available": bool(shutil.which("pdftoppm")),
        "poppler_path": shutil.which("pdftoppm") or "",
        "antivirus_enabled": bool(getattr(settings, "DOCUMENT_ANTIVIRUS_ENABLED", False)),
        "antivirus_available": bool(shutil.which("clamscan")),
        "supported_extensions": [".pdf", ".doc", ".docx", ".ppt", ".pptx"],
    }
