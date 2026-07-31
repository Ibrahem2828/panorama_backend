# API security and authentication

Owner: Application Security Team  
Last reviewed: 2026-07-31

## Compatibility

Existing API v1 routes and response envelopes are preserved. The lecture API is
additive under `/api/v1/lectures/` and `/api/v1/dashboard/lectures/`. Generated
OpenAPI is the contract source of truth. Breaking changes require a versioned
route, migration plan, contract tests, and changelog entry.

Successful responses use `success`, `code`, `message`, `data`, and a request ID
when available. Failures are normalized by the DRF exception handler and never
return production tracebacks or secret values.

## Authentication and OTP

JWT access/refresh uses refresh rotation and blacklist support. Logout and a
password change blacklist outstanding refresh tokens. Inactive users are not
authorized. Tokens, Authorization headers, cookies, passwords, and OTP values
must not be logged.

OTP values are generated with `secrets`, stored as password hashes, expire,
become single-use, track failed attempts, lock after the configured limit, and
invalidate a prior active code on resend. Request and verification throttles use
both source IP and a hashed submitted identifier. Public request/reset flows use
generic success messages to avoid account enumeration. SMTP has a finite timeout;
the current synchronous delivery keeps the raw OTP out of Redis/Celery messages
and revokes the issued OTP if SMTP delivery fails.

## Authorization and private files

Capabilities define staff access, with expiring per-user allow/deny overrides.
Every protected file stream rechecks authentication, ownership/ticket binding,
and resource RBAC; it streams through Django storage rather than a filesystem
path. Responses set inline disposition where appropriate, `private, no-store`,
`nosniff`, and no public storage URL.

`/media/` must never be proxied publicly in production. The development-only
static media route is guarded by `DEBUG` and cannot be enabled by production
settings.

## Mobile lifecycle and idempotency

Mobile version headers are advisory unless the server has an active required
release policy. The server then returns `426 APP_UPDATE_REQUIRED` only to
protected mobile API traffic below the minimum build; bootstrap, policy, and
health recovery routes are excluded. A maintenance window returns
`503 MAINTENANCE_MODE` with `Retry-After` but leaves health and dashboard
recovery controls available.

An `Idempotency-Key` is scoped to the authenticated actor and endpoint. The
database stores hashes, the payload fingerprint, a bounded response, and an
expiry—not the raw key or sensitive body. Reusing a key with a different body
returns a validation error; a concurrent in-flight use returns a conflict.
Feature flags only disable behavior and never replace authentication or RBAC.
