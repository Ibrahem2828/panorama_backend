"""Offline protected lecture packages: blob building, license lifecycle, revocation hooks.

Access rule (the only one): a license is usable while ``user_can_view_lecture(user, lecture)`` holds, which for a
subject lecture means an approved membership in the subject's study group (term not ended, not blocked/left) and an
active account. No payment concept lives here; a future manual-payment gate only controls *when the membership gets
approved*.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import tempfile
import uuid
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.files import File
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException, NotFound

from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.crypto import decrypt_text, encrypt_text

from . import offline_crypto
from .models import Lecture, LectureOfflineBlob, LectureOfflineLicense
from .services import user_can_view_lecture

logger = logging.getLogger(__name__)

DEVICE_HEADER = "X-Panorama-Device-Id"

# Revocation reasons (stored in ``revoke_reason``; the app may show or log them).
REASON_MEMBERSHIP_ENDED = "membership_ended"
REASON_ACCESS_LOST = "access_lost"
REASON_LECTURE_UNAVAILABLE = "lecture_unavailable"
REASON_LECTURE_REPLACED = "lecture_replaced"
REASON_RELEASED = "released_by_device"
REASON_STAFF = "revoked_by_staff"
REASON_DEVICE_REPLACED = "device_replaced"
REASON_ACCOUNT_DISABLED = "account_disabled"
REASON_EXPIRED = "expired"


class OfflineLimitReached(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The offline lecture limit was reached. Remove a downloaded lecture first."
    default_code = "offline_limit_reached"


def license_days() -> int:
    return max(1, int(getattr(settings, "LECTURE_OFFLINE_LICENSE_DAYS", 14)))


def max_per_device() -> int:
    return max(1, int(getattr(settings, "LECTURE_OFFLINE_MAX_PER_DEVICE", 40)))


def max_per_user() -> int:
    return max(1, int(getattr(settings, "LECTURE_OFFLINE_MAX_PER_USER", 120)))


def device_hash_from_request(request) -> str:
    """SHA-256 of the optional ``X-Panorama-Device-Id`` header (same convention as the device binding)."""

    raw = (request.headers.get(DEVICE_HEADER, "") or "").strip()[:256]
    return hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else ""


def _safe_uuid(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


# -- blobs --------------------------------------------------------------------------------------------------------------


def retire_blob(blob: LectureOfflineBlob, reason: str) -> int:
    blob.retired_at = timezone.now()
    blob.save(update_fields=["retired_at", "updated_at"])
    return revoke_licenses(LectureOfflineLicense.objects.filter(blob=blob), reason)


def _build_blob(lecture: Lecture, version: int) -> LectureOfflineBlob:
    key = os.urandom(offline_crypto.KEY_SIZE)
    key_id = uuid.uuid4()
    source = lecture.viewer_pdf
    with source.open("rb") as plain, tempfile.TemporaryFile() as cipher:
        info = offline_crypto.encrypt_stream(
            plain,
            cipher,
            key=key,
            key_id=key_id,
            plaintext_size=source.size,
            chunk_size=int(getattr(settings, "LECTURE_OFFLINE_CHUNK_SIZE", offline_crypto.DEFAULT_CHUNK_SIZE)),
        )
        cipher.seek(0)
        blob = LectureOfflineBlob(
            lecture=lecture,
            version=version,
            key_id=key_id,
            wrapped_key=encrypt_text(base64.b64encode(key).decode("ascii")),
            source_name=source.name,
            plaintext_size=info.plaintext_size,
            package_size=info.size,
            package_sha256=info.sha256,
            chunk_size=info.chunk_size,
        )
        blob.package.save("package.pnof", File(cipher), save=False)
    blob.save()
    return blob


def ensure_blob(lecture_id: int) -> LectureOfflineBlob:
    """The live encrypted package of the lecture's current viewer PDF, building (or rebuilding) it when needed."""

    with transaction.atomic():
        lecture = Lecture.objects.select_for_update().filter(pk=lecture_id, is_deleted=False).first()
        if lecture is None or not lecture.is_ready_for_students:
            raise NotFound("Lecture is unavailable.")
        live = LectureOfflineBlob.objects.filter(lecture=lecture, retired_at__isnull=True).first()
        if live is not None and live.source_name == lecture.viewer_pdf.name:
            return live
        last = LectureOfflineBlob.objects.filter(lecture=lecture).order_by("-version").first()
        next_version = last.version + 1 if last is not None else 1
        if live is not None:
            retire_blob(live, REASON_LECTURE_REPLACED)
        try:
            return _build_blob(lecture, next_version)
        except ValueError as exc:
            raise NotFound("Lecture is unavailable.") from exc


