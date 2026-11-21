"""Offline protected packages for library files: blob building, license lifecycle, revocation hooks.

Mirrors ``apps.lectures.offline_services`` (see ``docs/OFFLINE_FILES.md``); the PNOF encryption, key wrapping, payload
helpers, streaming and sync reconciliation are shared through ``apps.common.offline``.

Access rule (the only one): a license is usable while ``user_can_access_file(user, file)`` holds (the same rule as the
online viewer and ticket endpoints), the file is ready (``viewer_ready``) and the account is active.
"""

from __future__ import annotations

import logging
import tempfile
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
from apps.common import offline as shared
from apps.common.offline import (
    REASON_ACCESS_LOST,
    REASON_ACCOUNT_DISABLED,
    REASON_EXPIRED,
    REASON_FILE_REPLACED,
    REASON_FILE_UNAVAILABLE,
    REASON_MEMBERSHIP_ENDED,
    REASON_RELEASED,
    REASON_STAFF,
    device_hash_from_request,
    license_state,
)

from . import offline_pdf
from .models import FileOfflineBlob, FileOfflineLicense, FilePage, FileResource
from .services import user_can_access_file

logger = logging.getLogger(__name__)

__all__ = [
    "REASON_ACCESS_LOST",
    "REASON_ACCOUNT_DISABLED",
    "REASON_FILE_REPLACED",
    "REASON_FILE_UNAVAILABLE",
    "REASON_MEMBERSHIP_ENDED",
    "REASON_RELEASED",
    "REASON_STAFF",
    "device_hash_from_request",
    "license_state",
]


