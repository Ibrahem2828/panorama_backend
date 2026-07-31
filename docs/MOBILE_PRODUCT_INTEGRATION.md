# Mobile product integration

Owner: Backend Platform Team  
Last reviewed: 2026-07-31  
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

The import-ready canonical collection is
`integrations/api/panorama-mobile-api.postman_collection.json`. It is generated
from OpenAPI; regenerate it after a deliberate contract change.
