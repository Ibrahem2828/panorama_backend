import zipfile
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

import pytest
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from pypdf import PdfWriter
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.common.tests_phase3 import configure_printing
from apps.printing.models import PrintConversion, PrintConversionStatus, PrintOrder
from apps.printing.tasks import convert_print_document, purge_expired_print_conversions

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def make_docx() -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<w:document/>")
    return buffer.getvalue()


def make_pdf(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        full_name="Normal",
        email="convert@example.com",
        phone_number="+963944440001",
        password="StrongPass123!",
        role=UserRole.NORMAL_USER,
    )


@pytest.fixture
def other(db):
    return User.objects.create_user(
        full_name="Other",
        email="convert-other@example.com",
        phone_number="+963944440002",
        password="StrongPass123!",
        role=UserRole.NORMAL_USER,
    )


@pytest.fixture
def client(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def fake_convert(pages=3):
    def _convert(source, extension, workspace):
        output = workspace / "out.pdf"
        output.write_bytes(make_pdf(pages))
        return output

    return _convert


@pytest.mark.django_db
def test_convert_endpoint_converts_word_and_item_prices_by_converted_pages(
    client, settings, tmp_path, django_capture_on_commit_callbacks
):
    settings.MEDIA_ROOT = str(tmp_path)
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.PRINT_CONVERSION_WAIT_SECONDS = 0
    configure_printing()
    docx = SimpleUploadedFile("Lecture 1.docx", make_docx(), content_type=DOCX_MIME)
    with patch("apps.printing.tasks.convert_to_pdf", side_effect=fake_convert(3)):
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post("/api/v1/printing/convert/", {"file": docx}, format="multipart")
    assert response.status_code == status.HTTP_202_ACCEPTED, response.content
    token = response.json()["data"]["id"]
    response = client.get(f"/api/v1/printing/convert/{token}/")
    assert response.status_code == status.HTTP_200_OK, response.content
    data = response.json()["data"]
    assert data["status"] == "ready"
    assert data["pages_count"] == 3
    assert data["filename"] == "Lecture 1.pdf"

    item = {
        "converted_upload": data["id"],
        "copies": 1,
        "color_mode": "black_white",
        "paper_size": "A4",
        "sides": "one_sided",
        "binding": "none",
    }
    quote = client.post("/api/v1/printing/quote/", {"items": [item]}, format="json")
    assert quote.status_code == status.HTTP_200_OK, quote.content
    assert quote.json()["data"]["items"][0]["pages_count"] == 3

    order = client.post("/api/v1/printing/orders/", {"items": [item]}, format="json")
    assert order.status_code == status.HTTP_201_CREATED, order.content
    stored = PrintOrder.objects.get().items.get()
    assert stored.uploaded_file.name.endswith(".pdf")
    assert stored.pages_count == 3


@pytest.mark.django_db
def test_convert_endpoint_rejects_other_types_and_bad_content(client, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    for name, content in (("a.exe", b"MZ"), ("a.docx", b"not a zip"), ("a.xlsx", b"PK")):
        response = client.post(
            "/api/v1/printing/convert/", {"file": SimpleUploadedFile(name, content)}, format="multipart"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, name
    assert PrintConversion.objects.count() == 0


@pytest.mark.django_db
def test_conversion_is_private_to_its_owner(client, other, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    conversion = PrintConversion.objects.create(
        user=other,
        original_name="x.docx",
        status=PrintConversionStatus.READY,
        pages_count=2,
        expires_at=timezone.now() + timedelta(hours=1),
    )
    conversion.pdf_file.save("x.pdf", ContentFile(make_pdf(2)))
    assert client.get(f"/api/v1/printing/convert/{conversion.token}/").status_code == status.HTTP_404_NOT_FOUND
    configure_printing()
    item = {
        "converted_upload": str(conversion.token),
        "copies": 1,
        "color_mode": "black_white",
        "paper_size": "A4",
        "sides": "one_sided",
        "binding": "none",
    }
    assert client.post("/api/v1/printing/quote/", {"items": [item]}, format="json").status_code == 400


@pytest.mark.django_db
def test_task_marks_failure_and_purge_removes_expired(user, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    conversion = PrintConversion.objects.create(
        user=user, original_name="x.docx", expires_at=timezone.now() - timedelta(minutes=1)
    )
    conversion.input_file.save("x.docx", ContentFile(make_docx()))
    from apps.lectures.document_pipeline import DocumentPipelineError

    with patch("apps.printing.tasks.convert_to_pdf", side_effect=DocumentPipelineError("Document conversion failed.")):
        assert convert_print_document(conversion.pk) == "failed"
    conversion.refresh_from_db()
    assert conversion.status == PrintConversionStatus.FAILED
    assert not conversion.input_file
    assert purge_expired_print_conversions() == 1
    assert PrintConversion.objects.count() == 0
