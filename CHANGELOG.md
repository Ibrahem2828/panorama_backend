# Changelog

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
