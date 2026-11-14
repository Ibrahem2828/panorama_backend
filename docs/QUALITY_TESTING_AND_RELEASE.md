# Quality, testing, and release gates

Owner: Backend Platform Team
Last reviewed: 2026-08-14
Applies to: Panorama API v1

## Local evidence

| Gate | Result | Evidence |
| --- | --- | --- |
| Unit/integration tests | PASS | `pytest -q`: 138 passed, 1 Redis-marked test skipped locally |
| WebSocket auth regression | PASS | 13 passed, 1 Redis-marked concurrency test skipped locally; CI supplies disposable Redis |
| Ruff lint / format | PASS | `ruff check .`; `ruff format --check .` |
| Django testing check | PASS | `manage.py check --settings=config.settings.testing` |
| Production settings smoke | PASS | `manage.py check --deploy --settings=config.settings.production` with ephemeral values |
| Migration drift | PASS | `manage.py makemigrations --check --dry-run --settings=config.settings.testing` |
| OpenAPI JSON/YAML | PASS | `python scripts/openapi_contract.py validate`; 272 operations |
| OpenAPI drift | PASS | `python scripts/openapi_contract.py check-drift` |
| Canonical collection coverage | PASS | `validate_api_collections.py`: 272/272 operations |
| Mypy | PASS | 0 errors in 246 source files |
| Bandit medium/high | PASS | No findings reported |
| Dependency installation state | PASS | `pip check`: no broken requirements |
| Dependency advisory audit | PASS | `pip-audit -r requirements.lock`: no known vulnerabilities found |
| Gitleaks local scan | NOT VERIFIED | Local executable unavailable; CI contains the Gitleaks action |
| Docker build/runtime | NOT VERIFIED | Docker Desktop Linux daemon unavailable |
| PostgreSQL/Redis/Celery/Channels | NOT VERIFIED | No staging runtime was available |
| DAST, load, backup/restore, rollback | NOT VERIFIED | Require an isolated staging environment and approved test data |

## Contract workflow

`docs/api/openapi.json` and `docs/api/openapi.yaml` are the canonical API
artifacts. Use the following commands:

```text
python scripts/openapi_contract.py generate
python scripts/openapi_contract.py validate
python scripts/openapi_contract.py check-drift
```

Generation also refreshes the two Postman collections and the API coverage
matrix. CI validates schema generation and fails on contract drift.

## Required staging evidence

Before production approval, retain evidence for:

1. Immutable web and conversion-image build, scan, SBOM, and startup.
2. PostgreSQL/Redis/Channels/Celery health and failure behaviour.
3. Protected-file and WebSocket handshake tests against shared Redis.
4. Staging smoke, DAST, 50/100-user load, backup restore, and rollback.
5. Persistent-volume behaviour across restart and redeploy.

## Current release decision

**BACKEND CLOSED — READY FOR DASHBOARD INTEGRATION.** This is not a production
approval: the external gates above remain required before any production
promotion.

## Accepted dependency advisories (time-boxed)

Twisted 26.4.0 has seven advisories whose fix exists only in 26.10.0rc1 (no release candidate goes into production
locks): GHSA-vpp8-g58w-m8mp, CVE-2026-115778, CVE-2026-115774, CVE-2026-115775, CVE-2026-115776, CVE-2026-115877,
CVE-2026-83813. They are ignored by id in the CI `pip-audit` step (plus the older GHSA-8pqf-f4m5-798g), so any new
vulnerability still fails the gate.

- Not exposed: telnet/conch, IMAP4, words (XMPP, IRC) and names (DNS) are never used; Panorama runs Twisted only through
  daphne (HTTP/1.1 and WebSocket); the `h2` package is not installed, so HTTP/2 rapid reset does not apply.
- Possibly exposed: CVE-2026-115877 (HTTP request smuggling through a bare CR or LF in a header value) depends on the
  reverse proxy rejecting such headers. This has not been verified.
- Fix version: Twisted 26.10.0 final. Review date: 2026-12-15 (or when 26.10.0 is released); then bump the lock and remove
  the ignores.

