from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from pypdf import PdfWriter
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.lectures.document_pipeline import (
    DocumentPipelineError,
    QuarantinedDocumentError,
    _render_page,
    _render_thumbnail,
    _scan_if_enabled,
    convert_to_pdf,
    create_viewer_assets,
    document_pipeline_capabilities,
    inspect_lecture_document,
)
from apps.lectures.models import Lecture, LecturePage, LectureProcessingStatus
from apps.lectures.tasks import process_lecture_document
from apps.universities.models import AcademicYear, Faculty, Major, Semester, Subject, University


def make_pdf() -> bytes:
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(output)
    return output.getvalue()


def make_docx() -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
    return output.getvalue()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def academic_structure(db):
    university = University.objects.create(name="Lecture University", code="LU")
    faculty = Faculty.objects.create(university=university, name="Engineering", code="ENG")
    major = Major.objects.create(faculty=faculty, name="Software", code="SWE")
    other_major = Major.objects.create(faculty=faculty, name="Networks", code="NET")
    year = AcademicYear.objects.create(name="Year 1", order=11)
    semester = Semester.objects.create(name="Semester 1", order=11)
    subject = Subject.objects.create(major=major, academic_year=year, semester=semester, name="Algorithms", code="ALG")
    return {
        "university": university,
        "faculty": faculty,
        "major": major,
        "other_major": other_major,
        "year": year,
        "semester": semester,
        "subject": subject,
    }


@pytest.fixture
def manager(db):
    return User.objects.create_user(
        full_name="Content Manager",
        email="lecture-manager@example.com",
        phone_number="+963955000001",
        password="StrongPass123!",
        role=UserRole.CONTENT_MANAGER,
    )


@pytest.fixture
def student(db, academic_structure):
    user = User.objects.create_user(
        full_name="Eligible Student",
        email="lecture-student@example.com",
        phone_number="+963955000002",
        password="StrongPass123!",
        role=UserRole.STUDENT,
    )
    StudentProfile.objects.create(
        user=user,
        university=academic_structure["university"],
        faculty=academic_structure["faculty"],
        major=academic_structure["major"],
        academic_year=academic_structure["year"],
        semester=academic_structure["semester"],
        verification_status=StudentVerificationStatus.APPROVED,
    )
    return user


@pytest.fixture
def other_student(db, academic_structure):
    user = User.objects.create_user(
        full_name="Other Student",
        email="lecture-other@example.com",
        phone_number="+963955000003",
        password="StrongPass123!",
        role=UserRole.STUDENT,
    )
    StudentProfile.objects.create(
        user=user,
        university=academic_structure["university"],
        faculty=academic_structure["faculty"],
        major=academic_structure["other_major"],
        academic_year=academic_structure["year"],
        semester=academic_structure["semester"],
        verification_status=StudentVerificationStatus.APPROVED,
    )
    return user


@pytest.fixture
def ready_lecture(db, manager, academic_structure):
    lecture = Lecture.objects.create(
        subject=academic_structure["subject"],
        title="Private algorithms lecture",
        original_file=ContentFile(make_pdf(), name="original.pdf"),
        original_filename="original.pdf",
        original_mime_type="application/pdf",
        original_size=100,
        original_sha256="a" * 64,
        viewer_pdf=ContentFile(make_pdf(), name="viewer.pdf"),
        status=LectureProcessingStatus.READY,
        page_count=1,
        is_published=True,
        uploaded_by=manager,
    )
    LecturePage.objects.create(
        lecture=lecture,
        page_number=1,
        rendered_file=ContentFile(b"\x89PNG\r\n\x1a\n", name="page.png"),
        text_content="Algorithms overview",
    )
    return lecture


@pytest.mark.django_db
def test_dashboard_upload_queues_private_lecture(api_client, manager, academic_structure):
    api_client.force_authenticate(manager)
    with patch("apps.lectures.views.process_lecture_document.delay") as delay:
        with patch("apps.lectures.views.transaction.on_commit", side_effect=lambda callback: callback()):
            response = api_client.post(
                "/api/v1/dashboard/lectures/",
                {
                    "subject": academic_structure["subject"].id,
                    "title": "Week 1",
                    "original_file": ContentFile(make_pdf(), name="week-1.pdf"),
                },
                format="multipart",
            )
    assert response.status_code == status.HTTP_201_CREATED
    assert "original_file" not in response.data["data"]
    lecture = Lecture.objects.get(title="Week 1")
    assert lecture.status == LectureProcessingStatus.QUEUED
    delay.assert_called_once_with(lecture.id)


