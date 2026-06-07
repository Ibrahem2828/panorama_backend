# Phase 4: Testing, Quality Gates and Observability

## Purpose

Phase 4 strengthens validation, test gates, request observability, and operational documentation before production deployment while preserving the existing mobile and dashboard API contract.

## What Changed

- Added request correlation middleware with `X-Request-ID`.
- Added a production logging filter and formatter that include request IDs.
- Added a safe structured logging redaction helper.
- Expanded `scripts/validate_backend.ps1` with syntax, migration, OpenAPI, focused test, and full test gates.
- Added `scripts/validate_backend.sh` for shell-based CI/container validation.
- Added Phase 4 observability and schema tests.
- Added runbook and observability documentation.

## Intentionally Not Changed

- No documented endpoint path was changed.
- No documented HTTP method was changed.
- No request field was renamed or removed.
- No existing response field was renamed or removed.
- The unified API response envelope was not changed.
- API collection JSON files were not modified.
- No new dependencies were added.

## API Compatibility Guarantee

The only HTTP-level addition is the response header `X-Request-ID`. Existing clients can ignore it. Existing request and response bodies remain unchanged.

## Quality Gates

The primary validation command is:

```powershell
.\scripts\validate_backend.ps1 -DeployCheck
```

Shell equivalent:

```bash
bash scripts/validate_backend.sh --deploy-check
```

These gates validate:

- Python syntax.
- Django system checks.
- Migration dry-run state.
- Production deploy checks.
- API collection JSON files.
- OpenAPI schema generation and validation.
- API contract tests.
- Production hardening tests.
- Phase 2 security tests.
- Phase 3 reliability tests.
- Phase 4 observability tests.
- Full pytest suite.

## Request Correlation

Every response receives an `X-Request-ID` header.

Accepted inbound IDs must match:

```text
^[A-Za-z0-9._:-]{1,128}$
```

Invalid values are replaced with generated UUIDs.

## Safe Logging

The redaction helper recursively redacts sensitive dictionary keys before structured diagnostic data is logged.

Production logs include:

```text
request_id=<value>
```

The code does not log request bodies, OTPs, passwords, tokens, uploaded files, or authorization headers.

## Tests Added

Added `app/apps/common/tests_phase4_observability.py` covering:

- Generated request IDs.
- Preserved valid client request IDs.
- Replacement of invalid request IDs.
- Sensitive structured log redaction.
- Request ID log filter behavior.
- OpenAPI schema generation and validation.

## Validation Commands

Run:

```powershell
.\.venv\Scripts\python.exe app\manage.py makemigrations --check --dry-run
.\scripts\validate_backend.ps1 -DeployCheck
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pytest app\apps\common\tests_api_contract_collections.py
.\.venv\Scripts\python.exe -m pytest app\apps\common\tests_production_hardening.py
.\.venv\Scripts\python.exe -m pytest app\apps\common\tests_phase2_security.py
.\.venv\Scripts\python.exe -m pytest app\apps\common\tests_phase3_reliability.py
.\.venv\Scripts\python.exe -m pytest app\apps\common\tests_phase4_observability.py
.\.venv\Scripts\python.exe app\manage.py spectacular --file $env:TEMP\panorama_openapi.yml --validate --settings config.settings.testing
```

## Known Limitations

- Coverage reporting is not enabled because no coverage dependency is currently configured.
- No new static-analysis dependency was added; syntax validation is dependency-free.
- External APM/error tracking is deferred until the production monitoring provider is selected.
