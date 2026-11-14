# Dashboard integration

Owner: Backend Platform Team  
Last reviewed: 2026-08-14
Contract: `/api/v1/`

## Authorization boundary

Dashboard requests use the normal JWT contract. Every dashboard route performs
server-side capability checks; hiding a UI control does not grant or remove
permission. `dashboard.access` controls entry and domain capabilities control
individual modules. Product lifecycle controls require `product.manage`, held
by the administrator and IT-support role unless an explicit unexpired override
says otherwise.

## Product-control APIs

The dashboard manages:

- `dashboard/mobile-release-policies/` for Android/iOS update rules.
- `dashboard/maintenance-modes/` for bounded maintenance windows.
- `dashboard/feature-flags/` for safe rollout and kill switches.
- `dashboard/terms-versions/` and `dashboard/privacy-policy-versions/`.
- `dashboard/notifications/campaign/` for bounded recipient campaigns.

Each modification is audit logged without credentials, tokens, or message
content. Release policy, maintenance, and flag caches are invalidated as part of
the same model-change path; no dashboard action relies on a client-side cache.

## Phone verification queue and account devices

- Phone verification (capability `verification.review`): list `GET dashboard/phone-verifications/` (phone shown, code never),
  open a row with `POST .../{id}/claim/` (the 6-digit code appears once in `data.code`: show it with a copy button), the
  staff member sends it on WhatsApp and presses the "code sent" button (`POST .../{id}/mark-sent/`, starts the 10-minute window).
  `POST .../{id}/reject/` needs a reason. Error codes: `PHONE_VERIFICATION_CLAIMED_BY_OTHER`, `PHONE_VERIFICATION_NOT_CLAIMED`,
  `PHONE_VERIFICATION_NOT_PENDING`, `PHONE_NUMBER_IN_USE`.
- Student devices (capability `users.manage`): on the student detail page `GET dashboard/users/{id}/account-devices/` shows
  the bound device, the history and the switch counter; "reset device" is `POST .../account-devices/reset/` `{reason}`
  (ends the student's sessions; the next login binds the new phone). This is different from the existing
  `dashboard/devices` push-installation screen.

## Integration requirements

Use the exact paths, methods, response envelopes, pagination, and error codes
from `docs/api/openapi.json`. Send `Authorization: Bearer ...` and a unique
`X-Request-ID`. For retryable creates, include `Idempotency-Key`; repeated keys
with a different body are rejected rather than executed twice.

Import `integrations/api/panorama-dashboard-api.postman_collection.json` into
Postman. It contains no secret, environment, or runtime credential; configure
tokens locally in collection variables. Do not reintroduce the legacy JSON
collections under `docs/api/`.

## Collection migration record

| Previous artifact | Classification | Canonical replacement |
| --- | --- | --- |
| `docs/api/dashboard_api_collection.json` | Hand-maintained Dashboard API collection | `integrations/api/panorama-dashboard-api.postman_collection.json` |
| `docs/api/mobile_api_collection.json` | Hand-maintained Mobile API collection | `integrations/api/panorama-mobile-api.postman_collection.json` |
| `docs/api/postman_collection.json` | Generated mixed API collection | The two role-specific canonical collections |
| `docs/api/openapi.json` and `openapi.yaml` | OpenAPI schema | Preserved as the CI and contract source |

The generator and validator compare all documented HTTP operations to the two
collections. Fixtures, package locks, and unrelated JSON are outside this
mapping and are preserved.

## DASHBOARD INTEGRATION HANDOFF

- Canonical contract: `docs/api/openapi.json` (OpenAPI 3.0.3), SHA-256
  `EA9745F55299680F4B76F63ACD495306B658306F5ED9A36A3BB2A976148DC653`;
  generated alongside `docs/api/openapi.yaml` and the Dashboard collection.
- Contract count: 184 paths and 272 documented HTTP operations. Regenerate it
  with `python scripts/openapi_contract.py generate`; CI validates and rejects
  schema drift.
- Dashboard authentication and capability rules are unchanged: send
  `Authorization: Bearer <access-token>` and `X-Request-ID`; permissions remain
  server-side. There are no added, removed, or deprecated Dashboard endpoints
  in this handoff.
- The additive chat handshake endpoint is Mobile-facing:
  `POST /api/v1/groups/{group_id}/chat-ticket/`. Dashboard clients should not
  send an access JWT in a WebSocket query string.

## Print option help images

`image_url` of `/dashboard/printing/option-help/` and `/printing/option-help/` is now
`/api/v1/printing/option-help/{id}/image/?v=<updated_at epoch>` (authenticated, not `/media/`). A browser `<img src>` cannot
send the Bearer token and the dashboard CSP must allow the origin, so load it through the dashboard BFF (a route that
forwards the call with the session token and streams the bytes) or fetch it with the token and show a blob URL. Staff
(`printing.manage`) may also read inactive entries. Note: a multipart create without `is_active` stores `false`; send
`is_active=true` explicitly when uploading an image with the form.

## Stationery store management

Full reference: `docs/STORE.md`. New capability **`store.manage`** (admin, print staff, IT support; overridable per
user). All routes are under `/api/v1/dashboard/store/`:

* Catalogue CRUD: `brands/` (multipart `logo`), `categories/`, `products/`, `variants/`, `images/` (multipart `image`;
  the thumbnail is generated by the server; the first image is the primary one). Deleting a brand or category that still
  has products is `409 IN_USE`.
* Stock: `POST variants/{id}/stock/ {movement_type: purchase|adjustment|waste, quantity, note}`. A new variant creates its
  inventory item (also visible on the counter quick-sale screen); `initial_stock` and `reorder_level` can be sent on create.
* Orders: `GET orders/?status=&q=&from=&to=`, `GET orders/{id}/`, `PATCH orders/{id}/status/ {status, reason, note}`
  (`confirmed`, `ready_for_pickup`, `picked_up`, `cancelled` with a reason; illegal moves are `409 INVALID_TRANSITION`).
  Cancelling returns the reserved stock; each change notifies the student and is audited.
* Reviews: `GET reviews/?is_hidden=&product=&rating=`, `POST reviews/{id}/hide/ {reason}`, `POST reviews/{id}/unhide/`.
* `GET summary/`: counts by status, orders today, low-stock variants. `GET /dashboard/quick-access/` now also returns
  `store_orders_new` for holders of `store.manage`; new orders arrive as inbox notifications (`kind: store_order`).
* Pictures in these payloads are authenticated URLs (`/api/v1/store/images/{id}/`); fetch them with the bearer token.
* New audit actions: `store_catalog_changed`, `store_price_changed`, `store_order_created`, `store_order_status_changed`,
  `store_review_moderated`.
