"""Offline protected lecture packages: package format, license lifecycle, access rules, revocation hooks."""

from __future__ import annotations

import base64
import struct
from datetime import timedelta

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.core.files.base import ContentFile
from django.utils import timezone
from rest_framework import status

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.audit.models import AuditAction, AuditLog
from apps.groups.models import GroupMembership, GroupMembershipStatus
from apps.lectures import offline_crypto
from apps.lectures import offline_services as offline
from apps.lectures import tests as base
from apps.lectures.models import Lecture, LectureOfflineBlob, LectureOfflineLicense, LectureProcessingStatus
from apps.product.models import FeatureFlag

academic_structure = base.academic_structure
api_client = base.api_client
manager = base.manager
ready_lecture = base.ready_lecture
student = base.student

make_pdf = base.make_pdf

DEVICE = {"HTTP_X_PANORAMA_DEVICE_ID": "device-aaaa"}
OTHER_DEVICE = {"HTTP_X_PANORAMA_DEVICE_ID": "device-bbbb"}


def reference_decrypt(package: bytes, key: bytes) -> bytes:
    """Independent reference implementation of the PNOF v1 format (what the mobile app must do)."""

    magic, version, algorithm, flags, chunk_size, plaintext_size, key_id, prefix = struct.unpack(
        ">4sBBHIQ16s4s", package[:40]
    )
    assert (magic, version, algorithm, flags) == (b"PNOF", 1, 1, 0)
    header = package[:40]
    chunk_count = -(-plaintext_size // chunk_size)
    aes = AESGCM(key)
    out = bytearray()
    offset = 40
    for index in range(chunk_count):
        length = min(chunk_size, plaintext_size - index * chunk_size)
        record = package[offset : offset + length + 16]
        offset += length + 16
        nonce = prefix + struct.pack(">Q", index)
        aad = header + struct.pack(">I", index) + (b"\x01" if index == chunk_count - 1 else b"\x00")
        out += aes.decrypt(nonce, record, aad)
    assert offset == len(package), "trailing bytes after the last chunk"
    return bytes(out)


@pytest.fixture
def other_student(db, academic_structure):
    """A second approved member of the same group and major (base ``other_student`` is in another major)."""

    user = User.objects.create_user(
        full_name="Second Member",
        email="second-member@example.com",
        phone_number="+963955000007",
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
    base.join_subject_group(user, academic_structure)
    return user


@pytest.fixture(autouse=True)
def small_chunks(settings):
    settings.LECTURE_OFFLINE_CHUNK_SIZE = 64  # several chunks even for the tiny test PDF
    settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")


def issue(client, lecture, **headers):
    return client.post(f"/api/v1/lectures/{lecture.id}/offline-license/", **headers)


def fetch_package(client, lecture, **headers):
    return client.get(f"/api/v1/lectures/{lecture.id}/offline-package/", **headers)


def body(response) -> bytes:
    return b"".join(response.streaming_content) if response.streaming else response.content


@pytest.mark.django_db
def test_member_gets_license_and_decrypts_package_round_trip(api_client, student, ready_lecture):
    api_client.force_authenticate(student)
    response = issue(api_client, ready_lecture, **DEVICE)
    assert response.status_code == status.HTTP_201_CREATED
    data = response.data["data"]
    key = base64.b64decode(data["content_key"])
    assert len(key) == 32
    assert data["algorithm"] == "AES-256-GCM-chunked"
    assert data["chunk_size"] == 64 and data["tag_size"] == 16 and data["header_size"] == 40
    assert response["Cache-Control"].startswith("private, no-store")
    expires = timezone.now() + timedelta(days=14)
    assert abs((data["expires_at"] - expires).total_seconds()) < 60

    package = fetch_package(api_client, ready_lecture, **DEVICE)
    assert package.status_code == 200
    raw = body(package)
    assert len(raw) == data["file_size"] == int(package["Content-Length"])
    import hashlib

    assert hashlib.sha256(raw).hexdigest() == data["sha256_of_ciphertext"]
    assert package["ETag"] == f'"{data["sha256_of_ciphertext"]}"'
    plain = ready_lecture.viewer_pdf.open("rb").read()
    assert reference_decrypt(raw, key) == plain
    assert b"%PDF" not in raw and plain[:20] not in raw  # no plaintext bytes on the offline route
    assert raw[:4] == b"PNOF"
    assert AuditLog.objects.filter(action=AuditAction.LECTURE_OFFLINE_LICENSE_ISSUED).count() == 1


@pytest.mark.django_db
def test_tampering_truncation_and_reordering_fail(api_client, student, ready_lecture):
    api_client.force_authenticate(student)
    key = base64.b64decode(issue(api_client, ready_lecture, **DEVICE).data["data"]["content_key"])
    raw = body(fetch_package(api_client, ready_lecture, **DEVICE))
    assert len(raw) > 40 + 3 * 80

    flipped = bytearray(raw)
    flipped[60] ^= 0x01
    with pytest.raises(InvalidTag):
        reference_decrypt(bytes(flipped), key)

    header_flip = bytearray(raw)
    header_flip[36] ^= 0x01  # nonce prefix is part of the header/AAD and of the nonce
    with pytest.raises(InvalidTag):
        reference_decrypt(bytes(header_flip), key)

    # Drop the final chunk: decrypting the (now) last chunk with final=1 AAD must fail.
    chunk_record = 64 + 16
    plain_size = struct.unpack(">Q", raw[12:20])[0]
    last_len = plain_size - (-(-plain_size // 64) - 1) * 64
    truncated = raw[: len(raw) - (last_len + 16)]
    with pytest.raises((InvalidTag, AssertionError)):
        reference_decrypt(truncated, key)

    # Swap the first two chunks.
    swapped = bytearray(raw)
    first, second = raw[40 : 40 + chunk_record], raw[40 + chunk_record : 40 + 2 * chunk_record]
    swapped[40 : 40 + chunk_record] = second
    swapped[40 + chunk_record : 40 + 2 * chunk_record] = first
    with pytest.raises(InvalidTag):
        reference_decrypt(bytes(swapped), key)

    with pytest.raises(InvalidTag):
        reference_decrypt(raw, bytes(32))


def test_encrypt_stream_rejects_wrong_lengths():
    import io
    import uuid

    key, key_id = bytes(32), uuid.uuid4()
    with pytest.raises(ValueError):
        offline_crypto.encrypt_stream(
            io.BytesIO(b"abc"), io.BytesIO(), plaintext_size=10, key=key, key_id=key_id, chunk_size=16
        )
    with pytest.raises(ValueError):
        offline_crypto.encrypt_stream(
            io.BytesIO(b"abcdef"), io.BytesIO(), plaintext_size=3, key=key, key_id=key_id, chunk_size=16
        )
    with pytest.raises(ValueError):
        offline_crypto.encrypt_stream(
            io.BytesIO(b""), io.BytesIO(), plaintext_size=0, key=key, key_id=key_id, chunk_size=16
        )
    info = offline_crypto.encrypt_stream(
        io.BytesIO(b"x" * 33), io.BytesIO(), plaintext_size=33, key=key, key_id=key_id, chunk_size=16
    )
    assert info.size == offline_crypto.package_size(33, 16) == 40 + 33 + 3 * 16


@pytest.mark.django_db
def test_range_etag_and_resumable_download(api_client, student, ready_lecture):
    api_client.force_authenticate(student)
    issue(api_client, ready_lecture, **DEVICE)
    full = fetch_package(api_client, ready_lecture, **DEVICE)
    raw, etag = body(full), full["ETag"]
    assert full["Accept-Ranges"] == "bytes"

    first = api_client.get(f"/api/v1/lectures/{ready_lecture.id}/offline-package/", HTTP_RANGE="bytes=0-99", **DEVICE)
    head = body(first)
    assert first.status_code == 206 and head == raw[:100]
    assert first["Content-Range"] == f"bytes 0-99/{len(raw)}"
    # Resume from where the first attempt stopped, guarded by If-Range.
    rest = api_client.get(
        f"/api/v1/lectures/{ready_lecture.id}/offline-package/",
        HTTP_RANGE="bytes=100-",
        HTTP_IF_RANGE=etag,
        **DEVICE,
    )
    assert rest.status_code == 206 and head + body(rest) == raw
    # A stale validator must restart from zero with the full body.
    stale = api_client.get(
        f"/api/v1/lectures/{ready_lecture.id}/offline-package/",
        HTTP_RANGE="bytes=100-",
        HTTP_IF_RANGE='"stale"',
        **DEVICE,
    )
    assert stale.status_code == 200 and body(stale) == raw
    suffix = api_client.get(f"/api/v1/lectures/{ready_lecture.id}/offline-package/", HTTP_RANGE="bytes=-16", **DEVICE)
    assert suffix.status_code == 206 and body(suffix) == raw[-16:]
    bad = api_client.get(
        f"/api/v1/lectures/{ready_lecture.id}/offline-package/", HTTP_RANGE=f"bytes={len(raw) + 5}-", **DEVICE
    )
    assert bad.status_code == 416
    cached = api_client.get(f"/api/v1/lectures/{ready_lecture.id}/offline-package/", HTTP_IF_NONE_MATCH=etag, **DEVICE)
    assert cached.status_code == 304


@pytest.mark.django_db
def test_non_member_cannot_get_license_or_package(api_client, student, academic_structure, ready_lecture):
    outsider = User.objects.create_user(
        full_name="Outsider",
        email="outsider@example.com",
        phone_number="+963955000099",
        password="StrongPass123!",
        role=UserRole.STUDENT,
    )
    StudentProfile.objects.create(
        user=outsider,
        university=academic_structure["university"],
        faculty=academic_structure["faculty"],
        major=academic_structure["major"],
        academic_year=academic_structure["year"],
        semester=academic_structure["semester"],
        verification_status=StudentVerificationStatus.APPROVED,
    )
    api_client.force_authenticate(outsider)
    assert issue(api_client, ready_lecture, **DEVICE).status_code == status.HTTP_404_NOT_FOUND
    assert fetch_package(api_client, ready_lecture, **DEVICE).status_code == status.HTTP_404_NOT_FOUND
    assert not LectureOfflineLicense.objects.exists()
    api_client.force_authenticate(None)
    assert issue(api_client, ready_lecture).status_code in {401, 403}
    assert fetch_package(api_client, ready_lecture).status_code in {401, 403}


@pytest.mark.django_db
def test_package_needs_a_license_for_this_device_and_user(api_client, student, other_student, ready_lecture):
    api_client.force_authenticate(student)
    issue(api_client, ready_lecture, **DEVICE)
    assert fetch_package(api_client, ready_lecture, **OTHER_DEVICE).status_code == 404
    assert fetch_package(api_client, ready_lecture).status_code == 404  # no device header -> different binding
    api_client.force_authenticate(other_student)  # a member too, but without a license
    assert fetch_package(api_client, ready_lecture, **DEVICE).status_code == 404


@pytest.mark.django_db
def test_issue_is_idempotent_and_shares_one_key_per_version(api_client, student, other_student, ready_lecture):
    api_client.force_authenticate(student)
    first = issue(api_client, ready_lecture, **DEVICE)
    again = issue(api_client, ready_lecture, **DEVICE)
    assert again.status_code == 200
    assert first.data["data"]["license_id"] == again.data["data"]["license_id"]
    api_client.force_authenticate(other_student)
    second_user = issue(api_client, ready_lecture, **DEVICE)
    assert second_user.data["data"]["license_id"] != first.data["data"]["license_id"]
    assert second_user.data["data"]["content_key"] == first.data["data"]["content_key"]  # documented trade-off
    assert LectureOfflineBlob.objects.count() == 1
    wrapped = LectureOfflineBlob.objects.get().wrapped_key
    assert first.data["data"]["content_key"] not in wrapped  # key is wrapped at rest


@pytest.mark.django_db
def test_expired_license_blocks_package_and_renewal_extends_while_member(api_client, student, ready_lecture):
    api_client.force_authenticate(student)
    issue(api_client, ready_lecture, **DEVICE)
    LectureOfflineLicense.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
    assert fetch_package(api_client, ready_lecture, **DEVICE).status_code == 404
    sync = api_client.post(
        "/api/v1/lectures/offline-licenses/sync/",
        {"license_ids": [str(LectureOfflineLicense.objects.get().public_id)]},
        format="json",
        **DEVICE,
    )
    assert sync.data["data"]["keep"] == [] and len(sync.data["data"]["renew"]) == 1
    renewed = api_client.post(f"/api/v1/lectures/{ready_lecture.id}/offline-license/renew/", **DEVICE)
    assert renewed.status_code == 200 and renewed.data["data"]["state"] == "active"
    assert fetch_package(api_client, ready_lecture, **DEVICE).status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize(
    "membership_status",
    [GroupMembershipStatus.BLOCKED, GroupMembershipStatus.LEFT, GroupMembershipStatus.REJECTED],
)
def test_membership_loss_revokes_licenses_and_refuses_renewal(api_client, student, ready_lecture, membership_status):
    api_client.force_authenticate(student)
    license_id = issue(api_client, ready_lecture, **DEVICE).data["data"]["license_id"]
    membership = GroupMembership.objects.get(user=student)
    membership.status = membership_status
    membership.save()
    row = LectureOfflineLicense.objects.get()
    assert row.revoked_at is not None and row.revoke_reason == offline.REASON_MEMBERSHIP_ENDED
    assert fetch_package(api_client, ready_lecture, **DEVICE).status_code == 404
    assert api_client.post(f"/api/v1/lectures/{ready_lecture.id}/offline-license/renew/", **DEVICE).status_code == 404
    assert issue(api_client, ready_lecture, **DEVICE).status_code == 404
    sync = api_client.post(
        "/api/v1/lectures/offline-licenses/sync/", {"license_ids": [license_id]}, format="json", **DEVICE
    )
    assert sync.data["data"] == {"keep": [], "renew": [], "purge": [license_id]}
    listing = api_client.get("/api/v1/lectures/offline-licenses/", **DEVICE)
    assert listing.data["data"][0]["state"] == "revoked"
    assert AuditLog.objects.filter(action=AuditAction.LECTURE_OFFLINE_LICENSE_REVOKED).count() == 1


@pytest.mark.django_db
def test_term_end_bulk_update_path_revokes_via_helper(api_client, student, other_student, ready_lecture):
    api_client.force_authenticate(student)
    issue(api_client, ready_lecture, **DEVICE)
    api_client.force_authenticate(other_student)
    issue(api_client, ready_lecture, **DEVICE)
    ended = GroupMembership.objects.filter(user=student)
    ids = list(ended.values_list("pk", flat=True))
    ended.update(status=GroupMembershipStatus.TERM_ENDED)  # signals do not fire on queryset.update
    assert offline.revoke_memberships_licenses(ids) == 1
    assert LectureOfflineLicense.objects.get(user=student).revoked_at is not None
    assert LectureOfflineLicense.objects.get(user=other_student).revoked_at is None


@pytest.mark.django_db
def test_live_check_denies_even_when_revocation_was_missed(api_client, student, ready_lecture):
    api_client.force_authenticate(student)
    issue(api_client, ready_lecture, **DEVICE)
    GroupMembership.objects.filter(user=student).update(status=GroupMembershipStatus.TERM_ENDED)
    assert fetch_package(api_client, ready_lecture, **DEVICE).status_code == 404
    assert LectureOfflineLicense.objects.get().revoked_at is not None


@pytest.mark.django_db
def test_unpublish_and_delete_revoke_all_licenses(api_client, student, other_student, ready_lecture):
    for user in (student, other_student):
        api_client.force_authenticate(user)
        issue(api_client, ready_lecture, **DEVICE)
    ready_lecture.is_published = False
    ready_lecture.save()
    assert LectureOfflineLicense.objects.filter(revoked_at__isnull=True).count() == 0
    assert set(LectureOfflineLicense.objects.values_list("revoke_reason", flat=True)) == {
        offline.REASON_LECTURE_UNAVAILABLE
    }
    ready_lecture.is_published = True
    ready_lecture.save()
    api_client.force_authenticate(student)
    issue(api_client, ready_lecture, **DEVICE)
    ready_lecture.is_deleted = True
    ready_lecture.save()
    assert LectureOfflineLicense.objects.filter(revoked_at__isnull=True).count() == 0


@pytest.mark.django_db
def test_replaced_document_bumps_version_and_revokes_old_licenses(api_client, student, ready_lecture):
    api_client.force_authenticate(student)
    first = issue(api_client, ready_lecture, **DEVICE).data["data"]
    ready_lecture.viewer_pdf.save("viewer.pdf", ContentFile(make_pdf(width=200)), save=True)
    old = LectureOfflineLicense.objects.get()
    assert old.revoked_at is not None and old.revoke_reason == offline.REASON_LECTURE_REPLACED
    assert LectureOfflineBlob.objects.get(version=1).retired_at is not None
    second = issue(api_client, ready_lecture, **DEVICE)
    assert second.status_code == 201
    assert second.data["data"]["package_version"] == 2
    assert second.data["data"]["content_key"] != first["content_key"]
    raw = body(fetch_package(api_client, ready_lecture, **DEVICE))
    assert reference_decrypt(raw, base64.b64decode(second.data["data"]["content_key"])) == (
        Lecture.objects.get(pk=ready_lecture.pk).viewer_pdf.open("rb").read()
    )


@pytest.mark.django_db
def test_revoke_user_licenses_service_and_device_filter(api_client, student, ready_lecture):
    api_client.force_authenticate(student)
    issue(api_client, ready_lecture, **DEVICE)
    issue(api_client, ready_lecture, **OTHER_DEVICE)
    import hashlib

    device_a = hashlib.sha256(b"device-aaaa").hexdigest()
    assert offline.revoke_user_licenses(student, offline.REASON_DEVICE_REPLACED, device_hash=device_a) == 1
    assert LectureOfflineLicense.objects.filter(revoked_at__isnull=True).count() == 1
    assert offline.revoke_user_licenses(student, offline.REASON_DEVICE_REPLACED) == 1
    assert offline.revoke_user_licenses(student, offline.REASON_DEVICE_REPLACED) == 0  # idempotent


@pytest.mark.django_db
def test_account_deactivation_revokes_licenses(api_client, student, ready_lecture):
    api_client.force_authenticate(student)
    issue(api_client, ready_lecture, **DEVICE)
    student.is_active = False
    student.save()
    row = LectureOfflineLicense.objects.get()
    assert row.revoked_at is not None and row.revoke_reason == offline.REASON_ACCOUNT_DISABLED


@pytest.mark.django_db
def test_limits_release_and_listing(api_client, student, ready_lecture, manager, academic_structure, settings):
    settings.LECTURE_OFFLINE_MAX_PER_DEVICE = 1
    second = Lecture.objects.create(
        subject=academic_structure["subject"],
        title="Second",
        original_file=ContentFile(make_pdf(), name="o2.pdf"),
        original_filename="o2.pdf",
        original_mime_type="application/pdf",
        original_size=10,
        original_sha256="b" * 64,
        viewer_pdf=ContentFile(make_pdf(), name="viewer2.pdf"),
        status=LectureProcessingStatus.READY,
        page_count=1,
        is_published=True,
        uploaded_by=manager,
    )
    api_client.force_authenticate(student)
    assert issue(api_client, ready_lecture, **DEVICE).status_code == 201
    over = issue(api_client, second, **DEVICE)
    assert over.status_code == status.HTTP_409_CONFLICT
    assert issue(api_client, second, **OTHER_DEVICE).status_code == 201  # per-device limit
    listing = api_client.get("/api/v1/lectures/offline-licenses/", **DEVICE)
    assert [item["lecture_id"] for item in listing.data["data"]] == [ready_lecture.id]
    assert "content_key" not in listing.data["data"][0]
    released = api_client.delete(f"/api/v1/lectures/{ready_lecture.id}/offline-license/", **DEVICE)
    assert released.data["data"]["released"] == 1
    assert issue(api_client, second, **DEVICE).status_code == 201


@pytest.mark.django_db
def test_sync_purges_unknown_foreign_and_malformed_ids(api_client, student, other_student, ready_lecture):
    api_client.force_authenticate(other_student)
    foreign = issue(api_client, ready_lecture, **DEVICE).data["data"]["license_id"]
    api_client.force_authenticate(student)
    mine = issue(api_client, ready_lecture, **DEVICE).data["data"]["license_id"]
    response = api_client.post(
        "/api/v1/lectures/offline-licenses/sync/",
        {"license_ids": [mine, foreign, "not-a-uuid", "00000000-0000-0000-0000-000000000000"]},
        format="json",
        **DEVICE,
    )
    assert response.data["data"]["keep"] == [mine]
    assert set(response.data["data"]["purge"]) == {foreign, "not-a-uuid", "00000000-0000-0000-0000-000000000000"}


@pytest.mark.django_db
def test_staff_can_list_and_revoke_but_students_cannot(api_client, student, manager, ready_lecture):
    api_client.force_authenticate(student)
    issue(api_client, ready_lecture, **DEVICE)
    assert api_client.get(f"/api/v1/dashboard/lectures/offline-licenses/?user={student.id}").status_code == 403
    assert (
        api_client.post("/api/v1/dashboard/lectures/offline-licenses/revoke/", {"user_id": student.id}).status_code
        == 403
    )
    api_client.force_authenticate(manager)
    listing = api_client.get(f"/api/v1/dashboard/lectures/offline-licenses/?user={student.id}")
    assert listing.status_code == 200 and listing.data["data"][0]["state"] == "active"
    assert api_client.get("/api/v1/dashboard/lectures/offline-licenses/").status_code == 400
    revoke = api_client.post(
        "/api/v1/dashboard/lectures/offline-licenses/revoke/", {"user_id": student.id}, format="json"
    )
    assert revoke.data["data"]["revoked"] == 1
    log = AuditLog.objects.get(action=AuditAction.LECTURE_OFFLINE_LICENSE_REVOKED)
    assert log.actor_id == manager.id
    api_client.force_authenticate(student)
    assert fetch_package(api_client, ready_lecture, **DEVICE).status_code == 404


@pytest.mark.django_db
def test_feature_flag_disables_offline_issue(api_client, student, ready_lecture):
    FeatureFlag.objects.create(key="lecture_offline_enabled", enabled=False)
    api_client.force_authenticate(student)
    assert issue(api_client, ready_lecture, **DEVICE).status_code >= 400
    assert not LectureOfflineLicense.objects.exists()
