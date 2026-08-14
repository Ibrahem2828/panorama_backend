from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

import pytest
from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, default_storage
from django.core.management import call_command
from django.http import StreamingHttpResponse
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.common.health_views import _local_media_is_ready
from apps.common.storage import PrivateFileSystemStorage
from apps.files.models import FileAccessTicket, FileResource, FileVisibility
from apps.groups.models import Group, GroupMembership, GroupMembershipStatus
from apps.universities.models import University


def _local_storage_settings(media_root: Path) -> dict[str, object]:
    return {
        "STORAGE_BACKEND": "local",
        "MEDIA_ROOT": media_root,
        "MEDIA_URL": "/media/",
        "STORAGES": {
            "default": {
                "BACKEND": "apps.common.storage.PrivateFileSystemStorage",
                "OPTIONS": {"location": str(media_root), "base_url": "/media/"},
            },
            "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
        },
    }


@pytest.fixture
def isolated_local_storage(tmp_path):
    media_root = tmp_path / "media"
    media_root.mkdir()
    with override_settings(**_local_storage_settings(media_root)):
        yield media_root


def test_private_local_storage_save_read_delete_and_randomizes_name(isolated_local_storage):
    saved_name = default_storage.save("files/original-name.pdf", ContentFile(b"private content"))

    assert isinstance(default_storage, FileSystemStorage)
    assert saved_name.startswith("files/")
    assert saved_name.endswith(".pdf")
    assert "original-name" not in saved_name
    assert default_storage.exists(saved_name)
    with default_storage.open(saved_name, "rb") as stored_file:
        assert stored_file.read() == b"private content"

    default_storage.delete(saved_name)
    assert not default_storage.exists(saved_name)


def test_private_storage_rejects_path_traversal(isolated_local_storage):
    storage = PrivateFileSystemStorage(location=str(isolated_local_storage), base_url="/media/")

    with pytest.raises(SuspiciousFileOperation):
        storage.generate_filename("../other-user.pdf")
    with pytest.raises(SuspiciousFileOperation):
        storage.generate_filename("/absolute.pdf")
    with pytest.raises(SuspiciousFileOperation):
        storage.generate_filename("unsafe" + chr(0) + "name.pdf")


def test_storage_status_write_test_leaves_no_file(isolated_local_storage, capsys):
    call_command("storage_status", "--write-test")

    output = capsys.readouterr().out
    assert "storage_mode=local" in output
    assert "write_test=passed" in output
    assert not list((isolated_local_storage / "healthchecks").glob("*"))


def test_readiness_storage_check_rejects_non_writable_directory(isolated_local_storage, monkeypatch):
    monkeypatch.setattr("apps.common.health_views.os.access", lambda *_args: False)

    assert _local_media_is_ready() is False


def test_storage_backend_validation_and_future_s3_variable_collection(monkeypatch, tmp_path):
    from config.settings.storage import build_storage_settings, resolve_storage_backend

    monkeypatch.setenv("STORAGE_BACKEND", "invalid")
    with pytest.raises(ImproperlyConfigured, match="Unsupported STORAGE_BACKEND"):
        resolve_storage_backend()

    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    for name in (
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "S3_BUCKET_NAME",
        "S3_ENDPOINT_URL",
        "S3_REGION",
        "S3_SIGNATURE_VERSION",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ImproperlyConfigured, match="Missing S3 storage environment variables") as exc_info:
        build_storage_settings(
            base_dir=tmp_path,
            static_root=tmp_path / "staticfiles",
            staticfiles_backend="whitenoise.storage.CompressedManifestStaticFilesStorage",
        )
    assert "S3_ACCESS_KEY_ID" in str(exc_info.value)


def test_legacy_false_storage_variable_uses_local_with_deprecation_warning(monkeypatch):
    from config.settings.storage import resolve_storage_backend

    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    monkeypatch.setenv("USE_S3_STORAGE", "False")

    with pytest.warns(DeprecationWarning, match="USE_S3_STORAGE=False"):
        assert resolve_storage_backend() == "local"