def blob_content_key(blob: LectureOfflineBlob) -> bytes:
    return base64.b64decode(decrypt_text(blob.wrapped_key))


# -- evaluation ---------------------------------------------------------------------------------------------------------


def license_denial_reason(license_: LectureOfflineLicense, *, check_expiry: bool = True) -> str | None:
    """Why the license cannot be used right now, or ``None`` when it is good."""

    if license_.revoked_at is not None:
        return license_.revoke_reason or "revoked"
    if check_expiry and license_.expires_at <= timezone.now():
        return REASON_EXPIRED
    if license_.blob.retired_at is not None:
        return REASON_LECTURE_REPLACED
    lecture = license_.lecture
    if not lecture.is_ready_for_students:
        return REASON_LECTURE_UNAVAILABLE
    if not user_can_view_lecture(license_.user, lecture):
        return REASON_ACCESS_LOST
    return None


def revoke_licenses(queryset, reason: str, *, actor=None, request=None) -> int:
    """Revoke every active license in ``queryset`` and write one audit row per license. Idempotent."""

    rows = list(queryset.filter(revoked_at__isnull=True).select_related("user", "lecture"))
    if not rows:
        return 0
    now = timezone.now()
    for row in rows:
        row.revoked_at = now
        row.revoke_reason = reason
        row.revoked_by = actor if getattr(actor, "is_authenticated", False) else None
        row.save(update_fields=["revoked_at", "revoke_reason", "revoked_by", "updated_at"])
        AuditLogService.log(
            actor=actor,
            action=AuditAction.LECTURE_OFFLINE_LICENSE_REVOKED,
            target=row,
            new_value={"user_id": row.user_id, "lecture_id": row.lecture_id, "reason": reason},
            request=request,
        )
    return len(rows)


def revoke_user_licenses(
    user, reason: str, *, lecture: Lecture | None = None, device_hash: str | None = None, actor=None, request=None
) -> int:
    """Revoke a user's offline licenses (all, one lecture, or one device).

    Generic hook: call it when a device binding is switched or an account is closed.
    """

    queryset = LectureOfflineLicense.objects.filter(user=user)
    if lecture is not None:
        queryset = queryset.filter(lecture=lecture)
    if device_hash is not None:
        queryset = queryset.filter(device_hash=device_hash)
    return revoke_licenses(queryset, reason, actor=actor, request=request)


def revoke_lecture_licenses(lecture_id: int, reason: str) -> int:
    return revoke_licenses(LectureOfflineLicense.objects.filter(lecture_id=lecture_id), reason)


def revoke_membership_licenses(user_id: int, group_id: int, reason: str = REASON_MEMBERSHIP_ENDED) -> int:
    """A user lost a group membership: revoke their licenses for that group's subject lectures."""

    from apps.groups.models import Group

    subject_id = Group.objects.filter(pk=group_id).values_list("subject_id", flat=True).first()
    if not subject_id:
        return 0
    return revoke_licenses(
        LectureOfflineLicense.objects.filter(user_id=user_id, lecture__subject_id=subject_id), reason
    )


def revoke_memberships_licenses(membership_ids: list[int], reason: str = REASON_MEMBERSHIP_ENDED) -> int:
    """Bulk variant for ``queryset.update`` paths that bypass signals (term rollover)."""

    from apps.groups.models import GroupMembership

    pairs = set(
        GroupMembership.objects.filter(pk__in=membership_ids, group__subject__isnull=False).values_list(
            "user_id", "group__subject_id"
        )
    )
    if not pairs:
        return 0
    candidates = LectureOfflineLicense.objects.filter(
        revoked_at__isnull=True, user_id__in={user_id for user_id, _ in pairs}, lecture__subject__isnull=False
    ).values_list("pk", "user_id", "lecture__subject_id")
    ids = [pk for pk, user_id, subject_id in candidates if (user_id, subject_id) in pairs]
    return revoke_licenses(LectureOfflineLicense.objects.filter(pk__in=ids), reason) if ids else 0


