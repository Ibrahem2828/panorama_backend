"""Library files become protected page images: processing, viewer sessions, access control, reprocessing."""

from __future__ import annotations

import io
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.utils import timezone
from PIL import Image
from pypdf import PdfWriter
from rest_framework.test import APIClient

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.files.models import FilePage, FileProcessingStatus, FileResource, FileViewerSession
from apps.files.tasks import fail_stuck_files, process_file_resource
from apps.lectures.document_pipeline import DocumentPipelineError, QuarantinedDocumentError
from apps.universities.models import University


def _png(width=60, height=80) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, "PNG")
    return buffer.getvalue()


def _pdf() -> bytes:
    buffer = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(buffer)
    return buffer.getvalue()


def _client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def world(db):
    university = University.objects.create(name="Viewer University", code="VU")
    admin = User.objects.create_user(
        full_name="Admin",
        email="a@v.test",
        phone_number="+963992000001",
        password="StrongPass123!",
        role=UserRole.ADMIN,
    )
    student = User.objects.create_user(
        full_name="Student",
        email="s@v.test",
        phone_number="+963992000002",
        password="StrongPass123!",
        role=UserRole.STUDENT,
    )
    StudentProfile.objects.create(
        user=student, university=university, verification_status=StudentVerificationStatus.APPROVED
    )
    other = User.objects.create_user(
        full_name="Other",
        email="o@v.test",
        phone_number="+963992000003",
        password="StrongPass123!",
        role=UserRole.STUDENT,
    )
    return {"admin": admin, "student": student, "other": other, "university": university}


def _file(world, *, name="notes.png", content=None, visibility="verified_students_only", **extra) -> FileResource:
    return FileResource.objects.create(
        title="Notes",
        file=ContentFile(content if content is not None else _png(), name=name),
        uploaded_by=world["admin"],
        university=world["university"],
        visibility=visibility,
        **extra,
    )


def _ready_file(world, pages=3) -> FileResource:
    resource = _file(world, processing_status=FileProcessingStatus.READY, pages_count=pages)
    for number in range(1, pages + 1):
        FilePage.objects.create(
            file_resource=resource,
            page_number=number,
            rendered_file=ContentFile(_png(), name=f"p{number}.png"),
            thumbnail=ContentFile(_png(10, 14), name=f"t{number}.png"),
            text_content=f"text of page {number}",
            width=60,
            height=80,
        )
    return resource


def _open_session(client, resource) -> str:
    response = client.post(f"/api/v1/files/{resource.pk}/viewer/session/")
    assert response.status_code == 201, response.data
    return response.data["data"]["session_token"]


def test_image_files_become_one_clean_png_page(world):
    resource = _file(world, name="slide.png", content=_png(120, 90))
    assert process_file_resource.apply(args=(resource.pk,)).get() == "ready"
    resource.refresh_from_db()
    assert resource.processing_status == FileProcessingStatus.READY
    assert resource.pages_count == 1 and resource.processed_at is not None
    page = resource.pages.get()
    assert (page.width, page.height) == (120, 90)
    assert page.thumbnail


def test_processing_is_idempotent_and_reports_failures(world):
    resource = _file(world, name="slide.png")
    process_file_resource.apply(args=(resource.pk,))
    assert process_file_resource.apply(args=(resource.pk,)).get() == "ready"
    assert resource.pages.count() == 1

    broken = _file(world, name="broken.png", content=b"not really an image")
    assert process_file_resource.apply(args=(broken.pk,)).get() == "failed"
    broken.refresh_from_db()
    assert broken.processing_status == FileProcessingStatus.FAILED and broken.failure_code == "PROCESSING_FAILED"


def test_antivirus_rejection_quarantines(world):
    resource = _file(world, name="slide.png")
    with patch("apps.files.viewer_pipeline._scan_if_enabled", side_effect=QuarantinedDocumentError("virus")):
        assert process_file_resource.apply(args=(resource.pk,)).get() == "quarantined"
    resource.refresh_from_db()
    assert resource.processing_status == FileProcessingStatus.QUARANTINED