@pytest.mark.django_db
def test_protected_file_stream_requires_ticket_owner_and_authentication(isolated_local_storage):
    owner = User.objects.create_user(
        full_name="Owner",
        email="storage-owner@example.test",
        phone_number="+963900001001",
        password="StrongPass123!",
        role=UserRole.ADMIN,
    )
    other_user = User.objects.create_user(
        full_name="Other",
        email="storage-other@example.test",
        phone_number="+963900001002",
        password="StrongPass123!",
        role=UserRole.NORMAL_USER,
    )
    resource = FileResource.objects.create(
        title="Private document",
        file=ContentFile(b"%PDF-1.4 private", name="source.pdf"),
        uploaded_by=owner,
    )
    ticket = FileAccessTicket.issue(file_resource=resource, user=owner)
    url = f"/api/v1/protected-files/{ticket.token}/"
    client = APIClient()

    anonymous = client.get(url)
    assert anonymous.status_code == status.HTTP_401_UNAUTHORIZED

    client.force_authenticate(other_user)
    foreign_user = client.get(url)
    assert foreign_user.status_code == status.HTTP_404_NOT_FOUND
    ticket.refresh_from_db()
    assert ticket.use_count == 0

    client.force_authenticate(owner)
    issued = client.post(f"/api/v1/files/{resource.pk}/access-ticket/", {"purpose": "view"}, format="json")
    assert issued.status_code == status.HTTP_201_CREATED
    issued_preview_path = urlparse(issued.data["data"]["preview_url"]).path
    assert client.get(issued_preview_path).status_code == status.HTTP_200_OK

    owner_response = client.get(url)
    assert owner_response.status_code == status.HTTP_200_OK
    assert owner_response["Cache-Control"] == "private, no-store, max-age=0"
    assert owner_response["X-Content-Type-Options"] == "nosniff"
    assert owner_response["Content-Disposition"].startswith("inline;")
    assert owner_response["Content-Type"] == "application/pdf"

    # The ticket intentionally supports multiple protected stream requests so a
    # PDF/WebView range-or-retry sequence is not invalidated after its first GET.
    second_owner_response = client.get(url)
    assert second_owner_response.status_code == status.HTTP_200_OK
    ticket.refresh_from_db()
    assert ticket.use_count == 2

    range_ticket = FileAccessTicket.issue(file_resource=resource, user=owner)
    range_response = client.get(f"/api/v1/protected-files/{range_ticket.token}/", HTTP_RANGE="bytes=0-3")
    assert range_response.status_code == status.HTTP_206_PARTIAL_CONTENT
    assert range_response["Accept-Ranges"] == "bytes"
    assert range_response["Content-Range"] == "bytes 0-3/16"
    assert range_response["Content-Length"] == "4"
    range_content = cast(StreamingHttpResponse, range_response).streaming_content
    assert b"".join(cast(Iterable[bytes], range_content)) == b"%PDF"
    range_ticket.refresh_from_db()
    assert range_ticket.use_count == 1

    expired_ticket = FileAccessTicket.issue(file_resource=resource, user=owner)
    expired_ticket.expires_at = timezone.now() - timedelta(seconds=1)
    expired_ticket.save(update_fields=["expires_at", "updated_at"])
    assert client.get(f"/api/v1/protected-files/{expired_ticket.token}/").status_code == status.HTTP_404_NOT_FOUND

    revoked_ticket = FileAccessTicket.issue(file_resource=resource, user=owner)
    revoked_ticket.revoked_at = timezone.now()
    revoked_ticket.save(update_fields=["revoked_at", "updated_at"])
    assert client.get(f"/api/v1/protected-files/{revoked_ticket.token}/").status_code == status.HTTP_404_NOT_FOUND

    traversal = client.get("/api/v1/protected-files/%2e%2e%2fprivate.pdf/")
    assert traversal.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_protected_file_stream_rechecks_live_group_eligibility(isolated_local_storage):
    admin = User.objects.create_user(
        full_name="File administrator",
        email="file-admin@example.test",
        phone_number="+963900001011",
        password="StrongPass123!",
        role=UserRole.ADMIN,
    )
    student = User.objects.create_user(
        full_name="Eligible file student",
        email="file-student@example.test",
        phone_number="+963900001012",
        password="StrongPass123!",
        role=UserRole.STUDENT,
    )
    university = University.objects.create(name="Protected File University", code="PFU")
    StudentProfile.objects.create(
        user=student,
        university=university,
        verification_status=StudentVerificationStatus.APPROVED,
    )
    group = Group.objects.create(name="Protected file group", university=university, created_by=admin)
    membership = GroupMembership.objects.create(group=group, user=student, status=GroupMembershipStatus.APPROVED)
    resource = FileResource.objects.create(
        title="Group-only document",
        file=ContentFile(b"%PDF-1.4 group", name="group.pdf"),
        uploaded_by=admin,
        group=group,
        visibility=FileVisibility.GROUP_ONLY,
    )
    ticket = FileAccessTicket.issue(file_resource=resource, user=student)
    membership.status = GroupMembershipStatus.BLOCKED
    membership.save(update_fields=["status", "updated_at"])

    client = APIClient()
    client.force_authenticate(student)
    response = client.get(f"/api/v1/protected-files/{ticket.token}/")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    ticket.refresh_from_db()
    assert ticket.use_count == 0
