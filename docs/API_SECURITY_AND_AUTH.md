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
must not be logged. Access tokens default to 15 minutes
(`JWT_ACCESS_TOKEN_LIFETIME_MINUTES`) and stay valid for at most that long after
logout; a password change, role change or deactivation revokes them immediately
through `session_version`. Login is also locked per account (not just per IP) after
`LOGIN_LOCKOUT_THRESHOLD` consecutive failures; see the Arabic incident runbook.

OTP values are generated with `secrets`, stored as password hashes, expire,
become single-use, track failed attempts, lock after the configured limit, and
invalidate a prior active code on resend. Request and verification throttles use
both source IP and a hashed submitted identifier. Public request/reset flows use
generic success messages to avoid account enumeration. SMTP has a finite timeout;
the current synchronous delivery keeps the raw OTP out of Redis/Celery messages
and revokes the issued OTP if SMTP delivery fails.

## Account device binding (students)

Goal: one student account = one phone, so accounts cannot be shared (subject registration and group joining are
becoming paid). Implemented in `apps/accounts/devices.py`; staff and normal users are exempt.

- The app generates a random UUID once, keeps it in secure storage and sends it on login, refresh and every
  authenticated request: `X-Panorama-Device-Id` (required), `X-Panorama-Device-Name` (optional, <= 80 chars) and
  `X-Panorama-Platform` (`android|ios|web`, optional). Only a SHA-256 of the id is stored (`AccountDevice.device_hash`).
  This is unrelated to push tokens (`DeviceInstallation` / `DeviceToken`).
- First login binds the device. Tokens carry the device hash (`dh` claim, also in the access token).
- Login from another device (right password): `409 DEVICE_MISMATCH`, NO tokens. `errors` has `bound_device`
  (`name`, `platform`, `last_seen_at` only), `can_switch`, `switches_remaining`, `device_switch_token` (signed, single use,
  `DEVICE_SWITCH_TOKEN_TTL_SECONDS`=300 s, bound to the new device id) and `device_switch_token_expires_in`.
- `POST auth/device/switch/` `{device_switch_token}` + the same device headers: the old binding becomes `replaced`, every
  session of the account ends (`User.invalidate_sessions`: refresh tokens blacklisted, `session_version` bumped, so access
  tokens and chat WebSockets/handshake tickets stop working), the new device is bound and a fresh token pair is returned
  (`200 DEVICE_SWITCHED`). Errors: `400 DEVICE_SWITCH_TOKEN_INVALID` (expired, replayed, wrong device),
  `400 DEVICE_ID_REQUIRED`, `403 DEVICE_SWITCH_LIMIT_REACHED` (`contact_admin: true`).
- Refresh from another device: `409 DEVICE_MISMATCH` with `device_switch_token: null` (a refresh token does not prove the
  password); the app must send the user to the login screen. An access token used with another device id: `401
  DEVICE_MISMATCH`; no header while enforced: `401/400 DEVICE_ID_REQUIRED`.
- Ended sessions: a request or refresh with a token of a device that was replaced by a switch returns `401 DEVICE_MISMATCH`
  (message "تم تسجيل الدخول من جهاز آخر"); after a staff reset it returns `401 SESSION_REVOKED_BY_ADMIN`. Only tokens whose
  `dh` claim matches a replaced/revoked device of that same user get these codes (`invalidated_session_error`).
- Logout keeps the binding (so logging out never frees the account). Account deletion revokes it.
- Mismatch attempts are audited (`account_device_mismatch`) and limited to `DEVICE_MISMATCH_MAX_PER_HOUR` per account (429).
  Switch endpoint: `THROTTLE_DEVICE_SWITCH` (10/hour per IP).

Settings (defaults; the owner did not specify the policy):

| Setting | Default | Meaning |
| --- | --- | --- |
| `DEVICE_BINDING_ENFORCED` | `false` | Grace mode: bindings are only RECORDED when the header is present; nobody is blocked. Turn on after the app that sends the header is released. |
| `DEVICE_SWITCH_LIMIT_PER_TERM` | `1` | Self-service switches per window. |
| `DEVICE_SWITCH_WINDOW_DAYS` | `120` | There is no academic-term date model, so a "term" is a rolling window. |
| `DEVICE_SWITCH_TOKEN_TTL_SECONDS` | `300` | Lifetime of the switch token. |
| `DEVICE_MISMATCH_MAX_PER_HOUR` | `10` | Wrong-device logins per account per hour. |

