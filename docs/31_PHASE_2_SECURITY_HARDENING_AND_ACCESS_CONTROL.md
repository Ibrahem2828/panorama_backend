# Phase 2: Security Hardening and Access Control

## Purpose

Phase 2 hardens authentication, OTP handling, password reset behavior, upload validation, file access, chat safety, assignment rules, audit coverage, and production security settings without changing the existing mobile or dashboard API contract.

## What Changed

- Added DRF scoped throttles for sensitive endpoints.
- Added configurable upload limits and allowed extension settings.
- Added OTP max-attempt enforcement.
- Made password reset requests enumeration-safe for unknown well-formed phone numbers.
- Added upload validation for images and documents.
- Added a protected file access endpoint and a `secure_file_url` response field while keeping the existing `file` field unchanged.
- Hardened chat message validation and cross-group reply handling.
- Hardened WebSocket payload handling for malformed JSON.
- Restricted print and support assignment targets by role.
- Expanded audit logging for representative sensitive actions.
- Added production security header settings.
- Added focused Phase 2 security regression tests.

## Intentionally Not Changed

- No documented API paths were changed.
- No documented HTTP methods were changed.
- No existing request fields were renamed or removed.
- No existing response fields were renamed or removed.
- The unified API response envelope was not changed.
- API collection JSON files were not modified.
- No new dependencies were added.
- Existing mobile and dashboard workflows remain backward-compatible for valid requests.

## API Compatibility Guarantee

Existing clients can continue using the current endpoints and fields. Invalid or abusive requests are now more likely to be rejected, but valid existing requests should continue to work.

The existing `file` serializer field remains present. The new `secure_file_url` field is additive and intended for future client migration to protected file access.

## Authentication and Throttling

Scoped throttles were added using built-in DRF throttling:

- `auth_login`
- `otp_send`
- `otp_verify`
- `password_reset`
- `change_password`
- `chat_message`
- `support_message`
- `print_order`

Default rates are configured in `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]` and can be overridden with environment variables such as `THROTTLE_AUTH_LOGIN` and `THROTTLE_OTP_SEND`.

Testing settings raise these rates to avoid suite flakiness. Dedicated throttle tests override rates deterministically.

## OTP Hardening

OTP codes remain hashed and are not stored in plain text. Phase 2 adds:

- Configurable `MAX_OTP_VERIFY_ATTEMPTS`.
- Max-attempt cutoff before accepting even the correct code.
- Audit logging for max-attempt OTP failures.
- Existing expiry and one-time-use behavior remains enforced.
- `RETURN_DEVELOPMENT_OTP` remains disabled in production settings.

## Password Reset Enumeration Protection

Password reset requests now return the same public success envelope for unknown well-formed phone numbers. The backend only creates an OTP when a matching user exists.

Invalid phone formats still return validation errors.

## Permission Audit Summary

Reviewed and preserved existing object-level protections for:

- Student profile ownership and approved-profile edit blocking.
- Verification review permissions.
- Group eligibility, blocked-membership handling, and dashboard membership controls.
- File visibility filtering.
- Print order owner visibility and dashboard role restrictions.
- Support ticket owner visibility and dashboard role restrictions.
- Chat membership and moderation rules.

Fixed or strengthened:

- Print assignment now only accepts `print_staff`, `admin`, or `it_support`.
- Support assignment now only accepts `admin` or `it_support`.
- Chat replies must target messages in the same group.

## File Access

The existing `file` field remains unchanged for backward compatibility.

Added:

- `secure_file_url` on file serializer responses.
- `GET /api/v1/files/{file_id}/view/` protected file view.
- Authentication and object-level visibility checks before file streaming.
- Safe file-not-found handling.
- Audit logging for protected file access.

## Upload Validation

Centralized upload validation rejects:

- Empty files.
- Oversized files.
- Missing extensions.
- Dangerous executable/script extensions.
- Extensions outside configured allowlists.

Settings:

- `MAX_IMAGE_UPLOAD_SIZE_MB`
- `MAX_DOCUMENT_UPLOAD_SIZE_MB`
- `ALLOWED_IMAGE_EXTENSIONS`
- `ALLOWED_DOCUMENT_EXTENSIONS`

Validated upload points include verification card images, student card images, group images, file resources, print uploads, support attachments, and chat attachments.

## Chat Hardening

Phase 2 adds:

- Max message length via `MAX_CHAT_MESSAGE_LENGTH`.
- Whitespace-only text rejection.
- Cross-group reply rejection.
- Type-aware attachment validation.
- Malformed WebSocket JSON handling with safe error messages.
- Existing send-permission enforcement remains shared through `ChatMessageService`.

## Assignment Validation

Print order `assigned_to` accepts only:

- `print_staff`
- `admin`
- `it_support`

Support ticket `assigned_to` accepts only:

- `admin`
- `it_support`

The `assigned_to` request field is unchanged.

## Audit Coverage Added

Added or expanded audit events for:

- Logout.
- Password change.
- Password reset confirmation.
- OTP max-attempt failures.
- Verification submit.
- Group create/update/delete.
- Membership approve/reject/block and role change.
- File upload/update/delete/access.
- Print assignment and internal note update.
- Support assignment, priority change, and staff reply.

Audit logging remains best-effort and redacts sensitive keys through the existing audit service.

## Security Headers and Settings

Production settings now include:

- `SECURE_CONTENT_TYPE_NOSNIFF = True`
- `SECURE_REFERRER_POLICY`
- `X_FRAME_OPTIONS`
- Configurable HSTS settings.
- Existing explicit CORS and CSRF origin requirements.
- Existing `SECURE_PROXY_SSL_HEADER` for reverse-proxy deployments.

No domains are hardcoded.

## Tests Added

Added `app/apps/common/tests_phase2_security.py` covering:

- OTP success, no plain storage, reuse rejection, expiry rejection, and max-attempt lockout.
- Password reset enumeration-safe response.
- Login throttling envelope.
- Upload validation for images and documents.
- Protected file view permission checks.
- Chat cross-group reply and blocked-send rejection.
- Admins-only chat send behavior and moderator delete.
- Print and support assignment role validation.
- Production development-OTP and security header settings.

## Validation Commands

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest app\apps\common\tests_phase2_security.py
.\.venv\Scripts\python.exe -m pytest app\apps\common\tests_api_contract_collections.py
.\.venv\Scripts\python.exe -m pytest app\apps\common\tests_production_hardening.py
.\.venv\Scripts\python.exe -m pytest
.\scripts\validate_backend.ps1 -DeployCheck
```

## Known Limitations

- Protected file view streams local/media-backed files; production object storage should still be reviewed in the next phase.
- Upload validation is extension and size based; it is not antivirus or deep content scanning.
- Throttling uses Django/DRF cache behavior. Production should use a shared cache backend for multi-instance deployments.
- WebSocket typing events are still lightweight and not separately throttled.
- Audit coverage was expanded for representative sensitive paths, not every possible CRUD action in the system.

## Remaining Risks for Phase 3

- Multi-instance cache and Redis reliability for throttling and Channels.
- Background task robustness and retry behavior.
- Media/object storage authorization strategy.
- Database integrity constraints and idempotency for high-traffic workflows.
- Performance of dashboard list filters at larger data volumes.
- Operational monitoring, alerting, and structured security logs.

## Next Recommended Phase

Phase 3 should focus on reliability, performance, and data integrity.
