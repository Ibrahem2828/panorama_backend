# API changelog

## 2026-07-30 — release candidate contract regeneration

- Regenerated `docs/api/openapi.json`, `docs/api/openapi.yaml`, and `docs/api/postman_collection.json` using the Django test settings and drf-spectacular validation.
- Current schema: OpenAPI 3.0.3, 146 paths, 265 generated Postman requests.
- No API version path was changed in this task; all published endpoints remain under `/api/v1/`.
- Health additions in the candidate are additive (`/health/live/`, `/health/ready/`, `/health/startup/`).

Breaking changes: **none identified by schema generation**. This is not a substitute for deployed client contract tests; WebSocket protocol documentation and mobile/dashboard E2E contract execution remain release blockers.
