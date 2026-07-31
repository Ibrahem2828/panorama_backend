# Quality, testing, and release gates

Owner: Backend Platform Team  
Last reviewed: 2026-07-31  
Applies to: Panorama Backend API v2

## Baseline before the lecture-platform work

This is a measured local baseline, not a production certification. The repository
contained 13 local Django applications, 40 local models, 146 OpenAPI paths, and
265 documented API operations. It had no lectures application, lecture-viewer
route, conversion worker, or lecture-note model; the existing `files` application
only supplied protected generic files.

| Gate or measure | Result | Evidence / limitation |
| --- | --- | --- |
| Test suite | PASS | `.venv\\Scripts\\python.exe -m pytest -q`: 83 passed |
| Coverage | PASS | `coverage run -m pytest -q; coverage report`: 86% (7,262 statements; 1,026 missed) |
| Django checks | Previously PASS | Re-run after every schema or settings change |
| Migration drift | Previously PASS | Re-run after every schema change |
| Ruff lint | FAIL | `ruff check .`: two import-order errors in scripts |
| Ruff formatting | FAIL | `ruff format --check .`: 57 legacy files require formatting |
| Mypy critical scope | FAIL | 54 errors in 27 files; no new lecture code exists at this baseline |
| Docker/Linux runtime | BLOCKED | Docker Desktop Linux engine is unavailable on this workstation |
| PostgreSQL EXPLAIN / DB latency | BLOCKED | No isolated staging PostgreSQL data set was provided locally |
| Redis/Celery runtime | BLOCKED | No running Redis/Celery service was provided locally |
| Load test | BLOCKED | No staging URL, credentials, or capacity allocation was provided |

The existing local test environment uses SQLite and in-memory cache/channels. It
cannot prove PostgreSQL query plans, Redis behaviour, Celery execution, volume
persistence, Docker image content, or Coolify deployment behaviour.

## Performance baseline policy

Before a production performance claim, run representative data and capture p50,
p95, p99, response size, error rate, database query count/time, cache hit/miss,
CPU, memory, and worker queue depth for login, OTP, profile, subjects, lecture
list/detail, viewer manifest/pages, notes, administrative upload, protected-file
streaming, chat, and support. Store the command, environment resources, dataset
shape, and raw report as CI or staging artifacts; do not replace a benchmark with
a static code review.

## Release gates

Required gates are: unit/integration tests, coverage not below the baseline,
migration-drift check, Django checks, focused Mypy for changed critical code,
Ruff lint/format for changed files, Bandit, dependency audit, OpenAPI validation,
Linux Docker build, container startup, PostgreSQL/Redis/Celery validation,
protected-viewer authorization tests, persistent-volume restart/redeploy test,
and a staging load test. A gate that has not actually run remains **BLOCKED**.

`PRODUCTION READY` is reserved for an environment where every required release
gate has passed. A passing local test suite alone is not a production approval.

## 2026-07-31 implementation evidence

| Gate | Result | Evidence |
| --- | --- | --- |
| Tests | PASS | `coverage run -m pytest -q`: 94 passed |
| Coverage | PASS | 86% (8,140 statements; 1,124 missed), above the 85% gate |
| Migration drift | PASS | `manage.py makemigrations --check --dry-run`: no changes detected |
| Django production checks | PASS | `manage.py check --deploy` with ephemeral non-secret validation environment: no issues |
| OpenAPI | PASS | JSON and YAML regenerated with `spectacular --validate --fail-on-warn` |
| Ruff | PASS | `ruff check .`: all checks passed; every changed Python file passes format check |
| Bandit | PASS | `bandit -q -r app -ll`: no medium/high finding |
| Dependency audit | PASS | `pip-audit --disable-pip -r requirements.lock`: no known vulnerabilities |
| Document capability | PARTIAL | Command correctly reports LibreOffice/Poppler unavailable on this Windows environment; real conversion is not claimed |
| Docker Linux build/runtime | BLOCKED | Docker Desktop Linux daemon was unavailable (`dockerDesktopLinuxEngine` pipe missing) |
| PostgreSQL/Redis/Celery/Coolify/load/restore | BLOCKED | No matching staging runtime was supplied |
| Mypy full critical scope | FAIL | Existing shared-code/stub typing debt remains; this blocks a full type gate |

Release decision at this revision: **PRODUCTION CANDIDATE**. It is not
`PRODUCTION READY` until the BLOCKED and FAIL gates are closed with runtime
evidence.
