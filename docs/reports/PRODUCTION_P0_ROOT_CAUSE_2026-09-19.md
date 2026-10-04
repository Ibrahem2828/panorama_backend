# Production P0 — root cause identified

Date: 2026-09-19
Verdict: **PRODUCTION FULLY DOWN — root cause identified, fix is operational**

Supersedes the `UNKNOWN` classification in `PRODUCTION_P0_BACKEND_HOTFIX.md` (2026-08-15).

## 1. Severity correction

Previous reports characterised production as healthy-but-unready (`/health/ready/` 503).
That understated the outage. Probing the full public surface shows **every real API
endpoint returns HTTP 500**. The platform has been completely unusable for mobile and
dashboard clients, not degraded.

| Endpoint | Result (2026-09-19) |
| --- | --- |
| `/api/v1/health/` `/health/live/` `/health/db/` | 200 JSON |
| `/api/v1/health/ready/` `/health/startup/` | 503 JSON (fail-closed, as designed) |
| `/api/v1/mobile/bootstrap/` `/api/v1/mobile/update-policy/` | 400 JSON `VALIDATION_ERROR` (correct envelope) |
| `/api/v1/auth/login/` `/auth/register/` | 500 HTML |
| `/api/v1/universities/` `/announcements/` `/notifications/` | 500 HTML |
| `/api/v1/schema/` `/docs/` `/policies/current/` | 500 HTML |

## 2. Root cause

`ProductLifecycleMiddleware` (`apps/product/middleware.py`) runs on every `/api/v1/`
request except four public prefixes and `/api/v1/dashboard/`. It called
`ProductConfigurationService.active_maintenance()`, whose cache read is guarded but whose
`MaintenanceMode.objects.filter(...)` query was not. With the `product` migrations not
applied to the production database, that query raises `ProgrammingError`, so **the
deployed schema does not match the deployed code**.

Django does not route middleware exceptions through `process_exception`, so
`APIErrorEnvelopeMiddleware` never sees it and Django's default HTML 500 is returned.
The same unapplied-migration state makes `migration_plan()` non-empty in
`_dependency_checks()`, which is why readiness and startup return 503.

This single cause explains every observation, including the ones that previously looked
contradictory:

- `/health/db/` passes because it only runs `SELECT 1`; the connection is healthy, the schema is not.
- Exempt prefixes return a correct JSON envelope, proving the application stack, DRF, serializers and the error envelope all work. The 2026-08-15 `DEPLOYMENT_REVISION_MISMATCH` theory for the HTML 500 is therefore **incorrect** — the envelope is deployed and working; it simply cannot catch middleware-level exceptions.
- `/policies/current/` is exempt from the middleware yet still 500s, because its view queries the same unmigrated tables. Independent confirmation of the same cause.
- Redis is **not** implicated: `active_maintenance()` already swallows cache errors.

## 3. Why migrations were never applied

`docker/entrypoint.sh` intentionally does not migrate, so replicas never race on schema
changes. `migrate` exists only in `docker/release.sh`, which is profile-gated in
`docker-compose.coolify.yml` and must be invoked explicitly per deploy. That invocation
was never wired into Coolify, so no deploy has ever migrated this database.

## 4. Fixes

**Operational (restores production, must be done by a release owner).** Wire and run the
release job, then re-probe. See `docs/DEPLOYMENT_AND_OPERATIONS.md`, "Wiring the release
job in Coolify". Verification: `showmigrations --plan | grep '\[ \]'` prints nothing, and
`/health/ready/` returns 200 `READY`.

**Code (prevents recurrence, in this commit).** Lifecycle lookups in the middleware now
fail open on `DatabaseError` and log `product_lifecycle_lookup_failed`. A schema or
database fault can no longer convert into a total API outage; readiness still fails closed,
so a broken revision is still kept out of the load balancer. Regression test:
`test_lifecycle_lookup_database_failure_keeps_api_serving`.

## 5. Deployment pipeline finding

The `Production Acceptance Gate` workflow has **never passed since 2026-07-27**. Every run
failed in `Set up job` within ~3 seconds because `aquasecurity/trivy-action@0.33.1` is not
a resolvable tag (the release is `v0.33.1`). No user step ever executed.

Consequences: `main` is frozen at `6814a3c` while the validated candidate `dbee14e` sits
unpublished; every Dependabot PR was blocked by the same failing required check, so six
weeks of security updates never merged. `pip-audit` was clean on 2026-08-15 and now
reports 15 known vulnerabilities across `django`, `djangorestframework`, `pypdf` and
`sqlparse` — a direct consequence, not an independent regression.

The tag is corrected in `ci.yml` and `release-image.yml` in this commit. Dependency
updates should be allowed to flow through Dependabot once CI is green again rather than
hand-editing the lock files.

## 6. Local verification of this commit

| Gate | Result |
| --- | --- |
| `manage.py check` | PASS — 0 issues |
| `makemigrations --check --dry-run` | PASS — no changes |
| `migrate` | PASS — all 19 apps |
| pytest | PASS — 139 passed, 1 skipped (real Redis URL not configured) |
| Coverage gate | PASS — 85%+ with `--cov-fail-under=85` |
| ruff check / format | PASS — 238 files |
| mypy | PASS — 246 files, 0 issues |
| bandit (medium+) | PASS |
| OpenAPI validate / drift | PASS — 272/272 operations |

## 7. Remaining external gates

1. Authorize publishing `dbee14e` plus this commit to protected `main`.
2. Wire the release job in Coolify, deploy, and confirm migrations applied.
3. Re-probe the full endpoint table in section 1; every row must be non-500.
4. Correlate `product_lifecycle_lookup_failed` and `health_*` log events to confirm no
   remaining dependency is failing.
5. Only then resume dashboard and mobile integration verification.
