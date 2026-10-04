# Changelog

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
