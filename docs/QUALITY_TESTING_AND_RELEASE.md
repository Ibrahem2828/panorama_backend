# Quality, testing, and release gates

Owner: Backend Platform Team  
Last reviewed: 2026-07-31  
Applies to: Panorama API v1

## Measured baseline and current result

Before this productization change, the checked-in revision had 14 Django apps,
94 collected tests, lecture conversion/viewer support, local persistent media,
and 288 documented HTTP operations. The present working tree has 15 apps,
105 collected tests, 331 documented HTTP operations, two canonical Postman
collections, and additive mobile-product controls. No existing `/api/v1/` path
or response field was deliberately removed.

| Gate | Current result | Local evidence |
| --- | --- | --- |
| Unit/integration tests | PASS | `coverage run -m pytest -q`: 105 passed |
| Overall coverage | **FAIL** | 87% (9,152 statements, 1,225 missed); release threshold is 90% |
| Ruff lint | PASS | `ruff check .`: all checks passed |
| Ruff formatting | PASS | `ruff format --check .`: all files formatted |
| Django testing check | PASS | `manage.py check --settings=config.settings.testing` |
| Production deploy check | PASS | `manage.py check --deploy` using an ephemeral non-secret validation environment |
| Production environment command | PASS | `manage.py validate_production_env`: passes without printing values |
| Migration drift | PASS | `manage.py makemigrations --check --dry-run --settings=config.settings.testing` |
| Storage write validation | PASS | `storage_status --write-test` with isolated testing media |
| OpenAPI JSON/YAML | PASS | `spectacular --validate --fail-on-warn`; 331 operations |
| Canonical collection coverage | PASS | `validate_api_collections.py`: 331/331 documented operations covered |
| Bandit medium/high | PASS | `bandit -q -r app -ll` |
| Dependency audit | PASS | `pip-audit --disable-pip -r requirements.lock`: no known vulnerabilities |
| Mypy first-party source | **FAIL** | `mypy app`: 82 errors in 41 files, including first-party source and missing third-party stubs |
| Gitleaks local scan | BLOCKED | The executable is not installed in this environment; CI has the Gitleaks action |
| Compose interpolation | PASS | `docker compose -f docker-compose.coolify.yml config --quiet` with ephemeral values |
| Linux Docker image/runtime | BLOCKED | Docker Desktop Linux daemon was unavailable (`dockerDesktopLinuxEngine` pipe missing) |
| PostgreSQL/Redis/Celery/Channels | BLOCKED | No staging runtime was available to this session |
| Conversion worker PDF/DOCX/PPTX | BLOCKED | Local capability command reports LibreOffice and Poppler absent on Windows |
| Persistent-volume restart/redeploy | BLOCKED | Requires a Coolify runtime and named volume |
| Load, backup/restore, rollback, DAST | BLOCKED | Require an isolated staging environment and approved test data |

Coverage XML and HTML are generated locally as `coverage.xml` and `htmlcov/`.
They are artifacts, not source evidence; CI uploads them and does not commit
them. The coverage threshold is intentionally set to 90% in CI and is not
weakened to make this revision appear green.

## Required staging evidence

Before a production approval, capture command output and artifacts for:

1. Immutable web and conversion image build, scan, SBOM, and startup.
2. Release job against an empty database and an upgrade copy.
3. PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)` for representative list/viewer queries.
4. Redis/worker/Beat availability, retries, task idempotency, and DOCX/PPTX conversion.
5. Media-volume file hash after restart and redeploy.
6. Staging smoke, DAST, 50/100-user load, backup restore, and rollback.

A gate without an executed artifact remains **BLOCKED**. A pass from SQLite or
a static configuration file is not a substitute for a matching staging result.

## Current release decision

**PRODUCTION CANDIDATE FOR DASHBOARD AND MOBILE INTEGRATION.** It is not
production-ready because the 90% coverage gate and first-party Mypy gate fail,
and Docker/runtime, conversion, staging, DAST, load, backup/restore, and
rollback evidence is still unavailable. The CI workflow intentionally blocks
merge/release until these gates are resolved.
