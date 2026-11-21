"""Offline protected packages for library files: PDF build, package format, licenses, access, revocation, sync, staff."""

from __future__ import annotations

import base64
import io
import struct
from datetime import timedelta
from typing import Any

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet
from django.core.files.base import ContentFile
from django.utils import timezone
from pypdf import PdfReader

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.audit.models import AuditAction, AuditLog
from apps.common import offline as shared
from apps.files import offline_services as offline
from apps.files.models import FileOfflineBlob, FileOfflineLicense, FilePage, FileProcessingStatus, FileResource
from apps.files.offline_pdf import POINTS_PER_PIXEL
from apps.groups.models import Group, GroupMembership, GroupMembershipStatus
from apps.lectures.tests_offline import reference_decrypt
from apps.product.models import FeatureFlag

from .tests_viewer import _client, _ready_file, world  # noqa: F401  (fixture `world`)

DEVICE: dict[str, Any] = {"HTTP_X_PANORAMA_DEVICE_ID": "device-aaaa"}
OTHER_DEVICE: dict[str, Any] = {"HTTP_X_PANORAMA_DEVICE_ID": "device-bbbb"}


@pytest.fixture(autouse=True)
def small_chunks(settings):
    settings.FILE_OFFLINE_CHUNK_SIZE = 256  # several chunks even for the tiny test PDF
    settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")


@pytest.fixture
def resource(world):  # noqa: F811
    return _ready_file(world, pages=3)


def issue(client, resource, **headers):
    return client.post(f"/api/v1/files/{resource.pk}/offline-license/", **headers)


def fetch(client, resource, **headers):
    return client.get(f"/api/v1/files/{resource.pk}/offline-package/", **headers)


def body(response) -> bytes:
    return b"".join(response.streaming_content) if response.streaming else response.content


def _student(email: str, phone: str, university, *, approved=True) -> User:
    user = User.objects.create_user(
        full_name=email, email=email, phone_number=phone, password="StrongPass123!", role=UserRole.STUDENT
    )
    StudentProfile.objects.create(
        user=user,
        university=university,
        verification_status=StudentVerificationStatus.APPROVED if approved else StudentVerificationStatus.PENDING,
    )
    return user


def test_pdf_is_built_from_pages_and_round_trips_through_the_package(world, resource):  # noqa: F811
    client = _client(world["student"])
    created = issue(client, resource, **DEVICE)
    assert created.status_code == 201, created.data
    data = created.data["data"]
    assert data["algorithm"] == "AES-256-GCM-chunked" and data["content_type"] == "application/pdf"
    assert data["file_id"] == resource.pk and data["package_url"] == f"/api/v1/files/{resource.pk}/offline-package/"
    assert data["chunk_size"] == 256 and data["header_size"] == 40 and data["tag_size"] == 16
    assert created["Cache-Control"].startswith("private, no-store")
    response = fetch(client, resource, **DEVICE)
    assert response.status_code == 200 and response["Accept-Ranges"] == "bytes"
    package = body(response)
    assert len(package) == data["file_size"] and response["ETag"] == f'"{data["sha256_of_ciphertext"]}"'
    assert package[:4] == b"PNOF" and b"%PDF" not in package
    plain = reference_decrypt(package, base64.b64decode(data["content_key"]))
    assert len(plain) == data["plaintext_size"] and plain.startswith(b"%PDF-1.4")
    reader = PdfReader(io.BytesIO(plain))
    assert len(reader.pages) == 3
    assert round(float(reader.pages[0].mediabox.width)) == round(60 * POINTS_PER_PIXEL)
    assert issue(client, resource, **DEVICE).status_code == 200  # refresh, same key
    assert FileOfflineBlob.objects.count() == 1


def test_tampering_truncation_and_reordering_fail(world, resource):  # noqa: F811
    client = _client(world["student"])
    data = issue(client, resource, **DEVICE).data["data"]
    package = body(fetch(client, resource, **DEVICE))
    key = base64.b64decode(data["content_key"])
    flipped = bytearray(package)
    flipped[60] ^= 1
    with pytest.raises(InvalidTag):
        reference_decrypt(bytes(flipped), key)
    with pytest.raises((InvalidTag, AssertionError)):
        reference_decrypt(package[: -(256 + 16)], key)
    record = 256 + 16
    swapped = bytearray(package)
    swapped[40 : 40 + record], swapped[40 + record : 40 + 2 * record] = (
        package[40 + record : 40 + 2 * record],
        package[40 : 40 + record],
    )
    with pytest.raises(InvalidTag):
        reference_decrypt(bytes(swapped), key)


