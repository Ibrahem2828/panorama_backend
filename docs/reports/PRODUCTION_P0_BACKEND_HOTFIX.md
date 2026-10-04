# Production P0 Backend Hotfix

Date: 2026-08-15  
Verdict: **BACKEND PRODUCTION BLOCKED**

## 1. Executive Verdict

The reviewed backend release candidate is locally healthy, but production is not eligible for dashboard closure. Production still returns readiness/startup `503` responses and Django default HTML `500` for invalid login.

The validated P0 candidate is `dbee14ef0840aa68e6c39dd4516cbbfe128edfbb`. It has not been published to the protected `main` release branch. Publishing and deployment need explicit release-owner authorization.

## 2. Root Cause

### `GET /api/v1/health/ready/` → `503`

- Root cause: Not identified from production runtime diagnostics.
- Classification: `UNKNOWN`.
- Evidence: Three controlled JSON `503 SERVICE_NOT_READY` responses: `p0-ready-1-80b29d89-52bb-4739-93e2-90ce0544dea1`, `p0-ready-2-d3124433-77ce-40bb-9d2d-c6def3285837`, and `p0-ready-3-35bd4814-0bce-4a52-b110-ea3b0537622a`.
- Confidence: High that readiness is fail-closed; insufficient evidence to choose between cache, migrations, critical configuration, or storage.

### `GET /api/v1/health/startup/` → `503`

- Root cause: Not identified from production runtime diagnostics.
- Classification: `UNKNOWN`.
- Evidence: Three controlled JSON `503 STARTUP_NOT_READY` responses: `p0-startup-1-ddc81438-44e1-4356-8e9f-9db2b3125efc`, `p0-startup-2-113d1329-c8a2-4c32-8903-37cd02e98a31`, and `p0-startup-3-c93a587a-01ba-411b-a50a-f09d1d5605e2`.
- Confidence: High that startup prerequisites are failing closed; the public contract intentionally omits the failed prerequisite.

### Invalid `POST /api/v1/auth/login/` → HTML `500`

- Root cause: The running HTTP process does not contain the P0 API error-envelope release candidate. The original runtime exception still requires request-correlated logs.
- Classification: `DEPLOYMENT_REVISION_MISMATCH`.
- Evidence: Three public probes returned `500 text/html; charset=utf-8` with `Server: daphne` and preserved request IDs. A safe fingerprint of the 145-byte page matched Django’s default `Server Error (500)` HTML and had no traceback marker. Commit `dbee14e` adds the JSON error envelope and regressions. Remote `main` is `6814a3c561eb500355715f904ba846f0c4880aa7`, two commits behind the candidate; immutable-image publication runs only for `main`.
- Confidence: High for source/runtime revision mismatch. Actual image digest and exception remain unknown without runtime metadata and logs.

## 3. Deployed Revision

| Item | Evidence |
| --- | --- |
| Local Git SHA | `dbee14ef0840aa68e6c39dd4516cbbfe128edfbb` |
| Expected P0 release SHA | `dbee14ef0840aa68e6c39dd4516cbbfe128edfbb` |
| Published `main` SHA | `6814a3c561eb500355715f904ba846f0c4880aa7` |
| Actual deployed SHA/image | Unknown; no runtime release metadata, Coolify access, or container inspection is available. |
| Replica count | Unknown. |
| Revision consistency | Unknown. |

A dry-run proved the candidate can fast-forward `main`. The actual protected-branch push was not performed because it triggers the production release workflow and needs explicit release-owner approval.

## 4. Readiness Dependency Matrix

| Dependency | Result | Safe evidence |
| --- | --- | --- |
| Database | PASS | `/api/v1/health/db/` returned JSON `200` three times, exercising the runtime database health check. |
| Redis/cache | UNKNOWN | No runtime cache probe or correlated logs available. |
| Migrations | UNKNOWN | Local migration-drift check passed; production `django_migrations` is inaccessible. |
| Configuration | UNKNOWN | Candidate settings validation passed with redacted validation-only inputs; deployed settings are inaccessible. |
| Storage/media | UNKNOWN | Local temporary storage probe passed; real named volume/mount/ownership/persistence is inaccessible. |
| Other/liveness | PASS | `/api/v1/health/live/` returned JSON `200 LIVE` three times. |