class OfflineLimitReached(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The offline file limit was reached. Remove a downloaded file first."
    default_code = "offline_limit_reached"


class OfflinePackageTooLarge(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "This file is too large to download for offline study."
    default_code = "offline_package_too_large"


class OfflinePackagePreparing(APIException):
    """The package of a big file is being built by a worker: the app retries the same request shortly."""

    status_code = status.HTTP_202_ACCEPTED
    default_detail = "The offline package is being prepared. Try again shortly."
    default_code = "offline_package_preparing"


def license_days() -> int:
    return max(1, int(getattr(settings, "FILE_OFFLINE_LICENSE_DAYS", 14)))


def max_per_device() -> int:
    return max(1, int(getattr(settings, "FILE_OFFLINE_MAX_PER_DEVICE", 40)))


def max_per_user() -> int:
    return max(1, int(getattr(settings, "FILE_OFFLINE_MAX_PER_USER", 120)))


# -- blobs --------------------------------------------------------------------------------------------------------------


def source_marker(resource: FileResource) -> str:
    """Identifies the rendered document: the upload, its hash and when its pages were last rendered."""

    processed = resource.processed_at.isoformat() if resource.processed_at else ""
    return f"{resource.file.name}|{resource.sha256}|{processed}"[:600]


def retire_blob(blob: FileOfflineBlob, reason: str) -> int:
    blob.retired_at = timezone.now()
    blob.save(update_fields=["retired_at", "updated_at"])
    return revoke_licenses(FileOfflineLicense.objects.filter(blob=blob), reason)


def _build_blob(resource: FileResource, version: int) -> FileOfflineBlob:
    pages = FilePage.objects.filter(file_resource=resource, is_deleted=False).exclude(rendered_file="")
    max_bytes = int(getattr(settings, "FILE_OFFLINE_MAX_PDF_BYTES", 250 * 1024 * 1024))
    with tempfile.TemporaryFile() as pdf:
        try:
            offline_pdf.write_pdf_from_pages(
                pages.order_by("page_number").iterator(chunk_size=20),
                pdf,
                quality=int(getattr(settings, "FILE_OFFLINE_JPEG_QUALITY", 82)),
                max_bytes=max_bytes,
            )
        except offline_pdf.PdfTooLarge as exc:
            raise OfflinePackageTooLarge() from exc
        except (ValueError, OSError) as exc:
            raise NotFound("File is unavailable.") from exc
        size = pdf.tell()
        pdf.seek(0)
        package = shared.encrypt_source(pdf, size, int(getattr(settings, "FILE_OFFLINE_CHUNK_SIZE", 64 * 1024)))
    with package.cipher as cipher:
        info = package.info
        blob = FileOfflineBlob(
            file_resource=resource,
            version=version,
            key_id=package.key_id,
            wrapped_key=package.wrapped_key,
            source_marker=source_marker(resource),
            plaintext_size=info.plaintext_size,
            package_size=info.size,
            package_sha256=info.sha256,
            chunk_size=info.chunk_size,
        )
        blob.package.save("package.pnof", File(cipher), save=False)
    blob.save()
    return blob


def live_blob(resource: FileResource) -> FileOfflineBlob | None:
    blob = FileOfflineBlob.objects.filter(file_resource=resource, retired_at__isnull=True).first()
    if blob is not None and blob.source_marker == source_marker(resource):
        return blob
    return None


def ensure_blob(file_id: int, *, allow_deferral: bool = True) -> FileOfflineBlob:
    """The live encrypted package of the file's current rendered pages, building (or rebuilding) it when needed.

    Files with more than ``FILE_OFFLINE_INLINE_BUILD_MAX_PAGES`` pages are built by a worker (``allow_deferral``): the
    request then raises ``OfflinePackagePreparing`` (202) and the app repeats it.
    """

    with transaction.atomic():
        resource = FileResource.objects.select_for_update().filter(pk=file_id, is_deleted=False).first()
        if resource is None or not resource.viewer_ready:
            raise NotFound("File is unavailable.")
        live = FileOfflineBlob.objects.filter(file_resource=resource, retired_at__isnull=True).first()
        if live is not None and live.source_marker == source_marker(resource):
            return live
        if allow_deferral and (resource.pages_count or 0) > int(
            getattr(settings, "FILE_OFFLINE_INLINE_BUILD_MAX_PAGES", 80)
        ):
            defer = True
        else:
            defer = False
    if defer:  # outside the transaction: an exception inside it would roll the dispatch back
        from .tasks import dispatch_offline_build

        dispatch_offline_build(file_id)
        raise OfflinePackagePreparing()
    return _rebuild(file_id)


def _rebuild(file_id: int) -> FileOfflineBlob:
    with transaction.atomic():
        resource = FileResource.objects.select_for_update().filter(pk=file_id, is_deleted=False).first()
        if resource is None or not resource.viewer_ready:
            raise NotFound("File is unavailable.")
        live = FileOfflineBlob.objects.filter(file_resource=resource, retired_at__isnull=True).first()
        if live is not None and live.source_marker == source_marker(resource):
            return live
        last = FileOfflineBlob.objects.filter(file_resource=resource).order_by("-version").first()
        next_version = last.version + 1 if last is not None else 1
        if live is not None:
            retire_blob(live, REASON_FILE_REPLACED)
        return _build_blob(resource, next_version)


def blob_content_key(blob: FileOfflineBlob) -> bytes:
    return shared.unwrap_key(blob.wrapped_key)


# -- evaluation ---------------------------------------------------------------------------------------------------------


def license_denial_reason(license_: FileOfflineLicense, *, check_expiry: bool = True) -> str | None:
    """Why the license cannot be used right now, or ``None`` when it is good."""

    if license_.revoked_at is not None:
        return license_.revoke_reason or "revoked"
    if check_expiry and license_.expires_at <= timezone.now():
        return REASON_EXPIRED
    if license_.blob.retired_at is not None:
        return REASON_FILE_REPLACED
    resource = license_.file_resource
    if not resource.viewer_ready:
        return REASON_FILE_UNAVAILABLE
    user = license_.user
    if not user.is_active or getattr(user, "is_deleted", False):
        return REASON_ACCOUNT_DISABLED
    if license_.blob.source_marker != source_marker(resource):
        return REASON_FILE_REPLACED
    if not user_can_access_file(user, resource):
        return REASON_ACCESS_LOST
    return None


def revoke_licenses(queryset, reason: str, *, actor=None, request=None) -> int:
    """Revoke every active license in ``queryset`` and write one audit row per license. Idempotent."""

    rows = list(queryset.filter(revoked_at__isnull=True).select_related("user", "file_resource"))
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
            action=AuditAction.FILE_OFFLINE_LICENSE_REVOKED,
            target=row,
            new_value={"user_id": row.user_id, "file_id": row.file_resource_id, "reason": reason},
            request=request,
        )
    return len(rows)


def revoke_user_licenses(
    user,
    reason: str,
    *,
    file_resource: FileResource | None = None,
    device_hash: str | None = None,
    actor=None,
    request=None,
) -> int:
    """Revoke a user's offline file licenses (all, one file, or one device)."""

    queryset = FileOfflineLicense.objects.filter(user=user)
    if file_resource is not None:
        queryset = queryset.filter(file_resource=file_resource)
    if device_hash is not None:
        queryset = queryset.filter(device_hash=device_hash)
    return revoke_licenses(queryset, reason, actor=actor, request=request)


def revoke_file_licenses(file_id: int, reason: str) -> int:
    return revoke_licenses(FileOfflineLicense.objects.filter(file_resource_id=file_id), reason)


def revoke_lapsed_licenses(*, user_id: int | None = None, file_id: int | None = None) -> int:
    """Re-evaluate the active licenses of a user and/or file and revoke those that no longer pass the access rule.

    Called by signals when memberships, student profiles, accounts or files change, and by the term rollover.
    """

    queryset = FileOfflineLicense.objects.filter(revoked_at__isnull=True).select_related(
        "blob", "file_resource", "user", "user__student_profile"
    )
    if user_id is not None:
        queryset = queryset.filter(user_id=user_id)
    if file_id is not None:
        queryset = queryset.filter(file_resource_id=file_id)
    revoked = 0
    for item in queryset[:2000]:
        reason = license_denial_reason(item, check_expiry=False)
        if reason is not None:
            revoked += revoke_licenses(FileOfflineLicense.objects.filter(pk=item.pk), reason)
    return revoked


def revoke_memberships_licenses(membership_ids: list[int], reason: str = REASON_MEMBERSHIP_ENDED) -> int:
    """Bulk variant for ``queryset.update`` paths that bypass signals (term rollover): group-only file access may be gone."""

    from apps.groups.models import GroupMembership

    user_ids = set(GroupMembership.objects.filter(pk__in=membership_ids).values_list("user_id", flat=True))
    return sum(revoke_lapsed_licenses(user_id=user_id) for user_id in user_ids)


# -- issue / renew / release --------------------------------------------------------------------------------------------


def _require_viewable(user, file_id: int) -> FileResource:
    resource = FileResource.objects.select_related("group").filter(pk=file_id, is_deleted=False).first()
    if resource is None or not resource.viewer_ready or not user_can_access_file(user, resource):
        raise NotFound("File is unavailable.")
    return resource


def _active_count(user, device_hash: str | None = None) -> int:
    queryset = FileOfflineLicense.objects.filter(user=user, revoked_at__isnull=True, expires_at__gt=timezone.now())
    if device_hash is not None:
        queryset = queryset.filter(device_hash=device_hash)
    return queryset.count()


def _extend(license_: FileOfflineLicense, user, request) -> None:
    now = timezone.now()
    license_.expires_at = now + timedelta(days=license_days())
    license_.last_renewed_at = now
    license_.renew_count += 1
    license_.save(update_fields=["expires_at", "last_renewed_at", "renew_count", "updated_at"])
    AuditLogService.log(
        actor=user,
        action=AuditAction.FILE_OFFLINE_LICENSE_RENEWED,
        target=license_,
        new_value={"file_id": license_.file_resource_id},
        request=request,
    )


def issue_license(user, file_id: int, device_hash: str, *, request=None) -> tuple[FileOfflineLicense, bool]:
    """Issue (or refresh) the (user, device, file) license. Returns ``(license, created)``."""

    resource = _require_viewable(user, file_id)
    blob = ensure_blob(resource.pk)
    now = timezone.now()
    with transaction.atomic():
        existing = (
            FileOfflineLicense.objects.select_for_update()
            .select_related("blob", "file_resource", "user")
            .filter(user=user, file_resource=resource, device_hash=device_hash, revoked_at__isnull=True)
            .first()
        )
        if existing is not None and existing.blob_id == blob.pk:
            _extend(existing, user, request)
            return existing, False
        if existing is not None:
            revoke_licenses(FileOfflineLicense.objects.filter(pk=existing.pk), REASON_FILE_REPLACED)
        if _active_count(user, device_hash) >= max_per_device() or _active_count(user) >= max_per_user():
            raise OfflineLimitReached()
        try:
            with transaction.atomic():
                license_ = FileOfflineLicense.objects.create(
                    user=user,
                    file_resource=resource,
                    blob=blob,
                    device_hash=device_hash,
                    issued_at=now,
                    expires_at=now + timedelta(days=license_days()),
                )
        except IntegrityError:  # concurrent issue of the same (user, device, file)
            license_ = FileOfflineLicense.objects.select_related("blob").get(
                user=user, file_resource=resource, device_hash=device_hash, revoked_at__isnull=True
            )
            return license_, False
        AuditLogService.log(
            actor=user,
            action=AuditAction.FILE_OFFLINE_LICENSE_ISSUED,
            target=license_,
            new_value={"file_id": resource.pk, "blob_version": blob.version},
            request=request,
        )
        return license_, True


def get_license(user, file_id: int, device_hash: str, *, include_expired: bool = False) -> FileOfflineLicense:
    """The caller's usable license for this file and device, or 404. Re-checks access live and revokes on loss."""

    license_ = (
        FileOfflineLicense.objects.select_related("blob", "file_resource", "file_resource__group", "user")
        .filter(user=user, file_resource_id=file_id, device_hash=device_hash, revoked_at__isnull=True)
        .first()
    )
    if license_ is None:
        raise NotFound("No offline license for this file on this device.")
    reason = license_denial_reason(license_, check_expiry=not include_expired)
    if reason is not None:
        if reason != REASON_EXPIRED:
            revoke_licenses(FileOfflineLicense.objects.filter(pk=license_.pk), reason)
        raise NotFound("No offline license for this file on this device.")
    return license_


def renew_license(user, file_id: int, device_hash: str, *, request=None) -> FileOfflineLicense:
    """Extend an existing license (also an expired one) while access holds; refuses otherwise."""

    license_ = get_license(user, file_id, device_hash, include_expired=True)
    _extend(license_, user, request)
    return license_


def release_license(user, file_id: int, device_hash: str, *, request=None) -> int:
    return revoke_licenses(
        FileOfflineLicense.objects.filter(user=user, file_resource_id=file_id, device_hash=device_hash),
        REASON_RELEASED,
        actor=user,
        request=request,
    )


# -- payload helpers ----------------------------------------------------------------------------------------------------


def license_summary(license_: FileOfflineLicense) -> dict[str, Any]:
    return {
        "license_id": str(license_.public_id),
        "file_id": license_.file_resource_id,
        **{k: v for k, v in shared.summary_fields(license_, license_.blob).items() if k != "license_id"},
    }


def license_payload(license_: FileOfflineLicense) -> dict[str, Any]:
    """Response of issue/renew: the summary plus the content key and the format parameters."""

    return {
        **license_summary(license_),
        **shared.payload_fields(license_.blob, f"/api/v1/files/{license_.file_resource_id}/offline-package/"),
    }


def sync_inventory(user, device_hash: str, license_ids: list[str]) -> dict[str, list[str]]:
    """``keep`` / ``renew`` / ``purge`` for the app's local inventory (semantics as for lectures)."""

    parsed = [value for value in (shared.safe_uuid(raw) for raw in license_ids) if value]
    owned = {
        item.public_id: item
        for item in FileOfflineLicense.objects.select_related("blob", "file_resource", "user").filter(
            user=user, public_id__in=parsed
        )
    }
    return shared.reconcile_inventory(
        owned,
        license_ids,
        device_hash,
        denial_reason=lambda item, check_expiry=True: license_denial_reason(item, check_expiry=check_expiry),
        revoke=lambda item, reason: revoke_licenses(FileOfflineLicense.objects.filter(pk=item.pk), reason),
    )