Migration path for sessions that exist at deploy time: while not enforced nothing changes. Once enforced, a legacy token
(no `dh` claim) binds the device on its first refresh or authenticated request that carries the header; a legacy refresh
token without the header is refused with `DEVICE_ID_REQUIRED` and the user signs in again (which binds).

Dashboard (capability `users.manage`, target must rank below the actor): `GET dashboard/users/{id}/account-devices/`
(binding history, switch counter, `binding_enforced`) and `POST dashboard/users/{id}/account-devices/reset/` `{reason}`
(revokes the binding, restarts the switch counter, ends all sessions; audited `account_device_reset`).

## Phone verification (manual WhatsApp code)

Student (role student only): `POST auth/phone-verification/` `{phone_number, password}` creates a request (E.164,
Syrian handling: `09xxxxxxxx`, `9639...`, `00963...`, `+9630...`, Arabic digits all become `+9639xxxxxxxx`; other
countries need `+`/`00`). One open request per student (`409 PHONE_VERIFICATION_ALREADY_OPEN`), 3 creations per day
(`THROTTLE_PHONE_VERIFICATION_CREATE`), a number held by another account is `409 PHONE_NUMBER_IN_USE`.
`GET auth/phone-verification/current/` (`data.request` is null when none), `POST auth/phone-verification/cancel/`,
`POST auth/phone-verification/confirm/` `{code}` (6 digits; `400 PHONE_CODE_INVALID` with `attempts_remaining`,
`PHONE_CODE_ATTEMPTS_EXCEEDED` after `PHONE_VERIFICATION_MAX_ATTEMPTS`=5, `404` once expired/closed, `409 PHONE_CODE_NOT_SENT`).
Success sets `phone_number` and `is_phone_verified` on the user.

Staff (capability `verification.review`, like identity verification): `GET dashboard/phone-verifications/`
(oldest first, `status`/`search` filters), `POST .../{id}/claim/` generates the 6-digit code (`secrets`) and returns it ONCE
in `data.code` (`Cache-Control: no-store`); claiming again by the same staff member issues a new code, another staff member
must wait 15 minutes. `POST .../{id}/mark-sent/` (the "code sent" button) sets `code_sent` and
`expires_at = now + PHONE_VERIFICATION_CODE_TTL_MINUTES` (10); `POST .../{id}/reject/` `{reason}`. Unhandled requests expire
after `PHONE_VERIFICATION_PENDING_TTL_HOURS` (48). The code is stored only as a keyed HMAC (`SECRET_KEY`), compared with
`hmac.compare_digest`, never logged or audited. Students are notified on code sent, verified, rejected and expired; staff
holding the capability are notified of new requests.

## Faculty community auto-join and WhatsApp access

When identity verification is approved (`VerificationService.review`) the student joins, without approval, the faculty
community: active groups of the faculty with no subject, major, academic year or semester
(`enrollment.services.FacultyCommunityService`, shared with `manage.py backfill_faculty_communities`). For the dental
faculty (name contains the Arabic "tibb al-asnan", English "dent", or code DEN/DENT/DDS) the student also joins the group named
"al-sareeriyya 1" (normalized name match: hamza, ta marbuta, diacritics, optional article, Arabic digits, "al-oula"; a
pinned year does not matter; absent group: skipped, logged `dental_clinical_group_missing`). Other numbered clinical groups of
that faculty are not auto-joined. Subject-less groups pinned to a major, year or semester ("level groups") are no longer
offered: they are hidden from `groups/`, `groups/available/` and `groups/{id}/` for non-members, and never auto-joined (rows
and existing memberships are kept). Joining stays enrollment-only (`GROUP_JOIN_ENROLLMENT_ONLY`).

WhatsApp links: no student endpoint serialises the invite (only `has_whatsapp_channel`). `POST groups/{id}/whatsapp-launch/`
returns a `whatsapp://` deep link only to a verified student with an APPROVED membership of that group (pending, rejected,
blocked, left and term-ended members are refused); the https link is never returned and the ticket/redirect flow is retired
(410). Audit of the exposure found no leak; the encrypted value is only visible in the Django admin (readonly).

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
