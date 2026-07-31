# Final production release certification

Date: 2026-07-30  
Candidate: current uncommitted workspace; no immutable image digest exists

| Gate | Status | Evidence | Command/Environment | Notes |
| --- | --- | --- | --- | --- |
| Coverage >= 85% | PASS | `COVERAGE_REPORT.md`, `coverage.xml`, `htmlcov/` | `coverage run -m pytest -q && coverage report --fail-under=85` | 76 passed; 85.54% |
| Docker build | BLOCKED | `DOCKER_BUILD_EVIDENCE.md` | local Docker Desktop | daemon unavailable |
| Image scan | BLOCKED | no image artifact | local/CI | Trivy not run against an image |
| SBOM | BLOCKED | no image artifact | local/CI | no SPDX/CycloneDX image SBOM generated |
| Runtime containers | BLOCKED | `RUNTIME_CONTAINER_VALIDATION.md` | Docker Compose | services were not started |
| Staging deployment | BLOCKED | `STAGING_DEPLOYMENT_EVIDENCE.md` | Coolify Staging | no URL, digest, or deployment access |
| DAST | BLOCKED | `DAST_SECURITY_REPORT.md` | isolated Staging | no ZAP/API security artifact |
| Load test | BLOCKED | `LOAD_TEST_REPORT.md` | k6/Locust Staging | no executable/target/result |
| Backup restore | BLOCKED | `BACKUP_RESTORE_EXECUTION_REPORT.md` | PostgreSQL/local-media Staging | runbook/scripts are not execution evidence |
| Rollback | BLOCKED | `ROLLBACK_EXECUTION_REPORT.md` | Coolify Staging | no preceding/current digest exercise |
| Smoke tests | BLOCKED | `SMOKE_TEST_REPORT.md` | HTTPS Staging | no synthetic target/credential |
| Ruff | PASS | command output | `ruff check app` | zero errors |
| Bandit Medium/High | PASS | command output | `bandit -q -r app -ll -x migrations/tests` | zero Medium/High findings |
| Dependency audit | PASS | command output | `pip-audit -r requirements.lock --cache-dir C:\\tmp\\pip-audit` | no known vulnerabilities |
| Django checks/migration drift/OpenAPI | PASS | command output | Django testing/production synthetic settings | no drift or schema warning |
| Mypy critical modules | FAIL | command output | Django/DRF stubs, 102 source files | 54 errors in 27 files |
| Gitleaks history | BLOCKED | executable unavailable; CI unexecuted | local/remote CI | no current/history result |
| Critical/High vulnerabilities | UNKNOWN | dependency audit is clean; image/DAST unrun | all required scopes | cannot certify zero without image and DAST scans |
| Unresolved P0 issues | 0 confirmed | `FINAL_RELEASE_GAP_ANALYSIS.md` | local audit only | scope is incomplete; this is not a P0-clear certification |
| Blocked gates | 10 | rows above | release gates | Docker, image scan, SBOM, runtime, Staging, DAST, load, restore, rollback, smoke |

## Release decision

**RELEASE DECISION: BLOCKED**

The release cannot be approved because multiple mandatory gates are blocked, Mypy currently fails, and the absence of image/DAST results means Critical/High vulnerability count cannot be certified as zero. To close the decision, resolve the 54 type errors, make Docker available, execute CI and image scanning/SBOM, then run the documented Staging deployment, smoke, DAST, load, encrypted restore, and rollback exercises with redacted evidence.

Post-certification local hygiene check: `.env` and `media/` were removed from the workspace; a final `pytest -q`, Django check, and migration-drift check passed with 76 tests.