Current source checks database, cache, migration plan, production storage/configuration, and media access. It emits safe diagnostic events `health_dependency_check_failed`, `health_pending_migrations`, or `health_invalid_critical_configuration`.

## 5. Login Failure Analysis

| Item | Result |
| --- | --- |
| Public behavior | 3/3 canonical invalid logins → `500 text/html`; no traceback marker. |
| Internal application behavior | Not available: no container/listener/runtime-network access. |
| HTML layer | Django default 500 output from a process identifying as Daphne; not an observed generic proxy error. |
| Actual exception/dependency | Unknown pending structured runtime logs. |
| Fix | Promote `dbee14e`, then restore any separately proven failed dependency without weakening readiness. |

The candidate login pipeline is routing → product lifecycle middleware → login throttle/cache → feature flag → serializer/database query → DRF exception handler → API error middleware. Incorrect credentials are designed to be non-enumerating JSON `400 VALIDATION_ERROR`. Redis throttle failure is JSON `503 SERVICE_DEPENDENCY_UNAVAILABLE`; unexpected API exception is safe JSON `500 INTERNAL_SERVER_ERROR`.

## 6. Changes Made

- Configuration: none in production; environment not accessible.
- Deployment: none; protected release publication is awaiting explicit authorization.
- Code: none during this investigation; existing `dbee14e` is the minimal hotfix candidate.
- Tests: existing regression and complete local suites executed.
- Docs: this evidence report and blocked handoff.

## 7. API Compatibility

| Item | Result |
| --- | --- |
| OpenAPI SHA before/after | `EA9745F55299680F4B76F63ACD495306B658306F5ED9A36A3BB2A976148DC653` |
| Inventory | 184 paths / 272 operations / 222 schemas |
| Breaking change | NO |
| Validation/drift | PASS |
| Contract coverage | 272/272 operations |

## 8. Migrations

New migrations: **NO**. Local `makemigrations --check --dry-run` reported no changes. Production migration history remains an external release gate.

## 9. Test Results

| Gate | Result |
| --- | --- |
| Django check | PASS — 0 issues |
| Deploy check | PASS — 0 issues with validation-only redacted values |
| Migration drift | PASS — no changes detected |
| Pytest | PASS — 138 passed, 1 skipped (real Redis URL not configured) |
| Ruff | PASS — check passed; 232 files formatted |
| Mypy | PASS — 246 source files, 0 issues |
| Bandit | PASS — medium-or-higher scan exit 0 |
| pip check | PASS — no broken requirements |
| pip-audit | PASS — no known vulnerabilities |
| OpenAPI | PASS — frozen SHA, validation, drift |
| Contract coverage | PASS — 272/272 |

## 10. Production Verification

| Probe | Repeated result |
| --- | --- |
| live | 3/3 JSON `200 LIVE`; 264–3266 ms |
| database | 3/3 JSON `200` |
| ready | 3/3 JSON `503 SERVICE_NOT_READY`; 296–884 ms |
| startup | 3/3 JSON `503 STARTUP_NOT_READY`; 369–573 ms |
| invalid login | 3/3 `500 text/html`; 325–520 ms |
| Content-Type | Health JSON; invalid login HTML (failing gate) |
| X-Request-ID | Preserved on all probes |
| Replica consistency | Unknown; no replica identifiers exposed |

## 11. Remaining External Gates

1. Explicitly authorize publishing `dbee14e` to protected `main`, retaining the immutable image digest.
2. Deploy that exact image through Coolify and run the release service once.
3. Correlate safe application logs for the recorded IDs to determine whether cache, migrations, configuration, or storage is failing.
4. Run safe runtime checks: production environment validation, migration history, cache set/get/delete, and media write probe.
5. Confirm every replica revision and repeat all acceptance probes three times.

## 12. Dashboard Handoff

Dashboard closure verification must **not** resume. It may resume only after post-deployment evidence shows readiness/startup JSON `200`, invalid login controlled JSON 4xx, consistent replica revisions, and the frozen OpenAPI SHA.
