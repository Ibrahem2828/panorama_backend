# Phase 1 Closure Report

Status: **NOT CLOSED / NOT PRODUCTION-APPROVED**

This report records commands actually run in the local Windows workspace on
2026-07-29. A configured CI step or an unexecuted staging procedure is not
reported as a pass.

## Delivered changes

- Added production fail-fast configuration for database, Redis, CSRF/CORS,
  email credentials, field encryption, and mandatory private S3/R2-compatible
  storage.
- Added `/api/v1/health/ready/`, a dependency-aware readiness endpoint, and
  corrected the Coolify healthcheck to require a 2xx response. Removed dead
  Compose maintenance flags and enabled worker/beat without an opt-in profile.
- Added secret rotation instructions, ignored secret locations, and CI gates
  for gitleaks, schema generation, Bandit, dependency audit, coverage, and a
  Docker build.
- Generated `docs/api/openapi.json` (144 paths) and
  `docs/api/postman_collection.json` (263 requests) from the schema.
- Extended feedback with CSAT/CES/NPS/free-text metrics, prompt governance,
  duplicate fingerprints, privacy requests, safe optional triage, analytics,
  tests, and `feedback.0003` migration.
- Added print-order idempotency and a server-calculated pricing revision with
  `printing.0004` migration. Added audit action migration `audit.0003`.
- Removed committed default seed/bootstrap passwords. Administrator passwords
  now come only from deployment environment secrets.
- Redirected testing uploads to the system temporary directory so tests no
  longer add fixture files to repository `media/`.

## Main files

Core runtime changes are in `app/config/settings/production.py`,
`app/apps/common/health_views.py`, `docker-compose.coolify.yml`,
`app/apps/feedback/`, and `app/apps/printing/`. Generated contracts and
security evidence are in `docs/api/` and `docs/security/ASVS_L2_MATRIX.md`.
The complete changed-file inventory is the current `git diff --name-only`.

## Actual acceptance matrix

| Gate | Result | Evidence |
| --- | --- | --- |
| Django check (testing) | PASS | `python app/manage.py check --settings=config.settings.testing` |
| Migration drift | PASS | `makemigrations --check --dry-run` after generating three migrations |
| Fresh SQLite migration | PASS | `python app/manage.py migrate --noinput --settings=config.settings.testing` |
| OpenAPI validation | PASS | `spectacular --format openapi-json --validate --fail-on-warn` |
| Generated Postman collection | PASS | JSON parsed; 144 schema paths and 263 generated requests |
| Test suite | PASS | 67 tests passed |
| Coverage gate (85%) | FAIL | 80.07%; `pytest --cov-fail-under=85` correctly exits non-zero |
| Ruff expanded rules | PASS | `ruff check app` |
| Bandit medium/high | PASS | `bandit ... --severity-level medium`; low-severity test/password-name false positives remain excluded from this gate |
| pip-audit | FAIL | External audit timed out after 124 seconds; no result available |
| Shell syntax | FAIL | WSL shell could not start (`CreateInstance/E_ACCESSDENIED`) |
| Type check (mypy/pyright) | FAIL | Neither checker is installed/configured |
| Local gitleaks execution | FAIL | gitleaks is configured in CI but unavailable locally |
| Docker build | FAIL | Docker Desktop Linux daemon was unavailable |
| No tracked `.env` or `media/` | PASS | `git ls-files -- .env media` returned no files |
| No local `.env` or `media/` payload | FAIL | Both still exist locally (the media directory contains 1,038 files); removal was blocked pending explicit owner approval |

## Remaining release blockers

1. Explicitly approve removal of the local `.env` and 1,038-file `media/`
   directory, then rotate all potentially exposed secrets listed in
   `SECURITY_ROTATION_REQUIRED.md`.
2. Raise total coverage from 80.07% to at least 85%, with targeted tests for
   WebSockets, file quarantine/access, feedback services/tasks, notifications,
   and support services.
3. Run pip-audit, gitleaks, shell syntax, type checks, and Docker build in an
   environment where the necessary services/tools are available.
4. Implement and exercise a real malware scanner/quarantine lifecycle,
   expiring signed quote token validation, PostgreSQL race tests, backup/restore,
   staging smoke tests, load tests, and an independent security review.

Until every blocker is resolved and the CI/staging gates pass, this repository
must not be presented as a final production release.
