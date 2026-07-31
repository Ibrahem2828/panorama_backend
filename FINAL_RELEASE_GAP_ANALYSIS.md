# Final release gap analysis

Date: 2026-07-30  
Scope: current `main` worktree for Panorama Backend  
Decision at analysis time: **RELEASE DECISION: BLOCKED**

## Evidence and repository state

- The requested `PHASE_1_FINAL_CLOSURE_REPORT.md` does not exist. Its available equivalent is `PHASE_1_CLOSURE_REPORT.md`, which explicitly says Phase 1 is not closed.
- Read reports: `PHASE_1_CLOSURE_REPORT.md`, `PRODUCTION_RELEASE_REPORT.md`, `COOLIFY_DEPLOYMENT_RUNBOOK.md`, `BACKUP_RESTORE_RUNBOOK.md`, `SECURITY_ROTATION_REQUIRED.md`, `docs/security/ASVS_L2_MATRIX.md`, and `docs/api/openapi.json`.
- The current tree is dirty with the prior Phase 1/2 candidate changes; no commit SHA/image digest or remote CI run proves those changes are released.
- At audit start, `.env` was untracked and `media/` contained 1,038 untracked files. On 2026-07-30 both exact workspace paths were removed by explicit request; `git ls-files -- .env media` remains empty. Secret rotation remains required because prior local exposure cannot be disproved.
- The repository contains 201 files under `app/` and nine discovered test modules. `pytest -q` completed with **70 passed** on this workspace.

## Actual test and coverage baseline

Command run: `coverage run -m pytest -q` followed by `coverage report`, `coverage xml -o coverage.xml`, and `coverage html -d htmlcov`.

Result: **84.25% total line coverage (5,845/6,938), FAIL against >=85%.** No `pragma: no cover` was added during this run. Existing `pragma: no cover` is present in `apps/common/file_validation.py` and violates the release rule; it must be removed and its import-fallback behavior tested.

| Test modules currently discovered | Coverage-relevant scope |
| --- | --- |
| `app/tests/test_security_contracts.py` | security headers/contracts |
| `app/tests/test_printing_contract.py` | print API contract |
| `app/tests/test_otp_and_login.py` | OTP/login flows |
| `app/tests/test_feedback.py` | feedback service behaviors |
| `app/apps/accounts/tests/test_auth_api.py` | authentication API |
| `app/apps/common/tests_mvp_hardening.py` | base hardening |
| `app/apps/common/tests_phase2.py` | protected resources/RBAC |
| `app/apps/common/tests_phase3.py` | feedback/printing phase behaviors |
| `app/apps/common/tests_production_hardening.py` | production config and health |

### Coverage by application

| Application | Coverage | Status |
| --- | ---: | --- |
| accounts | 86.04% | Below sensitive-target 90% |
| announcements | 85.16% | PASS overall only |
| audit | 96.43% | PASS |
| chat | 80.60% | FAIL sensitive target |
| common | 93.20% | PASS, but crypto remains weak |
| feedback | 74.31% | FAIL sensitive target |
| files | 63.90% | FAIL sensitive target |
| groups | 79.85% | FAIL sensitive target |
| notifications | 77.41% | FAIL |
| printing | 80.39% | FAIL financial target |
| support | 83.29% | FAIL |
| universities | 91.32% | PASS |
| verification | 85.01% | Below sensitive-target 90% |
| config | 95.08% | PASS |

### Lowest-covered production files

| File | Coverage | Risk |
| --- | ---: | --- |
| `apps/files/document_inspection.py` | 21.05% | P1: PDF handling/integrity |
| `apps/common/crypto.py` | 32.14% | P1: field encryption/decryption failure paths |
| `apps/notifications/services.py` | 35.53% | P1: outbound push/SSRF allowlist/error paths |
| `apps/announcements/serializers.py` | 50.00% | P2 |
| `apps/files/serializers.py` | 52.00% | P1: private uploads |
| `apps/accounts/dashboard_serializers.py` | 55.77% | P1: RBAC data exposure |
| `apps/files/services.py` | 56.00% | P1: access tickets/IDOR |
| `apps/feedback/serializers.py` | 56.48% | P1: metrics/spam validation |
| `apps/accounts/dashboard_views.py` | 56.72% | P1: administrative capability boundaries |
| `apps/files/views.py` | 57.29% | P1: protected-file responses |

