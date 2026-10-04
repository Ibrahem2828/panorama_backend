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

## WebSocket chat handshake

WebSocket connections do **not** accept access JWTs in the final contract.
After REST authentication, a client requests
`POST /api/v1/groups/{group_id}/chat-ticket/`. The server checks the active
account, the current session version, active group state, approved membership
where applicable, and capability-based chat access. It returns an opaque,
cryptographically random, user/group/session-version-bound ticket, an expiry,
and the WebSocket path.

The client connects once using
`wss://<host>/ws/v1/groups/{group_id}/chat/?ticket=<opaque-ticket>`. Tickets
live in Redis/cache for 45 seconds by default (production accepts only
30–60 seconds), are consumed atomically on the handshake, and must never be
written to audit records or logs. Production's Django Redis cache implements
the reservation with `SET ... NX`; one successful reservation is the sole
consumer and the payload is then deleted. A cache/Redis failure returns a
controlled `503` while issuing and rejects the WebSocket handshake rather than
bypassing the ticket.

The socket rechecks account, session version, group, and membership state before
every client action. Its close-code contract is stable:

- `4401`: authentication or session state is no longer valid (including an
  inactive account or session-version mismatch). A client may refresh normal
  REST authentication if appropriate, obtain one fresh chat ticket, and make a
  bounded reconnect attempt.
- `4403`: the client is authenticated but no longer has group-chat permission
  (for example, blocked membership or disabled group). The client must stop
  blind reconnecting, refresh group/membership state, and present the access
  state to the user.

The reverse proxy/WebSocket access log must redact or omit the `ticket` query
parameter. Application and audit logging deliberately record neither a ticket
value nor a WebSocket query string.

`WEBSOCKET_LEGACY_JWT_QUERY_AUTH_ENABLED` exists only for a controlled client
migration and defaults to `False`; it is deprecated and must remain `False` in
the final production configuration. It never bypasses session-version checks.

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