# -- issue / renew / release --------------------------------------------------------------------------------------------


def _require_viewable(user, lecture_id: int) -> Lecture:
    lecture = Lecture.objects.select_related("subject", "faculty").filter(pk=lecture_id, is_deleted=False).first()
    if lecture is None or not user_can_view_lecture(user, lecture) or not lecture.viewer_pdf:
        raise NotFound("Lecture is unavailable.")
    return lecture


def _active_count(user, device_hash: str | None = None) -> int:
    queryset = LectureOfflineLicense.objects.filter(user=user, revoked_at__isnull=True, expires_at__gt=timezone.now())
    if device_hash is not None:
        queryset = queryset.filter(device_hash=device_hash)
    return queryset.count()


def _extend(license_: LectureOfflineLicense, user, request) -> None:
    now = timezone.now()
    license_.expires_at = now + timedelta(days=license_days())
    license_.last_renewed_at = now
    license_.renew_count += 1
    license_.save(update_fields=["expires_at", "last_renewed_at", "renew_count", "updated_at"])
    AuditLogService.log(
        actor=user,
        action=AuditAction.LECTURE_OFFLINE_LICENSE_RENEWED,
        target=license_,
        new_value={"lecture_id": license_.lecture_id},
        request=request,
    )


def issue_license(user, lecture_id: int, device_hash: str, *, request=None) -> tuple[LectureOfflineLicense, bool]:
    """Issue (or refresh) the (user, device, lecture) license. Returns ``(license, created)``."""

    lecture = _require_viewable(user, lecture_id)
    blob = ensure_blob(lecture.pk)
    now = timezone.now()
    with transaction.atomic():
        existing = (
            LectureOfflineLicense.objects.select_for_update()
            .select_related("blob", "lecture", "user")
            .filter(user=user, lecture=lecture, device_hash=device_hash, revoked_at__isnull=True)
            .first()
        )
        if existing is not None and existing.blob_id == blob.pk:
            _extend(existing, user, request)
            return existing, False
        if existing is not None:
            revoke_licenses(LectureOfflineLicense.objects.filter(pk=existing.pk), REASON_LECTURE_REPLACED)
        if _active_count(user, device_hash) >= max_per_device() or _active_count(user) >= max_per_user():
            raise OfflineLimitReached()
        try:
            with transaction.atomic():
                license_ = LectureOfflineLicense.objects.create(
                    user=user,
                    lecture=lecture,
                    blob=blob,
                    device_hash=device_hash,
                    issued_at=now,
                    expires_at=now + timedelta(days=license_days()),
                )
        except IntegrityError:  # concurrent issue of the same (user, device, lecture)
            license_ = LectureOfflineLicense.objects.select_related("blob").get(
                user=user, lecture=lecture, device_hash=device_hash, revoked_at__isnull=True
            )
            return license_, False
        AuditLogService.log(
            actor=user,
            action=AuditAction.LECTURE_OFFLINE_LICENSE_ISSUED,
            target=license_,
            new_value={"lecture_id": lecture.pk, "blob_version": blob.version},
            request=request,
        )
        return license_, True


def get_license(user, lecture_id: int, device_hash: str, *, include_expired: bool = False) -> LectureOfflineLicense:
    """The caller's usable license for this lecture and device, or 404. Re-checks access live and revokes on loss."""

    license_ = (
        LectureOfflineLicense.objects.select_related("blob", "lecture", "lecture__subject", "user")
        .filter(user=user, lecture_id=lecture_id, device_hash=device_hash, revoked_at__isnull=True)
        .first()
    )
    if license_ is None:
        raise NotFound("No offline license for this lecture on this device.")
    reason = license_denial_reason(license_, check_expiry=not include_expired)
    if reason is not None:
        if reason != REASON_EXPIRED:
            revoke_licenses(LectureOfflineLicense.objects.filter(pk=license_.pk), reason)
        raise NotFound("No offline license for this lecture on this device.")
    return license_