def test_range_etag_and_resumable_download(world, resource):  # noqa: F811
    client = _client(world["student"])
    data = issue(client, resource, **DEVICE).data["data"]
    full = body(fetch(client, resource, **DEVICE))
    etag = f'"{data["sha256_of_ciphertext"]}"'
    part = fetch(client, resource, HTTP_RANGE="bytes=100-299", **DEVICE)
    assert part.status_code == 206 and part["Content-Range"] == f"bytes 100-299/{len(full)}"
    assert body(part) == full[100:300]
    resumed = fetch(client, resource, HTTP_RANGE="bytes=300-", HTTP_IF_RANGE=etag, **DEVICE)
    assert resumed.status_code == 206 and full[:300] + body(resumed) == full
    stale = fetch(client, resource, HTTP_RANGE="bytes=300-", HTTP_IF_RANGE='"other"', **DEVICE)
    assert stale.status_code == 200 and body(stale) == full
    assert fetch(client, resource, HTTP_IF_NONE_MATCH=etag, **DEVICE).status_code == 304
    assert fetch(client, resource, HTTP_RANGE=f"bytes={len(full) + 5}-", **DEVICE).status_code == 416


def test_non_member_and_unauthorised_get_404(world, resource):  # noqa: F811
    unverified = _student("unv@v.test", "+963992000090", world["university"], approved=False)
    assert issue(_client(unverified), resource, **DEVICE).status_code == 404
    assert issue(_client(world["other"]), resource, **DEVICE).status_code == 404  # no student profile
    assert fetch(_client(world["student"]), resource, **DEVICE).status_code == 404  # no license yet
    admin_only = _ready_file(world, pages=1)
    admin_only.visibility = "admin_only"
    admin_only.save()
    assert issue(_client(world["student"]), admin_only, **DEVICE).status_code == 404
    unready = _ready_file(world, pages=1)
    FileResource.objects.filter(pk=unready.pk).update(processing_status=FileProcessingStatus.PENDING)
    assert issue(_client(world["student"]), unready, **DEVICE).status_code == 404
    assert FileOfflineLicense.objects.count() == 0


def test_package_is_bound_to_user_and_device(world, resource):  # noqa: F811
    issue(_client(world["student"]), resource, **DEVICE)
    assert fetch(_client(world["student"]), resource, **OTHER_DEVICE).status_code == 404
    other = _student("two@v.test", "+963992000091", world["university"])
    assert fetch(_client(other), resource, **DEVICE).status_code == 404


def test_group_only_file_follows_membership(world):  # noqa: F811
    student = world["student"]
    group = Group.objects.create(name="Study", university=world["university"], created_by=world["admin"])
    resource = _ready_file(world, pages=1)
    resource.visibility, resource.group = "group_only", group
    resource.save()
    membership = GroupMembership.objects.create(group=group, user=student, status=GroupMembershipStatus.APPROVED)
    client = _client(student)
    assert issue(client, resource, **DEVICE).status_code == 201
    membership.status = GroupMembershipStatus.REJECTED
    membership.save()
    row = FileOfflineLicense.objects.get()
    assert row.revoked_at is not None and row.revoke_reason == offline.REASON_ACCESS_LOST
    assert fetch(client, resource, **DEVICE).status_code == 404
    assert issue(client, resource, **DEVICE).status_code == 404


def test_term_end_bulk_update_path_revokes_group_file_licenses(world):  # noqa: F811
    student = world["student"]
    group = Group.objects.create(name="Study", university=world["university"], created_by=world["admin"])
    resource = _ready_file(world, pages=1)
    resource.visibility, resource.group = "group_only", group
    resource.save()
    membership = GroupMembership.objects.create(group=group, user=student, status=GroupMembershipStatus.APPROVED)
    issue(_client(student), resource, **DEVICE)
    GroupMembership.objects.filter(pk=membership.pk).update(status=GroupMembershipStatus.TERM_ENDED)  # no signal
    assert offline.revoke_memberships_licenses([membership.pk]) == 1
    assert FileOfflineLicense.objects.get().revoked_at is not None