def test_pdf_goes_through_the_lecture_renderer(world):
    resource = _file(world, name="doc.pdf", content=_pdf())
    with (
        patch("apps.files.viewer_pipeline._render_page", return_value=_png(50, 70)) as page,
        patch("apps.files.viewer_pipeline._render_thumbnail", return_value=_png(10, 14)),
    ):
        assert process_file_resource.apply(args=(resource.pk,)).get() == "ready"
    assert page.call_count == 1
    resource.refresh_from_db()
    assert resource.pages_count == 1 and resource.pages.get().width == 50


def test_conversion_failure_is_a_safe_failed_state(world):
    resource = _file(world, name="doc.pdf", content=_pdf())
    with patch(
        "apps.files.viewer_pipeline.convert_to_pdf", side_effect=DocumentPipelineError("Document conversion failed.")
    ):
        assert process_file_resource.apply(args=(resource.pk,)).get() == "failed"
    resource.refresh_from_db()
    assert resource.failure_message == "Document conversion failed."


def test_viewer_session_pages_thumbnails_and_text(world):
    resource = _ready_file(world)
    client = _client(world["student"])
    token = _open_session(client, resource)
    headers = {"X-Viewer-Session": token}
    manifest = client.get(f"/api/v1/files/{resource.pk}/viewer/manifest/")
    assert manifest.data["data"]["page_count"] == 3 and manifest.data["data"]["ready"] is True
    assert manifest.data["data"]["viewer_capabilities"]["download_original"] is False

    page = client.get(f"/api/v1/files/{resource.pk}/viewer/pages/2/", headers=headers)
    assert page.status_code == 200 and page["Content-Type"] == "image/png"
    assert "no-store" in page["Cache-Control"]
    assert client.get(f"/api/v1/files/{resource.pk}/viewer/pages/2/thumbnail/", headers=headers).status_code == 200
    text = client.get(f"/api/v1/files/{resource.pk}/viewer/pages/3/text/", headers=headers)
    assert text.data["data"]["text"] == "text of page 3"
    assert client.get(f"/api/v1/files/{resource.pk}/viewer/pages/9/", headers=headers).status_code == 404


def test_pages_need_a_valid_session_that_belongs_to_the_user(world):
    resource = _ready_file(world)
    token = _open_session(_client(world["student"]), resource)
    url = f"/api/v1/files/{resource.pk}/viewer/pages/1/"
    assert _client(world["student"]).get(url).status_code == 404  # no header
    assert _client(world["student"]).get(url, headers={"X-Viewer-Session": "not-a-uuid"}).status_code == 404
    # A verified student who is not the session owner cannot reuse the token.
    StudentProfile.objects.create(
        user=world["other"], university=world["university"], verification_status=StudentVerificationStatus.APPROVED
    )
    assert _client(world["other"]).get(url, headers={"X-Viewer-Session": token}).status_code == 404
    FileViewerSession.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert _client(world["student"]).get(url, headers={"X-Viewer-Session": token}).status_code == 404


def test_session_request_budget_is_enforced(world):
    resource = _ready_file(world)
    client = _client(world["student"])
    token = _open_session(client, resource)
    FileViewerSession.objects.update(max_page_requests=2)
    url = f"/api/v1/files/{resource.pk}/viewer/pages/1/"
    assert client.get(url, headers={"X-Viewer-Session": token}).status_code == 200
    assert client.get(url, headers={"X-Viewer-Session": token}).status_code == 200
    assert client.get(url, headers={"X-Viewer-Session": token}).status_code == 404


def test_unverified_students_and_unprocessed_files_get_nothing(world):
    resource = _ready_file(world)
    assert _client(world["other"]).post(f"/api/v1/files/{resource.pk}/viewer/session/").status_code in (403, 404)
    pending = _file(world, name="later.png")
    assert _client(world["student"]).post(f"/api/v1/files/{pending.pk}/viewer/session/").status_code == 404
    manifest = _client(world["student"]).get(f"/api/v1/files/{pending.pk}/viewer/manifest/")
    assert manifest.data["data"]["ready"] is False and manifest.data["data"]["status"] == "pending"


def test_admin_only_files_are_not_viewable_by_students(world):
    resource = _ready_file(world)
    resource.visibility = "admin_only"
    resource.save()
    assert _client(world["student"]).post(f"/api/v1/files/{resource.pk}/viewer/session/").status_code == 404


