# Offline protected lecture packages

Owner: Learning Platform Team
Last reviewed: 2026-11-14

Goal: a student downloads a lecture **once, with internet, into the app's private storage, encrypted**, and can then
study it offline inside the app. The file is never a plain PDF on public storage, so it cannot be copied out, opened
by another app or shared. Access (online *and* offline) hinges **only** on an active approved membership of the
lecture's group (term not ended, account active). Nothing here knows about payment.

## What exists today (audit)

* `Lecture.original_file` (private storage, staff-only via `GET /dashboard/lectures/{id}/original/`) is converted by the
  Celery `conversion` queue to `Lecture.viewer_pdf` (private storage) plus per-page PNGs/thumbnails/text
  (`LecturePage`). Students never receive the PDF: they get rendered pages through
  `POST /lectures/{id}/viewer/session/` (UUID token, 15 min, 1200 page requests) and
  `GET /lectures/{id}/viewer/pages/{n}/` with `X-Viewer-Session`. Responses are `private, no-store`, `nosniff`.
* Access rule: `apps.lectures.services.user_can_view_lecture` (approved student profile + faculty/major match +
  approved membership of the subject's active group; general lectures: approved student, optional faculty).
  Managers (`lectures.manage`, admin, IT) see everything. Queryset visibility requires `published` and `ready`.
* No watermark exists. There is no offline path: every page needs a live session. The mobile app has no lecture viewer
  yet (only a WebView PDF preview for study files with `expo-screen-capture`'s `preventScreenCaptureAsync`).

Weaknesses found: (1) page PNGs are plain images, so a student can screenshot or script-save every page while a
session is valid (inherent to any viewer; the session cap of 1200 requests limits bulk scraping only mildly);
(2) viewer sessions are not bound to a device; (3) nothing supports offline study, which pushes students to
screenshot or export; (4) no per-user marking of rendered pages; (5) membership changes made with
`queryset.update` (term rollover) bypass signals, so any derived state must be re-checked live or revoked explicitly
(done here); (6) a lecture re-processing replaces `viewer_pdf` in place with no version concept (offline packages
introduce one).

## Key management decision

Two designs were considered:

| | A. Per-license key and per-license ciphertext | B. One content key and one ciphertext per (lecture, version) (chosen) |
| --- | --- | --- |
| Encryption cost | One full encryption pass per student per lecture. | Once per lecture version, cached. |
| Storage | N blobs per lecture. | One blob per lecture version. |
| ETag/CDN/resume | Per-user blobs break shared caching. | Stable ETag; resumable and cacheable behind auth. |
| Leak attribution | A leaked key identifies the license. | Same key for all licensed devices. |
| Revocation | Delete the license. | Delete the license; key release is per user/device and stops. |

B is chosen: maintainable, cheap, resumable. The trade-off is explicit: **there is no per-user key**, so a student who
extracts the key and the ciphertext while licensed can decrypt that lecture version for themselves (or share both).
Mitigations: key release is per user and per device and audited; licenses expire (default 14 days) and need a live
membership to renew; every version bump creates a new random key, so a leaked key does not unlock a replaced
document; a dynamic per-user watermark (below) is the proper deterrent and the design leaves room for it. The
content key is a random 32-byte AES key stored **wrapped with the repo's Fernet field-encryption key**
(`apps.common.crypto`, `FIELD_ENCRYPTION_KEY`) in `LectureOfflineBlob.wrapped_key`; the ciphertext lives in private
storage (`lectures/offline/`, never served by any plain-file route). The key is returned only by
`POST .../offline-license/` and `.../renew/` over TLS in a `no-store` response. The app must keep it in
Keystore/Keychain-backed storage (SecureStore), never in AsyncStorage or the file system.

The blob is built lazily on the first license request of a lecture version (streaming, one 64 KiB chunk in memory,
under a row lock on the lecture so concurrent requests build it once). The source is `Lecture.viewer_pdf` (the converted
PDF, or the original PDF after normalisation); plaintext bytes are never served by the offline endpoints.

## Package format ("PNOF" v1)

All integers big-endian. Algorithm id 1 = AES-256-GCM, chunked.

```
header (40 bytes)
  offset size field
  0      4    magic              "PNOF"
  4      1    format version     1
  5      1    algorithm          1
  6      2    flags              0
  8      4    chunk_size         plaintext bytes per full chunk (default 65536; also in the license response)
  12     8    plaintext_size     total plaintext bytes (the PDF size)
  20     16   key_id             UUID bytes of the content key (same as license key_id)
  36     4    nonce_prefix       random per package
chunks, i = 0 .. n-1 where n = ceil(plaintext_size / chunk_size)
  ciphertext   len = chunk_size (last chunk: the remainder)
  tag          16 bytes (AES-GCM tag, appended)
nonce_i = nonce_prefix (4 bytes) || uint64_be(i)                         -> 12 bytes
aad_i   = header (all 40 bytes) || uint32_be(i) || final (1 byte)        -> 45 bytes
          final = 0x01 for the last chunk, 0x00 otherwise
total package size = 40 + plaintext_size + 16 * n
```

Random access: chunk `i` occupies `[40 + i*(chunk_size+16), +len+16)`. To read a PDF byte offset `p`, decrypt chunk
`p / chunk_size`. Decryption must verify the tag of every chunk it uses; any tag failure means the package is corrupt or
tampered with: delete it and re-download. Binding the header into every AAD stops header edits; the index in the nonce
and AAD stops reordering; the `final` flag stops truncation. Verify the whole-file `sha256_of_ciphertext` after a
download (hash of the package bytes, equals the `ETag` without quotes). A reference decrypt lives in
`apps/lectures/tests_offline.py::reference_decrypt`; the encryptor is `apps/lectures/offline_crypto.py`.

The mobile app should decrypt to memory (or a private, non-backed-up cache) and feed the PDF renderer; never write
the decrypted PDF to shared storage, and exclude the package directory from OS backups.

## API

Common: `Authorization: Bearer ...`; optional `X-Panorama-Device-Id` (raw id; the server stores only its SHA-256, same
convention as the device binding). The license is bound to (user, device hash, lecture); an app that omits the header
is treated as one anonymous device and must omit it consistently. Success responses use the usual envelope.
Feature flag `lecture_offline_enabled` (default on). Throttles: `THROTTLE_LECTURE_OFFLINE_LICENSE` (60/hour),
`THROTTLE_LECTURE_OFFLINE_PACKAGE` (600/hour). Non-members, unpublished/unready lectures and anything without a valid
license return **404** (never a hint that the lecture exists).

| Route | Purpose |
| --- | --- |
| `POST /api/v1/lectures/{id}/offline-license/` | Issue the license (201) or refresh the existing one (200, extends expiry). Returns the key. 409 `offline_limit_reached` when the device/user limit is hit. |
| `POST /api/v1/lectures/{id}/offline-license/renew/` | Extend an existing (even expired) license while access holds; 404 once it is revoked or access is gone. |
| `DELETE /api/v1/lectures/{id}/offline-license/` | The app deleted its copy: release the license (frees a slot). |
| `GET /api/v1/lectures/{id}/offline-package/` | Streams the ciphertext package. `Range` (single range), `If-Range`, `ETag`, `If-None-Match` (304), `Accept-Ranges: bytes`. Requires a valid license for this device. |
| `GET /api/v1/lectures/offline-licenses/` | Licenses of this device (no keys) with `state` `active`/`expired`/`revoked` and `revoke_reason`. |
| `POST /api/v1/lectures/offline-licenses/sync/` | Body `{"license_ids": [...]}` (the local inventory, max 500) returns `{keep, renew, purge}`. `renew`: expired but still entitled, call renew before opening. `purge`: delete the local file and key. |
| `GET /api/v1/dashboard/lectures/offline-licenses/?user=ID[&lecture=ID]` | Staff list (`lectures.manage`). |
| `POST /api/v1/dashboard/lectures/offline-licenses/revoke/` | Staff revoke `{user_id, lecture_id?}` (`lectures.manage`), audited with the actor. |

Issue/renew response (`data`): `license_id` (UUID), `lecture_id`, `content_key` (base64 of 32 bytes), `key_id`,
`package_version`, `issued_at`, `expires_at`, `state`, `file_size` (package bytes), `sha256_of_ciphertext`,
`algorithm` (`AES-256-GCM-chunked`), `format_version`, `header_size` (40), `chunk_size`, `tag_size` (16),
`plaintext_size`, `nonce_scheme`, `aad_scheme`, `package_url`, `content_type` (`application/pdf`).

Recommended app flow: online -> issue license -> download package (resume with `Range` + `If-Range: <etag>`) -> verify
sha256 -> store file in private app storage and the key in SecureStore. On every app start while online and daily in
the background: `sync`, renew what is listed under `renew` (and anything expiring within 3 days), purge what is listed
under `purge`. Offline: the app opens a package only while `expires_at` (stored locally) is in the future and the
device clock has not moved backwards (keep a monotonic last-seen timestamp).

## License lifecycle

A license row is `(user, device_hash, lecture, blob/version, issued_at, expires_at, revoked_at, revoke_reason)`.
`expires_at` defaults to 14 days (`LECTURE_OFFLINE_LICENSE_DAYS`). Limits: `LECTURE_OFFLINE_MAX_PER_DEVICE` (40) and
`LECTURE_OFFLINE_MAX_PER_USER` (120) active licenses. Validity is **derived live** on every package request, renewal
and sync from `user_can_view_lecture` plus the lecture being published/ready and the blob not retired; stored
revocation is applied eagerly on top, so dashboards and audit stay accurate.

Eager revocation (`apps/lectures/signals.py`, `offline_services.py`):

* Membership saved as anything but `approved` (rejected, blocked, left, term_ended) or deleted -> licenses of that
  user for the group's subject lectures (`membership_ended`).
* Term rollover uses `queryset.update`, so `universities/terms.py` calls `revoke_memberships_licenses(ids)` explicitly.
* Lecture unpublished, deleted or no longer ready -> all its licenses (`lecture_unavailable`).
* Viewer PDF replaced (re-processing/re-upload) -> the blob is retired, its licenses revoked (`lecture_replaced`), and
  the next issue builds version N+1 with a new random key.
* Account deactivated -> all licenses (`account_disabled`).
* `revoke_user_licenses(user, reason, lecture=None, device_hash=None, actor=None)` is the generic hook for the device
  switch flow (reason `device_replaced`); staff action `revoke` uses `revoked_by_staff`.

Audit actions: `lecture_offline_license_issued`, `lecture_offline_license_renewed`,
`lecture_offline_license_revoked` (with reason).

## Security notes (honest)

Prevents: copies in public storage or gallery (only ciphertext on disk, key in Keystore); opening or sharing the file
from other apps (it is not a PDF without the key); casual extraction from a rooted file browser; use after
membership ends once the app syncs, and in any case after the license expires without a successful renewal; replaying
an old key against a replaced document; downloading ciphertext without a live license.

Cannot prevent: screenshots, screen recording or photographing the screen (mitigate with `FLAG_SECURE` /
`preventScreenCaptureAsync`, which only deter); a rooted/jailbroken device or instrumentation (Frida) that reads the
key from memory or Keystore while it is licensed; a student who extracts key and ciphertext during the license window
(same key for every licensed device, see trade-off); an offline student whose membership ended but whose license has
not expired and who never goes online (the exposure window is at most `LECTURE_OFFLINE_LICENSE_DAYS`; shorten it to
tighten); clock rollback (app-side monotonic check recommended).

Recommended future deterrent (not implemented): a dynamic per-user watermark (name/ID, faint, diagonal) drawn by the
app over decrypted pages, and optionally burned into per-user rendered pages for the online viewer. With a shared key
it is the only thing that attributes a leaked screenshot to a person.

## Payment readiness (manual payment later; nothing built now)

How enrollment approval works today: a verified student submits `EnrollmentRequest` (Learnata screenshot + subject
items) through `POST /enrollment/requests/`; reviewers (capability `enrollment.review`, held by admin and IT support by
role, grantable to others with a per-user permission override) decide with
`POST /dashboard/enrollment/requests/{id}/decision/` or `.../bulk-decision/`
(`EnrollmentService.decide`). Approving an item calls `add_to_group`, which creates or restores an **approved**
`GroupMembership` in the subject's groups (`term_ended`, `left`, `rejected`, `pending` are restored; `blocked` is
never lifted). That membership is the only key to lectures, chat and files, which is exactly what offline licenses
check. A reviewer cannot review their own request. Subject groups cannot be joined by hand any more
(`ENROLLMENT_REQUIRED`); the legacy group membership approve/reject/block endpoints (`groups.manage`) only act on
existing rows.

Recommended minimal future changes, all at the approval step so lectures and licenses need no change:

1. A separate capability, for example `enrollment.confirm_payment`, required in `EnrollmentService.decide` to approve
   any item while payment is enforced (reviewers without it can still reject). Keep `enrollment.review` for the
   screenshot check, so "verified registration" and "money received" stay separable duties.
2. Fields on the decision (or per item): `payment_note` (free text: receipt/reference, amount, contact), `received_by`
   (FK to the staff member who took the money), `payment_confirmed_at`; write them to the audit entry
   (`enrollment_reviewed`, already `critical`) and show them on the dashboard request detail.
3. A feature flag `enrollment_payment_required` (via `FeatureFlagService`) so the gate can be switched on per
   environment; when on, `decide` refuses an approval without the capability and a payment note.
4. Close the side doors at that time: the legacy membership approve endpoints and any staff "add member" path must go
   through the same check (or be limited to non-subject groups), otherwise `groups.manage` could approve a subject
   membership without payment.
5. Optional: per-item `access_until` (term end already ends memberships) so a partial payment can map to specific
   subjects; offline licenses follow automatically because they are derived from membership.

## Operations

Settings (see `.env.example`): `LECTURE_OFFLINE_LICENSE_DAYS`, `LECTURE_OFFLINE_MAX_PER_DEVICE`,
`LECTURE_OFFLINE_MAX_PER_USER`, `LECTURE_OFFLINE_CHUNK_SIZE` (changing it only affects newly built packages) and the
two throttles. `FIELD_ENCRYPTION_KEY` must stay stable: rotating it makes existing wrapped keys unreadable (retire
blobs and let them rebuild if it ever has to change). Back up `lectures/offline/` with other media or let it rebuild:
the packages are derived data and licenses of a lost blob can simply be re-issued after revocation. Migrations:
`lectures.0004_offline_licenses`, `audit.0019_lecture_offline_license_actions` (extends action choices only).
