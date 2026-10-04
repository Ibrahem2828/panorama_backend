# Mobile product integration

Owner: Backend Platform Team  
Last reviewed: 2026-08-14
Contract: `/api/v1/`

## Bootstrap before authenticated traffic

The app calls `GET /api/v1/mobile/bootstrap/` before any protected request. It
sends `X-App-Platform` (`android` or `ios`), `X-App-Version`, `X-App-Build`,
`X-Installation-ID`, and `X-Device-Locale`. The response contains only public,
session-independent configuration: current API version, update policy,
maintenance information, explicitly exposed feature flags, and policy versions.
It never contains a secret, a role grant, or a user record.

`MobileAppReleasePolicy` is managed by a permitted dashboard operator. A
required update causes protected mobile traffic below the configured build to
receive `426 APP_UPDATE_REQUIRED`; health, bootstrap, update-policy, and current
policies remain reachable for recovery. The emergency bypass is audited and is
for correcting a bad store policy, not for permanently disabling version checks.

## Maintenance and flags

`MaintenanceMode` returns `503 MAINTENANCE_MODE` and `Retry-After` for API
traffic while active. Health, bootstrap, current policy, and dashboard routes
remain reachable so an operator can recover. Dashboard authorization remains
server-side; maintenance bypass never grants a client a capability.

Feature flags have a safe default and optional platform/role scopes. Flags are
short-cacheable, invalidated on change, audited, and cannot bypass RBAC. Mobile
clients read only flags marked for public exposure; they cannot submit a flag
value to influence server behavior.

## Devices, policies, and deletion

Authenticated installs register with `POST /api/v1/mobile/devices/register/`.
The server stores a stable installation UUID, non-invasive platform/version and
locale metadata, an optional push token, and revocation state. A push token may
belong to one installation only. The `Idempotency-Key` header provides replay
safe registration; never use a device fingerprint.

Current terms and privacy versions are public at `GET /api/v1/policies/current/`.
Authenticated acceptance records only the version, language, and time. Account
deletion is feature-gated, has a grace period, can be cancelled, blacklists
refresh tokens when executed by Celery Beat, revokes push installations, and
anonymizes the account while preserving a minimal audit trail.

## Client integration sequence

1. Generate and persist one installation UUID per installation.
2. Call bootstrap on launch; handle `426` by directing the user to the store.
3. Treat `503` as a retryable maintenance state and honor `Retry-After`.
4. Authenticate, then register/update the installation with a new push token.
5. Read the canonical collection and OpenAPI rather than guessing payloads.
6. Send `X-Request-ID` and an `Idempotency-Key` for retryable writes.

## Group chat WebSocket

1. With a valid REST bearer token, call
   `POST /api/v1/groups/{group_id}/chat-ticket/`.
2. Read `data.ticket`, `data.expires_at`, and `data.websocket_path` from the
   normal success envelope.
3. Connect before expiry using
   `wss://api-host{websocket_path}?ticket={ticket}`. Do not put the REST access
   token in a query string or substitute it for the ticket.
4. A ticket is single-use. Obtain a fresh ticket for every new connection and
   only reconnect a bounded number of times:

   - `4401` means the authentication/session state is invalid. Refresh normal
     REST authentication when appropriate, then request one fresh ticket.
   - `4403` means the user is authenticated but forbidden for this group. Stop
     blind reconnecting, refresh group/membership state, and show the access
     state. Do not treat it as a ticket-refresh loop.

Example response data:

```json
{
  "ticket": "opaque-random-value",
  "expires_at": "2026-08-13T10:00:45Z",
  "websocket_path": "/ws/v1/groups/42/chat/"
}
```

The protected file contract is unchanged: first obtain its REST access ticket,
then request the returned `preview_url` with `Authorization: Bearer <access>`.
Ticket expiry and limited multi-use behaviour intentionally accommodate PDF
range requests; never treat a protected `preview_url` as a public URL.

## MOBILE INTEGRATION HANDOFF

- Canonical contract: `docs/api/openapi.json` (OpenAPI 3.0.3), SHA-256
  `EA9745F55299680F4B76F63ACD495306B658306F5ED9A36A3BB2A976148DC653`;
  the generated Mobile Postman collection is
  `integrations/api/panorama-mobile-api.postman_collection.json`.
- REST JWT/refresh behaviour is unchanged. The new additive endpoint is
  `POST /api/v1/groups/{group_id}/chat-ticket/`; it returns 201 with the normal
  success envelope. Handle 401/403 normally and retry a transient
  `503 CHAT_TICKET_SERVICE_UNAVAILABLE` only after a safe backoff.
- Use only `?ticket=<opaque-ticket>` when building the group-chat WebSocket
  URL. A ticket lasts 45 seconds by default, is single-use, and a reconnect
  always requires a new REST ticket. JWT query authentication is deprecated and
  disabled in production. `4401` is an authentication/session failure and may
  use the bounded REST-refresh/ticket/reconnect sequence. `4403` is an
  authorization failure and must stop blind reconnects while the client refreshes
  group/membership state.
- Protected files retain the existing two-step contract: obtain the access
  ticket through its authorized REST endpoint, then send the bearer header on
  `preview_url` requests. Range requests are supported by the file response;
  do not assume the preview URL is public or single-request.

The import-ready canonical collection is
`integrations/api/panorama-mobile-api.postman_collection.json`. It is generated
from OpenAPI; regenerate it after a deliberate contract change.
