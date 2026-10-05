# Changelog

## 2026-10-05 — Final hardening pass

- **Security:** admins can no longer escalate privileges through permission overrides or act on IT Support / peer
  admins (role-rank checks, own-override edits blocked, capabilities only delegable by holders). Failed OTP guesses
  now persist so the lockout works (it was rolled back before), per-account throttles added for OTP request/verify and
  password reset, password-reset cooldown no longer reveals whether an account exists, login timing equalised,
  non-object JSON bodies return 400 instead of 500, reset codes no longer mark an email as verified.
- **Reliability:** log redaction and JSON logs fixed (redaction wrote a literal ``, dropped %-arguments and hid every
  traceback location), audit rows with Decimal values are no longer silently dropped, feedback PII/secret redaction and the
  repeated-character flag actually match, idempotency keys stuck after a failed first attempt can be taken over after 60s,
  campaign pushes honour `push_enabled`, login stays reachable during maintenance, invalid `X-Viewer-Session` is a 404.
- **Operations:** daily retention purge and a stuck-lecture sweeper are scheduled in Celery beat, DB connection reuse and
  timeouts, safer Celery redelivery settings, WebSocket origin validation, `/health/db` returns the standard 503 envelope,
  optional Sentry (`SENTRY_DSN`) with PII scrubbing, hashed locks updated (Django 5.2.17, DRF 3.17.2, PyJWT 2.15.0,
  pypdf 6.19.0, sqlparse 0.6.0, urllib3 2.8.0).
- **Privacy:** account deletion now erases OTPs, device tokens, notifications and stored files and strips personal text
  from profiles, feedback, tickets, chat and print orders; repeat requests no longer extend the grace period.
- **Dashboard stats:** soft-deleted rows excluded, sections limited to callers holding the matching capability
  (empty object otherwise), aggregated queries, 30s cache.
- **Deployment:** `scripts/deploy/deploy-backend.sh` (build, release job, traffic swap with automatic rollback) and
  `scripts/deploy/check-migrations.py` (read-only migration history consistency check).

## 2026-07-31 — Production closure security correction

- **Security-breaking API correction:** removed accidental router-discovered
  POST, PUT, PATCH, and DELETE methods from viewsets declared read-only. They
  now return 405 Method Not Allowed. No compatibility adapter is provided
  because preserving an unapproved write surface would retain a P0
  authorization risk. Dashboard and Mobile consumers must regenerate from the
  committed OpenAPI/Postman contracts before deployment.
- Narrowed Dashboard user management to GET, PATCH of explicitly writable
  fields, and audited/idempotent activate/deactivate actions. Direct user
  creation and deletion are intentionally not exposed by that API.
- Added additive migration accounts.0005_user_session_version; existing
  access/refresh tokens issued before this migration remain valid only while the
  account session version is 1. Password, role, and account-state security
  events revoke prior sessions.
- Made lecture processing feature checks occur before persistence and record a
  recoverable failure when broker dispatch after commit fails.

## 2026-07-31 — Productization and API v1 contract freeze

- Added additive mobile bootstrap, release policy, maintenance mode, feature
  flags, device installations, policy consent, delayed account-deletion, and
  durable idempotency controls.
- Added notification preferences, expiration/deduplication metadata, safe
  dashboard campaigns, and mobile-installation push support.
- Replaced legacy hand-maintained API collections with canonical Postman v2.1
  Dashboard and Mobile collections generated from OpenAPI.
- Added API collection validation and canonical mobile/dashboard integration
  documentation.

## 2026-07-31 — Lecture viewer platform

- Added additive API v1 lecture routes, private originals, conversion states,
  protected viewer sessions/pages/text/thumbnails, and private student notes.
- Added a dedicated conversion-worker Docker target and a safe capability status
  command.
- Added Redis timeout/retry configuration, conversion queue routing, and lecture
  throttle settings.
- Consolidated evergreen documentation under `docs/`.

No existing API path or response field was removed in this change.
