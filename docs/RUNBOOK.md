# Panorama Backend Runbook

## Purpose

This runbook covers the operational checks needed before deploying the Panorama backend to Coolify or another production runtime.

## Pre-Deploy Validation

Run from the repository root:

```powershell
.\scripts\validate_backend.ps1 -DeployCheck
```

Linux/macOS or container shell:

```bash
bash scripts/validate_backend.sh --deploy-check
```

The validation entry points run:

- Python syntax checks.
- Django system checks.
- Migration dry-run checks.
- Django deploy checks with production settings.
- API collection JSON validation.
- OpenAPI schema generation and validation.
- Focused API contract, production hardening, Phase 2, Phase 3, and Phase 4 tests.
- Full pytest suite.

## Required Production Environment

Production settings require:

- `SECRET_KEY`
- `ALLOWED_HOSTS` or `DJANGO_ALLOWED_HOSTS`
- `CSRF_TRUSTED_ORIGINS` or `DJANGO_CSRF_TRUSTED_ORIGINS`
- `CORS_ALLOWED_ORIGINS` or `DJANGO_CORS_ALLOWED_ORIGINS`
- `DATABASE_URL` or all `DB_*` values
- `REDIS_URL`

Use explicit hostnames. Do not use `*` for `ALLOWED_HOSTS`.

## Health Checks

Use these unauthenticated endpoints:

- Liveness: `/api/v1/health/`
- Database check: `/api/v1/health/db/`
- Readiness: `/api/v1/health/ready/`

Use readiness for deployment health checks because it verifies database and cache availability.

## Request Correlation

Every response includes `X-Request-ID`. Clients may send a valid `X-Request-ID`; otherwise the backend generates one.

Production logs include `request_id=<value>` so a failing client request can be matched to server logs.

## Incident Triage

1. Check `/api/v1/health/ready/`.
2. Search logs by `request_id` from the client response header.
3. Confirm database and Redis service status.
4. Run `python app/manage.py check --deploy --settings config.settings.production` with the deployed environment.
5. Verify that migrations have been applied.

## Scheduled Maintenance

Expired or used OTP records can be cleaned with:

```powershell
.\.venv\Scripts\python.exe app\manage.py cleanup_expired_otp --retention-days 1
```

This command is idempotent and can be run by a future scheduled worker or platform cron.

## Known Operational Gaps

- External APM/error tracking is not configured.
- Slow-query monitoring depends on the production database/runtime tooling.
- Full Celery worker and beat scheduling remain future work.
- Object storage and signed media delivery remain future work.