Additional critical gaps: `apps/chat/consumers.py` and `apps/chat/middleware.py` have no direct WebSocket coverage; `apps/printing/views.py` is 62.71%; `apps/feedback/services.py` is 67.38%; `apps/support/views.py` is 68.61%.

## Gate matrix

| Gate | Status | Actual evidence / reason |
| --- | --- | --- |
| Unit/integration tests | PASS | `pytest -q`: 70 passed |
| Coverage >= 85% | FAIL | 84.25%, `coverage.xml` and `htmlcov/` generated |
| Ruff | PASS | `ruff check app` |
| Bandit Medium/High | PASS | `bandit -q -r app -ll -x migrations/tests` exited 0 |
| Django check (testing) | PASS | `manage.py check --settings=config.settings.testing` |
| Django check --deploy | PASS (synthetic configuration) | production settings check with non-secret valid test values |
| Migration drift | PASS | `makemigrations --check --dry-run` |
| OpenAPI validation | PASS | `spectacular --validate --fail-on-warn` |
| OpenAPI/Postman artifacts | PASS | OpenAPI 3.0.3, 146 paths; generated Postman collection has 265 requests |
| Dependency consistency | PASS | `python -m pip check` |
| pip-audit | FAIL | 33 known vulnerabilities in cryptography 45.0.7, Pillow 11.3.0, and pyOpenSSL 25.3.0 |
| Gitleaks current tree/history | BLOCKED | executable absent locally; remote CI has not run |
| Mypy with Django/DRF stubs | BLOCKED | tool, stubs, and configuration absent |
| Semgrep | BLOCKED | executable/configuration absent |
| Docker Compose configuration | PASS (static only) | previously validated with safe non-secret variables; not runtime evidence |
| Docker build/inspection/run | BLOCKED | Docker Desktop Linux daemon unavailable |
| Image scan / actual SBOM | BLOCKED | no built image or CI artifact exists |
| Runtime containers | BLOCKED | no Docker daemon; release/web/worker/beat not run |
| Staging Coolify deployment | BLOCKED | no Coolify access, URL, image digest, or deployment evidence |
| Smoke suite on Staging | BLOCKED | no Staging target or synthetic credentials |
| DAST | BLOCKED | no isolated Staging target or ZAP artifact |
| Load/soak/WebSocket load | BLOCKED | no k6 executable, Staging target, or result artifact |
| Backup/restore execution | BLOCKED | scripts exist but no controlled PostgreSQL/Staging execution evidence |
| Rollback execution | BLOCKED | no deployed digest or compatible Staging exercise |

## Security and operational findings

### P0: 0 confirmed

No confirmed P0 exploitation has been established in this local assessment. This is not a claim that no P0 exists; DAST, secret-history scanning, and independent review remain unexecuted.

### P1: open

1. **Dependency vulnerabilities:** `pip-audit` reported 33 findings. The source constraints cap cryptography below 46 and Pillow below 12, preventing fixes. pyOpenSSL is an indirect dependency. Update constraints/locks, audit again, and do not use `SECURITY_EXCEPTIONS.md` unless a time-bound false-positive/accepted-risk approval is supplied.
2. **Coverage below gate and weak sensitive areas:** total is below 85%; files, feedback, printing, chat, groups, support, and verification lack the required behavioral depth.
3. **No actual runtime certification:** Docker, Compose runtime, image scans, SBOM, release job, worker, beat, Redis restart, and health-under-dependency-failure are all unexecuted.
4. **No Staging certification:** Coolify, TLS/WSS, private local-media mount and routing policy, smoke, DAST, load, restore, and rollback are not evidenced.
5. **Secrets/media hygiene unresolved:** local `.env` and media payload remain. `SECURITY_ROTATION_REQUIRED.md` requires rotation before deployment. No current/history secret scan result exists.
6. **Supply-chain configuration incomplete:** Docker base images use mutable tags rather than pinned digests; OCI `source` is a placeholder; local SBOM files are absent. CI workflows are configured but unexecuted.
7. **Malware/quarantine lifecycle absent:** ASVS matrix records no actual malware engine. File upload checks are not a substitute for isolated malware scanning/quarantine.
8. **Observability incomplete:** request JSON logs exist, but no verified metrics exporter, task correlation propagation, alert routing, or monitoring backend exists. The WebSocket consumer logs a raw user ID in an error path and needs a privacy-safe identifier.
9. **Testing-rule violation:** existing `# pragma: no cover` in production validation code must be removed rather than relied upon.

