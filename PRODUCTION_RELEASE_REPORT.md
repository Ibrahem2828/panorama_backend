# Production release report — Phase 2

Date: 2026-07-29  
Candidate: current workspace, no immutable remote image digest published

## Implemented repository controls

- Multi-stage, non-root Docker image uses `requirements.lock` with hashes, OCI metadata arguments, cache-friendly dependency copying, and a liveness JSON health check.
- `docker-compose.coolify.yml` requires immutable `IMAGE_TAG`, runs web/worker/beat by default, has a profile-isolated one-shot release service, applies read-only runtime filesystems, dropped Linux capabilities, no-new-privileges, tmpfs, resource declarations, log rotation, graceful shutdowns, and no published DB/Redis ports.
- Health endpoints are separated: `/api/v1/health/live/`, `/ready/`, and `/startup/`. Readiness checks DB/cache/migrations/configuration; liveness makes no dependency call.
- Production settings reject short `SECRET_KEY`, invalid/missing Fernet keys, non-S3 storage, and missing `S3_BUCKET_PRIVATE` deployment attestation. Private bucket policy still requires provider-side verification.
- CI configurations build/publish Git-SHA images, scan secrets/dependencies/image/config, generate SBOM/provenance, and provide manually triggered Staging ZAP baseline.
- Backup/restore, deployment, incidents, DAST, load, and ASVS release-evidence runbooks are included. `load/k6/panorama.js` uses synthetic-environment variables only.

## Local evidence

| Gate | Command | Result |
| --- | --- | --- |
| Compose interpolation | `docker compose -f docker-compose.coolify.yml config --quiet` with non-secret test values | PASS |
| Django configuration | `python app/manage.py check --settings=config.settings.testing` | PASS |
| Migration drift | `python app/manage.py makemigrations --check --dry-run --settings=config.settings.testing` | PASS |
| OpenAPI validation | `python app/manage.py spectacular --format openapi-json --validate --fail-on-warn ...` | PASS |
| Generated contracts | `python scripts/generate_postman_collection.py` and JSON parse | PASS |
| Unit/integration tests | `pytest -q` | PASS: 70 tests |
| Health negative checks | `pytest app/apps/common/tests_production_hardening.py` | PASS: 12 tests |
| Lint | `ruff check app` | PASS |
| Diff whitespace | `git diff --check` | PASS |
| Coverage gate | `pytest -q --cov=apps --cov-fail-under=85` | FAIL: 79.87%, threshold 85% |
| Docker build/image scan/SBOM | `docker version` | BLOCKED: Docker Desktop Linux daemon unavailable |
| Shell syntax | `sh -n docker/*.sh` | BLOCKED locally: WSL shell access denied; CI executes it |
| Dependency audit | `pip-audit -r requirements.lock` | NOT RUN: prior registry audit timed out; CI gate configured |

## External evidence still required

1. Push candidate and obtain green CI artifact URLs: image scan, config scan, SBOM, and provenance.
2. Deploy immutable SHA to an isolated Staging Coolify environment; run release job once from both an empty DB and an upgraded Staging copy.
3. Verify 200/JSON behavior for health endpoints while intentionally cutting DB and Redis; confirm web, worker, beat, and one synthetic task.
4. Verify actual R2/S3 public-access block, bucket policy, limited CORS, lifecycle rules, private download tickets, and storage outage behavior.
5. Run encrypted backup then Staging restore; attach RPO/RTO evidence.
6. Run k6 scenarios against synthetic data and attach results meeting the stated SLOs.
7. Run ZAP plus authenticated API/WebSocket abuse tests and resolve or formally accept findings.
8. Demonstrate deploy and migration-compatible rollback under the documented expand/migrate/contract policy.

## Release decision

**BLOCKED**

Reasons: Phase 1 coverage gate is failing (79.87% < 85%); no Docker daemon/image artifact is available; CI, Coolify/Staging release, vulnerability scans, DAST, load, private-storage verification, backup restore, and rollback have not executed. No claim is made that there are no vulnerabilities; there are no known Critical/High findings only within checks actually run, which is insufficient for release certification.

## Remediation update — 2026-07-30

- The local coverage gate now passes: 76 tests and 85.54% with `coverage report --fail-under=85`.
- `pip-audit -r requirements.lock --cache-dir C:\\tmp\\pip-audit` now returns `No known vulnerabilities found` after dependency upgrades and hash-lock regeneration.
- `.env` and the ignored local `media/` payload were removed from the workspace by explicit request; secret rotation is still required.
- Docker build remains blocked by the unavailable daemon, Mypy reports 54 errors, and all remote/runtime gates remain blocked. The Phase 2 release decision is unchanged.