def test_expired_license_blocks_package_and_renewal_extends(world, resource):  # noqa: F811
    client = _client(world["student"])
    issue(client, resource, **DEVICE)
    FileOfflineLicense.objects.update(expires_at=timezone.now() - timedelta(days=1))
    assert fetch(client, resource, **DEVICE).status_code == 404
    listing = client.get("/api/v1/files/offline-licenses/", **DEVICE)
    assert listing.data["data"][0]["state"] == "expired"
    license_id = listing.data["data"][0]["license_id"]
    sync = client.post("/api/v1/files/offline-licenses/sync/", {"license_ids": [license_id]}, format="json", **DEVICE)
    assert sync.data["data"] == {"keep": [], "renew": [license_id], "purge": []}
    renewed = client.post(f"/api/v1/files/{resource.pk}/offline-license/renew/", **DEVICE)
    assert renewed.status_code == 200 and renewed.data["data"]["state"] == "active"
    assert fetch(client, resource, **DEVICE).status_code == 200
    assert AuditLog.objects.filter(action=AuditAction.FILE_OFFLINE_LICENSE_RENEWED).count() == 1


def test_hide_delete_and_visibility_change_revoke(world, resource):  # noqa: F811
    client = _client(world["student"])
    issue(client, resource, **DEVICE)
    resource.visibility = "admin_only"
    resource.save()
    assert FileOfflineLicense.objects.get().revoke_reason == offline.REASON_ACCESS_LOST
    resource.visibility = "verified_students_only"
    resource.save()
    assert issue(client, resource, **DEVICE).status_code == 201  # back: new license
    resource.is_active = False
    resource.save()
    assert FileOfflineLicense.objects.filter(revoked_at__isnull=True).count() == 0
    assert fetch(client, resource, **DEVICE).status_code == 404
    resource.is_active = True
    resource.save()
    issue(client, resource, **DEVICE)
    resource.is_deleted = True
    resource.save()
    assert FileOfflineLicense.objects.filter(revoked_at__isnull=True).count() == 0


def test_reprocessing_bumps_version_and_changes_key(world, resource):  # noqa: F811
    client = _client(world["student"])
    first = issue(client, resource, **DEVICE).data["data"]
    resource.processed_at = timezone.now()
    resource.save()  # new rendering of the document
    old = FileOfflineLicense.objects.get()
    assert old.revoked_at is not None and old.revoke_reason == offline.REASON_FILE_REPLACED
    assert FileOfflineBlob.objects.get(version=1).retired_at is not None
    second = issue(client, resource, **DEVICE).data["data"]
    assert second["package_version"] == 2 and second["key_id"] != first["key_id"]
    assert second["content_key"] != first["content_key"]
    assert FileOfflineBlob.objects.filter(retired_at__isnull=True).count() == 1


def test_live_check_denies_even_when_revocation_was_missed(world, resource):  # noqa: F811
    client = _client(world["student"])
    issue(client, resource, **DEVICE)
    FileResource.objects.filter(pk=resource.pk).update(visibility="admin_only")  # queryset.update: no signal
    assert fetch(client, resource, **DEVICE).status_code == 404
    assert FileOfflineLicense.objects.get().revoke_reason == offline.REASON_ACCESS_LOST


def test_account_and_profile_changes_revoke(world, resource):  # noqa: F811
    student = world["student"]
    issue(_client(student), resource, **DEVICE)
    profile = student.student_profile
    profile.verification_status = StudentVerificationStatus.REJECTED
    profile.save()
    assert FileOfflineLicense.objects.get().revoked_at is not None
    profile.verification_status = StudentVerificationStatus.APPROVED
    profile.save()
    issue(_client(student), resource, **DEVICE)
    student.is_active = False
    student.save()
    assert FileOfflineLicense.objects.filter(revoked_at__isnull=True).count() == 0


def test_limits_release_and_sync(world, resource, settings):  # noqa: F811
    settings.FILE_OFFLINE_MAX_PER_DEVICE = 1
    client = _client(world["student"])
    first = issue(client, resource, **DEVICE).data["data"]["license_id"]
    second_file = _ready_file(world, pages=1)
    limited = issue(client, second_file, **DEVICE)
    assert limited.status_code == 409
    assert issue(client, second_file, **OTHER_DEVICE).status_code == 201
    other_user = _student("three@v.test", "+963992000092", world["university"])
    foreign = issue(_client(other_user), resource, **DEVICE).data["data"]["license_id"]
    sync = client.post(
        "/api/v1/files/offline-licenses/sync/",
        {"license_ids": [first, foreign, "not-a-uuid", first]},
        format="json",
        **DEVICE,
    )
    assert sync.data["data"] == {"keep": [first], "renew": [], "purge": [foreign, "not-a-uuid"]}
    released = client.delete(f"/api/v1/files/{resource.pk}/offline-license/", **DEVICE)
    assert released.data["data"]["released"] == 1
    assert fetch(client, resource, **DEVICE).status_code == 404
    assert issue(client, second_file, **DEVICE).status_code == 201  # slot freed