def test_dashboard_upload_queues_processing_and_reprocess_endpoints(world):
    admin = _client(world["admin"])
    with patch("apps.files.views.dispatch_file_processing") as dispatch:
        created = admin.post(
            "/api/v1/dashboard/files/",
            {
                "title": "Slides",
                "file": SimpleUploadedFile("slides.png", _png(), content_type="image/png"),
                "visibility": "public",
            },
            format="multipart",
        )
        assert created.status_code == 201, created.data
        assert created.data["data"]["processing_status"] == "queued"
        dispatch.assert_called_once_with(created.data["data"]["id"])

    failed = _file(world, processing_status=FileProcessingStatus.FAILED, failure_code="PROCESSING_FAILED")
    with patch("apps.files.views.dispatch_file_processing") as dispatch:
        response = admin.post(f"/api/v1/dashboard/files/{failed.pk}/reprocess/")
        assert response.status_code == 202
        dispatch.assert_called_once_with(failed.pk)
    failed.refresh_from_db()
    assert failed.processing_status == FileProcessingStatus.QUEUED and failed.failure_code == ""
    # A student may not reprocess.
    assert _client(world["student"]).post(f"/api/v1/dashboard/files/{failed.pk}/reprocess/").status_code == 403


def test_reprocess_refuses_files_that_are_being_processed_or_quarantined(world):
    admin = _client(world["admin"])
    busy = _file(world, processing_status=FileProcessingStatus.RENDERING)
    assert admin.post(f"/api/v1/dashboard/files/{busy.pk}/reprocess/").status_code == 409
    quarantined = _file(world, processing_status=FileProcessingStatus.QUARANTINED)
    assert admin.post(f"/api/v1/dashboard/files/{quarantined.pk}/reprocess/").status_code == 409


def test_bulk_reprocess_and_summary(world):
    admin = _client(world["admin"])
    _file(world)  # pending
    _file(world, name="b.png")  # pending
    _file(world, name="c.png", processing_status=FileProcessingStatus.READY)
    _file(world, name="d.png", processing_status=FileProcessingStatus.FAILED)
    summary = admin.get("/api/v1/dashboard/files/processing-summary/").data["data"]
    assert summary["pending"] == 2 and summary["ready"] == 1 and summary["failed"] == 1
    with patch("apps.files.views.dispatch_file_processing"):
        queued = admin.post("/api/v1/dashboard/files/reprocess-bulk/", {"scope": "unprocessed"}, format="json")
    assert queued.data["data"]["queued"] == 2
    assert admin.post("/api/v1/dashboard/files/reprocess-bulk/", {}, format="json").status_code == 400


def test_stuck_files_are_failed_after_the_timeout(world):
    stuck = _file(world, processing_status=FileProcessingStatus.RENDERING)
    FileResource.objects.filter(pk=stuck.pk).update(updated_at=timezone.now() - timedelta(minutes=45))
    assert fail_stuck_files() == 1
    stuck.refresh_from_db()
    assert stuck.processing_status == FileProcessingStatus.FAILED


def test_process_library_files_command(world):
    resource = _file(world, name="cmd.png")
    call_command("process_library_files", "--inline")
    resource.refresh_from_db()
    assert resource.processing_status == FileProcessingStatus.READY
    call_command("process_library_files", "--dry-run")  # nothing left to do


def test_office_uploads_are_accepted_but_not_printable(world):
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
    with patch("apps.files.views.dispatch_file_processing"):
        response = _client(world["admin"]).post(
            "/api/v1/dashboard/files/",
            {
                "title": "Word file",
                "file": SimpleUploadedFile(
                    "notes.docx",
                    buffer.getvalue(),
                    content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ),
                "visibility": "public",
            },
            format="multipart",
        )
    assert response.status_code == 201, response.data
    assert response.data["data"]["is_printable"] is False
    assert response.data["data"]["pages_count"] is None


def test_dashboard_can_filter_files_by_processing_status(world):
    _file(world, name="a.png", processing_status=FileProcessingStatus.FAILED)
    _file(world, name="b.png", processing_status=FileProcessingStatus.READY)
    response = _client(world["admin"]).get("/api/v1/dashboard/files/?processing_status=failed")
    rows = response.data["data"]["results"] if isinstance(response.data["data"], dict) else response.data["data"]
    assert [row["processing_status"] for row in rows] == ["failed"]