@pytest.mark.django_db
def test_viewer_never_exposes_original_and_enforces_curriculum(api_client, ready_lecture, student, other_student):
    api_client.force_authenticate(student)
    manifest = api_client.get(f"/api/v1/lectures/{ready_lecture.id}/viewer/manifest/")
    assert manifest.status_code == status.HTTP_200_OK
    assert "original_file" not in manifest.data["data"]
    session = api_client.post(f"/api/v1/lectures/{ready_lecture.id}/viewer/session/")
    token = session.data["data"]["session_token"]
    page = api_client.get(
        f"/api/v1/lectures/{ready_lecture.id}/viewer/pages/1/",
        HTTP_X_VIEWER_SESSION=token,
    )
    assert page.status_code == status.HTTP_200_OK
    assert page["Cache-Control"] == "private, no-store, max-age=0"
    assert page["Content-Disposition"].startswith("inline")
    assert b"".join(page.streaming_content).startswith(b"\x89PNG")

    api_client.force_authenticate(other_student)
    denied = api_client.get(f"/api/v1/lectures/{ready_lecture.id}/viewer/manifest/")
    assert denied.status_code == status.HTTP_404_NOT_FOUND
    original = api_client.get(f"/api/v1/dashboard/lectures/{ready_lecture.id}/original/")
    assert original.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_viewer_session_expiry_and_notes_are_isolated(api_client, ready_lecture, student, other_student):
    api_client.force_authenticate(student)
    create = api_client.post(
        f"/api/v1/lectures/{ready_lecture.id}/notes/",
        {"page_number": 1, "content": "<script>alert(1)</script>Important", "anchor_data": {"x": 10}},
        format="json",
        HTTP_IDEMPOTENCY_KEY="stable-note-key",
    )
    assert create.status_code == status.HTTP_201_CREATED
    note = create.data["data"]
    assert "script" not in note["content"]
    replay = api_client.post(
        f"/api/v1/lectures/{ready_lecture.id}/notes/",
        {"page_number": 1, "content": "Different"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="stable-note-key",
    )
    assert replay.status_code == status.HTTP_200_OK
    assert replay.data["code"] == "LECTURE_NOTE_IDEMPOTENT_REPLAY"

    conflict = api_client.patch(
        f"/api/v1/lectures/{ready_lecture.id}/notes/{note['id']}/",
        {"version": 999, "content": "Changed"},
        format="json",
    )
    assert conflict.status_code == status.HTTP_409_CONFLICT
    api_client.force_authenticate(other_student)
    hidden = api_client.get(f"/api/v1/lectures/{ready_lecture.id}/notes/{note['id']}/")
    assert hidden.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_lecture_list_query_count_is_not_linear(api_client, manager, student, academic_structure):
    for number in range(50):
        Lecture.objects.create(
            subject=academic_structure["subject"],
            title=f"Lecture {number}",
            original_file=ContentFile(make_pdf(), name=f"original-{number}.pdf"),
            original_filename=f"original-{number}.pdf",
            original_mime_type="application/pdf",
            original_size=100,
            original_sha256=f"{number:064x}",
            viewer_pdf=ContentFile(make_pdf(), name=f"viewer-{number}.pdf"),
            status=LectureProcessingStatus.READY,
            page_count=1,
            is_published=True,
            uploaded_by=manager,
        )
    api_client.force_authenticate(student)
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as queries:
        response = api_client.get("/api/v1/lectures/?page_size=50")
    assert response.status_code == status.HTTP_200_OK
    assert len(response.data["data"]["results"]) == 50
    assert len(queries) <= 4


def test_document_inspection_rejects_mismatched_extensions():
    uploaded = SimpleUploadedFile(
        "malicious.pptx",
        b"not a powerpoint",
        content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
    with pytest.raises(DocumentPipelineError):
        inspect_lecture_document(uploaded)


def test_document_inspection_accepts_openxml_and_rejects_archive_bombs(settings):
    uploaded = SimpleUploadedFile(
        "lecture.docx",
        make_docx(),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    inspection = inspect_lecture_document(uploaded)
    assert inspection.extension == ".docx"
    assert inspection.mime_type.endswith("wordprocessingml.document")
    assert len(inspection.sha256) == 64

    settings.LECTURE_MAX_UPLOAD_SIZE = 1
    bomb = BytesIO()
    with zipfile.ZipFile(bomb, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("ppt/slides/slide1.xml", "x" * 32)
    rejected = SimpleUploadedFile(
        "lecture.pptx",
        bomb.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
    with pytest.raises(DocumentPipelineError):
        inspect_lecture_document(rejected)


def test_pipeline_conversion_and_renderer_use_safe_subprocess_arguments():
    with TemporaryDirectory() as directory:
        workspace = Path(directory)
        source = workspace / "input.docx"
        source.write_bytes(make_docx())

        def convert_run(command, **kwargs):
            assert kwargs["shell"] is False
            output_dir = Path(command[command.index("--outdir") + 1])
            output_dir.mkdir(exist_ok=True)
            (output_dir / "input.pdf").write_bytes(make_pdf())
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        with patch("apps.lectures.document_pipeline.shutil.which", return_value="soffice"):
            with patch("apps.lectures.document_pipeline.subprocess.run", side_effect=convert_run):
                converted = convert_to_pdf(source, ".docx", workspace)
        assert converted.read_bytes().startswith(b"%PDF-")

        pdf = workspace / "viewer.pdf"
        pdf.write_bytes(make_pdf())

        def render_run(command, **kwargs):
            assert kwargs["shell"] is False
            Path(command[-1]).with_suffix(".png").write_bytes(b"png")
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        with patch("apps.lectures.document_pipeline.shutil.which", return_value="pdftoppm"):
            with patch("apps.lectures.document_pipeline.subprocess.run", side_effect=render_run):
                assert _render_page(pdf, 1, workspace) == b"png"
                assert _render_thumbnail(pdf, 1, workspace) == b"png"


@pytest.mark.django_db
def test_pipeline_creates_private_assets_and_cleans_prior_page_files(ready_lecture):
    callbacks: list[str] = []
    with patch("apps.lectures.document_pipeline._render_page", return_value=b"new-png"):
        with patch("apps.lectures.document_pipeline._render_thumbnail", return_value=b"small-png"):
            page_count = create_viewer_assets(ready_lecture, status_callback=callbacks.append)
    ready_lecture.refresh_from_db()
    page = ready_lecture.pages.get(page_number=1)
    assert page_count == 1
    assert ready_lecture.viewer_pdf
    assert page.rendered_file.open("rb").read() == b"new-png"
    assert page.thumbnail.open("rb").read() == b"small-png"
    assert callbacks == ["scanning", "converting", "extracting", "rendering"]


def test_scanner_and_capability_status_fail_closed_when_enabled(settings, tmp_path):
    source = tmp_path / "lecture.pdf"
    source.write_bytes(make_pdf())
    settings.DOCUMENT_ANTIVIRUS_ENABLED = True
    with patch("apps.lectures.document_pipeline.shutil.which", return_value=None):
        with pytest.raises(DocumentPipelineError):
            _scan_if_enabled(source)
    with patch("apps.lectures.document_pipeline.shutil.which", return_value="clamscan"):
        with patch(
            "apps.lectures.document_pipeline.subprocess.run",
            return_value=SimpleNamespace(returncode=1, stdout=b"", stderr=b""),
        ):
            with pytest.raises(QuarantinedDocumentError):
                _scan_if_enabled(source)
    with patch("apps.lectures.document_pipeline.shutil.which", return_value=None):
        capabilities = document_pipeline_capabilities()
    assert capabilities["libreoffice_available"] is False
    assert capabilities["supported_extensions"] == [".pdf", ".doc", ".docx", ".ppt", ".pptx"]


@pytest.mark.django_db
def test_document_task_marks_safe_failure(ready_lecture):
    ready_lecture.status = LectureProcessingStatus.QUEUED
    ready_lecture.viewer_pdf.delete(save=False)
    ready_lecture.save(update_fields=["status", "viewer_pdf", "updated_at"])
    with patch("apps.lectures.tasks.create_viewer_assets", side_effect=DocumentPipelineError("Renderer unavailable")):
        result = process_lecture_document.apply(args=[ready_lecture.id]).get()
    ready_lecture.refresh_from_db()
    assert result == "failed"
    assert ready_lecture.status == LectureProcessingStatus.FAILED
    assert ready_lecture.failure_code == "PROCESSING_FAILED"


@pytest.mark.django_db
def test_document_task_quarantines_and_is_idempotent_when_ready(ready_lecture):
    ready_lecture.status = LectureProcessingStatus.QUEUED
    ready_lecture.save(update_fields=["status", "updated_at"])
    with patch("apps.lectures.tasks.create_viewer_assets", side_effect=QuarantinedDocumentError("Rejected")):
        assert process_lecture_document.apply(args=[ready_lecture.id]).get() == "quarantined"
    ready_lecture.refresh_from_db()
    assert ready_lecture.status == LectureProcessingStatus.QUARANTINED
    ready_lecture.status = LectureProcessingStatus.READY
    ready_lecture.viewer_pdf = ContentFile(make_pdf(), name="already-ready.pdf")
    ready_lecture.save(update_fields=["status", "viewer_pdf", "updated_at"])
    assert process_lecture_document.apply(args=[ready_lecture.id]).get() == "ready"