def renew_license(user, lecture_id: int, device_hash: str, *, request=None) -> LectureOfflineLicense:
    """Extend an existing license (also an expired one) while access holds; refuses otherwise."""

    license_ = get_license(user, lecture_id, device_hash, include_expired=True)
    _extend(license_, user, request)
    return license_


def release_license(user, lecture_id: int, device_hash: str, *, request=None) -> int:
    return revoke_licenses(
        LectureOfflineLicense.objects.filter(user=user, lecture_id=lecture_id, device_hash=device_hash),
        REASON_RELEASED,
        actor=user,
        request=request,
    )


# -- payload helpers ----------------------------------------------------------------------------------------------------


def license_state(license_: LectureOfflineLicense) -> str:
    if license_.revoked_at:
        return "revoked"
    return "expired" if license_.expires_at <= timezone.now() else "active"


def license_summary(license_: LectureOfflineLicense) -> dict[str, Any]:
    blob = license_.blob
    return {
        "license_id": str(license_.public_id),
        "lecture_id": license_.lecture_id,
        "key_id": str(blob.key_id),
        "package_version": blob.version,
        "issued_at": license_.issued_at,
        "expires_at": license_.expires_at,
        "state": license_state(license_),
        "revoked_at": license_.revoked_at,
        "revoke_reason": license_.revoke_reason,
        "file_size": blob.package_size,
        "sha256_of_ciphertext": blob.package_sha256,
    }


def license_payload(license_: LectureOfflineLicense) -> dict[str, Any]:
    """Response of issue/renew: the summary plus the content key and the format parameters."""

    blob = license_.blob
    return {
        **license_summary(license_),
        "content_key": base64.b64encode(blob_content_key(blob)).decode("ascii"),
        "algorithm": offline_crypto.ALGORITHM_NAME,
        "format_version": offline_crypto.FORMAT_VERSION,
        "header_size": offline_crypto.HEADER_SIZE,
        "chunk_size": blob.chunk_size,
        "tag_size": offline_crypto.TAG_SIZE,
        "plaintext_size": blob.plaintext_size,
        "nonce_scheme": offline_crypto.NONCE_SCHEME,
        "aad_scheme": offline_crypto.AAD_SCHEME,
        "package_url": f"/api/v1/lectures/{license_.lecture_id}/offline-package/",
        "content_type": "application/pdf",
    }


def sync_inventory(user, device_hash: str, license_ids: list[str]) -> dict[str, list[str]]:
    """Compare the app's local inventory with the server: ``keep`` / ``renew`` / ``purge``.

    Unknown, foreign, other-device, revoked or no-longer-authorised licenses are purged. An expired license whose
    access still holds is listed under ``renew`` (do not open it until renewed). Authorisation is re-evaluated live and
    a license that lost access is revoked on the spot (audited).
    """

    parsed = {raw: _safe_uuid(raw) for raw in license_ids}
    owned = {
        item.public_id: item
        for item in LectureOfflineLicense.objects.select_related("blob", "lecture", "lecture__subject", "user").filter(
            user=user, public_id__in=[value for value in parsed.values() if value]
        )
    }
    keep: list[str] = []
    renew: list[str] = []
    purge: list[str] = []
    for raw, value in parsed.items():
        item = owned.get(value) if value else None
        if item is None or item.device_hash != device_hash:
            purge.append(raw)
            continue
        reason = license_denial_reason(item)
        if reason is None:
            keep.append(raw)
            continue
        if reason == REASON_EXPIRED:
            if license_denial_reason(item, check_expiry=False) is None:
                renew.append(raw)
                continue
            reason = license_denial_reason(item, check_expiry=False) or REASON_ACCESS_LOST
        if item.revoked_at is None:
            revoke_licenses(LectureOfflineLicense.objects.filter(pk=item.pk), reason)
        purge.append(raw)
    return {"keep": keep, "renew": renew, "purge": purge}
