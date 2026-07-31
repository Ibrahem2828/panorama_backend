# Dashboard integration

Owner: Backend Platform Team  
Last reviewed: 2026-07-31  
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