## Database, migrations, contracts, asynchronous services, and feedback

- Migrations: drift check passes. Current new migrations are additive (`audit.0003`, `feedback.0003`, `printing.0004`) but neither PostgreSQL fresh-db nor prior-version upgrade has been executed in this environment; expand/contract compatibility is not certified.
- Contracts: schema validation passes and artifacts were generated. No `openapi.yaml`, WebSocket contract document, mobile/dashboard contract suite, or API change log exists yet.
- Celery: worker/beat commands are configured but have not connected to a real Redis broker or executed a task. Beat singleton behavior is only assumed from a one-replica deployment; no distributed singleton lock is implemented or tested.
- WebSocket: Channels/Daphne routing exists but consumer/auth/reconnect/rate-limit behavior is not exercised through an ASGI/WebSocket test client.
- Feedback: CSAT/CES/NPS, policy, spam flags, and optional local triage are implemented. AI triage defaults off and redacts common PII; its failure/circuit, analytics, cooldown/sampling, and human-review behavior require broader tests.
- Backup/restore/rollback: safe scripts and runbooks exist only. There is no execution report, RPO/RTO, or restoration integrity evidence.

## Ordered closure plan

1. Remove the coverage pragma and write behavioral tests for crypto, file validation/inspection/access tickets, notifications SSRF guard, feedback triage/prompt policy, RBAC/dashboard, printing idempotency/concurrency, and ASGI/WebSocket authorization. Re-run coverage until >=85% and create `COVERAGE_REPORT.md`.
2. Upgrade vulnerable dependency constraints, regenerate both hashed locks, run `pip check` and `pip-audit` until zero unacceptable findings. Add `SECURITY_EXCEPTIONS.md` only for approved, expiring exceptions.
3. Add mypy/Django/DRF stubs and strict configuration for critical modules; install/run Gitleaks and Semgrep locally or in executed CI.
4. Pin Docker base images by digest, replace OCI placeholder source, build and inspect a real image, generate SPDX and CycloneDX SBOMs, and run Trivy/Grype.
5. Start Docker and validate release/web/worker/beat/PostgreSQL/Redis runtime, including dependency-failure health behavior and one scheduled task. Record only command output in runtime evidence.
6. Use an isolated Coolify Staging environment with synthetic data, immutable image digest, private storage verification, and HTTPS/WSS. Run smoke, DAST, k6, backup restore, and rollback in that order.
7. Regenerate OpenAPI JSON/YAML/Postman, add contract/WebSocket documentation and `API_CHANGELOG.md`, then issue the final certification only if every gate is actually PASS.

## Remediation update — 2026-07-30

- Behavioral tests were added for Fernet handling, document validation/inspection, and notification provider safety. The executed full-suite gate is now **76 passed** and **85.54%** with `coverage report --fail-under=85`.
- The prohibited production `pragma: no cover` was removed. `rg` found no remaining `pragma: no cover` in `app/`.
- Dependency constraints and locks were upgraded to cryptography 48.0.1, Pillow 12.3.0, and pyOpenSSL 26.2.0. Executed `pip-audit` now reports `No known vulnerabilities found`.
- Mypy was installed with Django/DRF stubs and executed. It reports **54 errors in 27 files**, so the type-check gate is **FAIL**, not configured as a pass.
- Docker build was attempted and remains **BLOCKED** by the unavailable Docker Desktop Linux daemon. All runtime/Staging gates remain blocked.
