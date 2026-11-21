# Offline library files

Owner: Learning Platform Team
Last reviewed: 2026-11-21

Students can download a library file (`apps.files.FileResource`, the "الملفات" library) **once, with internet, into the
app's private storage, encrypted**, and open it later with no connection. The mechanics are the lecture offline
packages (PNOF v1, per-version key, per-device licenses, live revalidation): read
[OFFLINE_LECTURE_PACKAGES.md](OFFLINE_LECTURE_PACKAGES.md) for the format, key management and security notes. This
document covers only what differs for files.

## Audit

* `FileResource` keeps the original upload (`file`: PDF, DOCX, PPTX or an image), metadata and visibility, and, after the
  Celery `conversion` task (`process_file_resource`), one `FilePage` per page (PNG, thumbnail, text). The converted PDF
  is **not** kept. Students only see pages through `FileViewerSession`.
* Access rule (viewer, tickets, listing): `apps.files.services.user_can_access_file` (admin/IT always; `public`;
  `students_only` = has a student profile; `verified_students_only` = approved profile; `major_only` = approved and same
  major+year; `group_only` = approved membership of the file's group; `admin_only` = staff only), with the file active
  and not soft-deleted. The viewer additionally needs `processing_status == ready` (`viewer_ready`). Non-accessible
  files answer 404 on the viewer; the offline endpoints do the same.
* Production (read-only audit): 1 `FileResource`, 53 `FilePage` rows, no offline licenses.

## Design: generalise the machinery, not the tables

`apps/common/offline.py` now owns everything that does not depend on the owning model: key wrapping, the streaming
encryption of a source into a temporary file, summary/payload fields, the ETag/Range/If-Range streaming response, the
`keep/renew/purge` reconciliation, the revoke reasons and `revoke_all_user_offline_licenses`. The PNOF encoder stays in
`apps/lectures/offline_crypto.py` (unchanged). Lecture models, endpoints and responses are untouched (the lecture
views/services now delegate to the shared helpers; the lecture test-suite passes unchanged). Files get their own
tables, `FileOfflineBlob` and `FileOfflineLicense` (one active license per user, device and file; one live blob per
file), so no lecture migration or column was touched.

### Plaintext = a PDF built from the rendered pages

The package plaintext must be a PDF the app can render. Because the converted PDF is not stored, the PDF is generated
from the same `FilePage` PNGs the online viewer serves (`apps/files/offline_pdf.py`): each page becomes a JPEG
(quality `FILE_OFFLINE_JPEG_QUALITY`, 4:4:4) image page; the output is a script-free PDF 1.4 (images only: no
JavaScript, links, forms or embedded files, so it is safe to render). Consequences: same pixels as the online viewer;
no selectable text offline (the online viewer's text layer is not embedded); the original DOCX/PPTX is never exposed.
Memory is bounded: pages are streamed one at a time (a custom writer; Pillow's multi-page writer keeps every decoded page
alive), the PDF goes to a temporary file, and encryption keeps one 64 KiB chunk in memory. Limits: the page count is
already capped by `LECTURE_MAX_PAGES` (500) at processing time and the PDF by `FILE_OFFLINE_MAX_PDF_BYTES` (250 MB;
beyond it `409 offline_package_too_large`). Files above `FILE_OFFLINE_INLINE_BUILD_MAX_PAGES` (80) are packaged by the
`build_file_offline_package` Celery task (queue `conversion`); the first license request answers
`202 FILE_OFFLINE_PREPARING` (`{"state":"preparing","retry_after_seconds":15}`) and the app repeats it until `201`.
Smaller files are built inline in the request (under a row lock on the file).

The blob's `source_marker` is `file name | sha256 | processed_at`: re-uploading or re-rendering a file retires the blob
(licenses revoked, reason `file_replaced`) and the next issue builds version N+1 with a new random key.

## API

Same headers, envelope, `no-store` behaviour and 404-for-everything-else rule as lectures. Feature flag
`file_offline_enabled` (default on). Throttles `THROTTLE_FILE_OFFLINE_LICENSE` / `THROTTLE_FILE_OFFLINE_PACKAGE`
(60/hour, 600/hour).

| Route | Purpose |
| --- | --- |
| `POST /api/v1/files/{id}/offline-license/` | Issue (201) or refresh (200) the license; returns the key. 202 `preparing` for big files, 409 `offline_limit_reached`, 409 `offline_package_too_large`. |
| `POST /api/v1/files/{id}/offline-license/renew/` | Extend an existing, even expired, license while access holds; 404 once revoked or access is gone. |
| `DELETE /api/v1/files/{id}/offline-license/` | Release (frees a slot). |
| `GET /api/v1/files/{id}/offline-package/` | Ciphertext only; `Range`, `If-Range`, `ETag`, `If-None-Match` (304). Needs a valid license for this user and device. |
| `GET /api/v1/files/offline-licenses/` | Licenses of this device (no keys): `state` `active`/`expired`/`revoked`, `revoke_reason`. |
| `POST /api/v1/files/offline-licenses/sync/` | `{"license_ids": [...]}` (max 500) -> `{keep, renew, purge}`. |
| `GET /api/v1/dashboard/files/offline-licenses/?user=ID[&file=ID]` | Staff list (capability `files.manage`), no keys. |
| `POST /api/v1/dashboard/files/offline-licenses/revoke/` | `{user_id, file_id?}`; revokes with reason `revoked_by_staff`, audited with the actor. |

Issue/renew `data`: `license_id`, `file_id`, `content_key` (base64, 32 bytes), `key_id`, `package_version`, `issued_at`,
`expires_at`, `state`, `revoked_at`, `revoke_reason`, `file_size`, `sha256_of_ciphertext`, `algorithm`
(`AES-256-GCM-chunked`), `format_version` (1), `header_size` (40), `chunk_size`, `tag_size` (16), `plaintext_size`,
`nonce_scheme`, `aad_scheme`, `package_url` (`/api/v1/files/{id}/offline-package/`), `content_type`
(`application/pdf`). It is the lecture payload with `lecture_id` replaced by `file_id`.

Settings: `FILE_OFFLINE_LICENSE_DAYS`, `FILE_OFFLINE_MAX_PER_DEVICE`, `FILE_OFFLINE_MAX_PER_USER`,
`FILE_OFFLINE_CHUNK_SIZE` (all default to the matching `LECTURE_OFFLINE_*` value), `FILE_OFFLINE_MAX_PDF_BYTES`,
`FILE_OFFLINE_JPEG_QUALITY`, `FILE_OFFLINE_INLINE_BUILD_MAX_PAGES`.

## Validity and revocation

Validity is derived live on every package request, renewal and sync: license not revoked or expired, blob not retired and
matching the file's `source_marker`, file `viewer_ready` (ready, active, not deleted), account active, and
`user_can_access_file`. Eager hooks (`apps/files/signals.py`) keep stored state and audit accurate:

| Event | Reason |
| --- | --- |
| File hidden (`is_active` false), deleted, or no longer `ready` | `file_unavailable` |
| File re-uploaded / re-rendered | `file_replaced` (new version, new key) |
| File visibility/group/major/year edit, membership leaves `approved` or is deleted, student profile change (verification, major, year), role change | `access_lost` (only for holders who no longer pass the rule) |
| Term rollover (`queryset.update`, no signals): `universities/terms.py` calls `revoke_memberships_licenses` | `access_lost` for group-only files |
| Account deactivated/deleted | `account_disabled` |
| App released the license | `released_by_device` |
| Staff revoke | `revoked_by_staff` |
| **Device switch** (`DeviceBindingService._switch_locked`) | `device_replaced` |
| **Staff device reset** (`DeviceBindingService.reset`) | `revoked_by_staff` (actor recorded) |

Audit actions (migration `audit.0022`): `file_offline_license_issued`, `file_offline_license_renewed`,
`file_offline_license_revoked`.

### Device switch and reset

`apps.common.offline.revoke_all_user_offline_licenses(user, reason, actor=None, request=None)` revokes every active
license of the account, lectures and files. It is called inside the switch and reset transactions (right after
`invalidate_sessions`). Offline licenses are keyed by the SHA-256 of the raw `X-Panorama-Device-Id` while bindings use a
different hash, so the revocation is per account, not per device: the new device holds no licenses yet, so nothing
legitimate is lost. (Before this change the lecture flow was NOT wired to the switch/reset; only the generic
`revoke_user_licenses` existed.)

## Not verified here

Rendering of the generated PDF in the mobile PDF viewer and behaviour on a 50 MB / 500-page file in production were not
exercised (tests build small PDFs and re-read them with pypdf). The app must follow the lecture storage rules (key in
SecureStore, package in private non-backed-up storage).