def test_staff_can_list_and_revoke_and_students_cannot(world, resource):  # noqa: F811
    student = world["student"]
    issue(_client(student), resource, **DEVICE)
    staff = _client(world["admin"])
    listing = staff.get(f"/api/v1/dashboard/files/offline-licenses/?user={student.pk}")
    assert listing.status_code == 200 and listing.data["data"][0]["file_title"] == "Notes"
    assert "content_key" not in listing.data["data"][0]
    assert staff.get("/api/v1/dashboard/files/offline-licenses/").status_code == 400
    assert _client(student).get(f"/api/v1/dashboard/files/offline-licenses/?user={student.pk}").status_code == 403
    denied = _client(student).post(
        "/api/v1/dashboard/files/offline-licenses/revoke/", {"user_id": student.pk}, format="json"
    )
    assert denied.status_code == 403
    revoked = staff.post(
        "/api/v1/dashboard/files/offline-licenses/revoke/",
        {"user_id": student.pk, "file_id": resource.pk},
        format="json",
    )
    assert revoked.status_code == 200 and revoked.data["data"]["revoked"] == 1
    row = FileOfflineLicense.objects.get()
    assert row.revoke_reason == offline.REASON_STAFF and row.revoked_by == world["admin"]
    audit = AuditLog.objects.get(action=AuditAction.FILE_OFFLINE_LICENSE_REVOKED)
    assert audit.actor == world["admin"]
    assert (
        staff.post(
            "/api/v1/dashboard/files/offline-licenses/revoke/", {"user_id": student.pk, "file_id": 99999}, format="json"
        ).status_code
        == 404
    )


def test_feature_flag_disables_issue(world, resource):  # noqa: F811
    FeatureFlag.objects.create(key="file_offline_enabled", enabled=False)
    assert issue(_client(world["student"]), resource, **DEVICE).status_code >= 400


def test_big_file_is_built_by_a_worker_and_answers_202(world, resource, settings):  # noqa: F811
    settings.FILE_OFFLINE_INLINE_BUILD_MAX_PAGES = 1
    client = _client(world["student"])
    pending = issue(client, resource, **DEVICE)
    assert pending.status_code == 202 and pending.data["data"]["state"] == "preparing"
    from apps.files.tasks import build_file_offline_package

    assert build_file_offline_package.apply(args=(resource.pk,)).get() == "built"
    assert issue(client, resource, **DEVICE).status_code == 201


def test_too_large_pdf_is_refused(world, resource, settings):  # noqa: F811
    settings.FILE_OFFLINE_MAX_PDF_BYTES = 100
    response = issue(_client(world["student"]), resource, **DEVICE)
    assert response.status_code == 409 and not FileOfflineBlob.objects.exists()


def test_pdf_writer_streams_pages_and_handles_modes(world):  # noqa: F811
    from PIL import Image

    from apps.files import offline_pdf

    resource = _ready_file(world, pages=1)
    buffer = io.BytesIO()
    Image.new("RGBA", (50, 40), (255, 0, 0, 128)).save(buffer, "PNG")
    FilePage.objects.create(
        file_resource=resource, page_number=2, rendered_file=ContentFile(buffer.getvalue(), name="rgba.png")
    )
    gray = io.BytesIO()
    Image.new("L", (30, 30), 200).save(gray, "PNG")
    FilePage.objects.create(
        file_resource=resource, page_number=3, rendered_file=ContentFile(gray.getvalue(), name="gray.png")
    )
    out = io.BytesIO()
    assert offline_pdf.write_pdf_from_pages(resource.pages.order_by("page_number"), out) == 3
    assert len(PdfReader(io.BytesIO(out.getvalue())).pages) == 3
    with pytest.raises(ValueError):
        offline_pdf.write_pdf_from_pages([], io.BytesIO())


def test_header_layout_matches_lecture_format(world, resource):  # noqa: F811
    client = _client(world["student"])
    issue(client, resource, **DEVICE)
    package = body(fetch(client, resource, **DEVICE))
    magic, version, algorithm, flags, chunk, plain_size, key_id, _prefix = struct.unpack(">4sBBHIQ16s4s", package[:40])
    assert (magic, version, algorithm, flags, chunk) == (b"PNOF", 1, 1, 0, 256)
    assert len(package) == 40 + plain_size + 16 * -(-plain_size // 256)
    assert shared.REASON_FILE_REPLACED == "file_replaced"
