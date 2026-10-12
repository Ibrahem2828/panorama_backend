from __future__ import annotations

import io
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.exceptions import ValidationError

from apps.common.file_validation import (
    DOCUMENT_POLICY,
    LECTURE_DOCUMENT_POLICY,
    UploadPolicy,
    detect_content_type,
    is_strict_text,
    validate_document_upload,
    validate_image_upload,
    validate_uploaded_file,
)

PNG = bytes.fromhex("89504E470D0A1A0A") + b"\x00\x00\x00\rIHDR" + b"\x00" * 20
JPEG = bytes.fromhex("FFD8FFE000104A464946") + b"\x00" * 20
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 20
PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n"
OLE = bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 64

TEXT_POLICY = UploadPolicy(
    allowed_extensions=frozenset({"txt", "csv"}),
    allowed_mime_types=frozenset({"text/plain", "text/csv"}),
    max_size_bytes=1024 * 1024,
)


def _upload(name: str, content: bytes, content_type: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, content_type=content_type)


def _docx() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
    return output.getvalue()


@pytest.mark.parametrize(
    ("name", "content", "mime"),
    [
        ("a.pdf", PDF, "application/pdf"),
        ("a.png", PNG, "image/png"),
        ("a.jpg", JPEG, "image/jpeg"),
        ("a.jpeg", JPEG, "image/jpg"),
        ("a.webp", WEBP, "image/webp"),
        # Browsers/clients that send no useful type are fine; the content decides.
        ("a.pdf", PDF, ""),
        ("a.png", PNG, "application/octet-stream"),
    ],
)
def test_valid_binary_uploads_are_accepted(name, content, mime):
    upload = _upload(name, content, mime)
    assert validate_document_upload(upload) is upload


@pytest.mark.parametrize("name", ["a.pdf", "a.png", "a.jpg", "a.webp"])
@pytest.mark.parametrize("mime", ["application/pdf", "image/png", "image/jpeg", "image/webp"])
def test_binary_formats_are_rejected_when_content_is_not_recognised(name, mime):
    """The client MIME must never substitute for failed content detection."""
    with pytest.raises(ValidationError, match="content does not match"):
        validate_document_upload(_upload(name, b"just some plain bytes, not a real file", mime))


def test_pdf_must_start_with_signature_and_polyglots_are_rejected():
    html_polyglot = b"<html><script>alert(1)</script>\n" + PDF
    with pytest.raises(ValidationError):
        validate_document_upload(_upload("x.pdf", html_polyglot, "application/pdf"))
    with pytest.raises(ValidationError):
        validate_document_upload(_upload("x.pdf", b"\x00" * 16 + PDF, "application/pdf"))
    assert detect_content_type(b"  %PDF-1.7", "pdf") is None
    assert detect_content_type(PDF, "pdf") == "application/pdf"


def test_extension_detected_and_supplied_types_must_agree():
    with pytest.raises(ValidationError, match="extension"):
        validate_document_upload(_upload("x.png", PDF, "application/pdf"))
    # Right extension and right content but a contradicting client type.
    with pytest.raises(ValidationError, match="declared file type"):
        validate_document_upload(_upload("x.pdf", PDF, "image/png"))
    with pytest.raises(ValidationError, match="declared file type"):
        validate_image_upload(_upload("x.png", PNG, "text/html"))
    with pytest.raises(ValidationError, match="Unsupported file extension"):
        validate_document_upload(_upload("x.html", b"<html>", "text/html"))


def test_png_with_jpeg_content_is_rejected():
    with pytest.raises(ValidationError, match="extension"):
        validate_image_upload(_upload("x.png", JPEG, "image/png"))


def test_office_uploads_need_matching_container_signature():
    docx = _upload("x.docx", _docx(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert validate_uploaded_file(docx, LECTURE_DOCUMENT_POLICY) is docx
    assert validate_uploaded_file(_upload("x.doc", OLE, "application/msword"), LECTURE_DOCUMENT_POLICY)
    assert validate_uploaded_file(_upload("x.ppt", OLE, "application/vnd.ms-powerpoint"), LECTURE_DOCUMENT_POLICY)
    with pytest.raises(ValidationError):
        validate_uploaded_file(
            _upload(
                "x.docx", b"not a zip at all", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            LECTURE_DOCUMENT_POLICY,
        )
    with pytest.raises(ValidationError, match="extension"):
        validate_uploaded_file(_upload("x.doc", _docx(), "application/msword"), LECTURE_DOCUMENT_POLICY)
    with pytest.raises(ValidationError, match="extension"):
        validate_uploaded_file(_upload("x.pdf", OLE, "application/pdf"), LECTURE_DOCUMENT_POLICY)


def test_document_policy_is_unchanged_for_allowed_types():
    assert "pdf" in DOCUMENT_POLICY.allowed_extensions


def test_plain_text_is_allowed_only_after_strict_check():
    assert validate_uploaded_file(_upload("a.txt", "héllo\nworld\t!".encode(), "text/plain"), TEXT_POLICY)
    assert validate_uploaded_file(_upload("a.csv", b"a,b\r\n1,2\r\n", "text/csv"), TEXT_POLICY)
    for bad in (b"abc\x00def", b"abc\x01def", b"\xff\xfe\xfa invalid utf8", b"bell\x07"):
        with pytest.raises(ValidationError):
            validate_uploaded_file(_upload("a.txt", bad, "text/plain"), TEXT_POLICY)
    # Binary content with a text extension and a text MIME is still rejected.
    with pytest.raises(ValidationError):
        validate_uploaded_file(_upload("a.txt", PNG, "text/plain"), TEXT_POLICY)
    with pytest.raises(ValidationError, match="declared file type"):
        validate_uploaded_file(_upload("a.txt", b"hello", "application/pdf"), TEXT_POLICY)


def test_is_strict_text_tolerates_only_a_truncated_trailing_character():
    assert is_strict_text(("é" * 10).encode()[:-1])  # cut in the middle of the last sequence
    assert not is_strict_text(b"ab\xffcd")
    assert not is_strict_text(b"")
