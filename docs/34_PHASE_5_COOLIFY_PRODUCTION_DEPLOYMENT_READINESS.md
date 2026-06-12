# Phase 5: Coolify Production Deployment Readiness

## Purpose

Phase 5 prepares the backend for a controlled production deployment on Coolify without changing product behavior or API contracts.

## What Changed

- Hardened the production Dockerfile.
- Added `.dockerignore`.
- Improved the production entrypoint.
- Added `docker-compose.coolify.yml`.
- Added a standard Celery app and OTP cleanup task.
- Added production environment, Coolify, backup/restore, smoke-test, and security docs.
- Added Phase 5 deployment readiness tests.
- Updated README deployment links.

## Intentionally Not Changed

- No API paths, methods, serializers, request fields, response fields, status names, enum values, or API collection files were changed.
- Local development compose remains available.
- Object storage was documented but not implemented.
- External monitoring/Sentry/email providers were not added.

## API Compatibility Guarantee

Existing mobile and dashboard clients can continue using the same endpoints and payloads. Phase 5 changes deployment/runtime files and adds background-task readiness only.

## Dockerfile Summary

The Dockerfile now:

- Uses `python:3.12-slim`.
- Installs production requirements only.
- Runs as a non-root `panorama` user.
- Creates static and media runtime directories.
- Uses Daphne ASGI for HTTP and WebSockets.
- Adds a readiness healthcheck.
- Uses the production entrypoint.

## Dockerignore Summary

`.dockerignore` excludes secrets, `.env` files, media, static output, logs, caches, local databases, IDE folders, archives, and temporary files.

## Entrypoint Summary

The entrypoint:

- Fails fast.
- Optionally waits for database and Redis.
- Optionally runs deploy check, migrations, collectstatic, admin bootstrap, and seed data.
- Starts the provided command.
- Does not echo secrets.

## ASGI and WebSocket Runtime

Production uses:

```bash
daphne -b 0.0.0.0 -p ${PORT:-8000} config.asgi:application
```

`config.asgi` keeps HTTP and WebSocket routing through Channels. WebSocket paths are unchanged.

## Coolify Compose Summary

`docker-compose.coolify.yml` defines:

- `web`
- `postgres`
- `redis`
- optional profiled `worker`
- optional profiled `beat`

PostgreSQL and Redis are not publicly exposed.

## Environment Variable Contract

See `docs/ENVIRONMENT_PRODUCTION.md`.

## Static Strategy

WhiteNoise serves collected static files. `collectstatic --noinput` runs when `RUN_COLLECTSTATIC=True`.

## Media Strategy

MVP production uses a persistent `/app/media` volume. Private media must not be exposed directly; protected backend endpoints remain the controlled access path.

## Health and Readiness

Container and Coolify checks should use `/api/v1/health/ready/`. The check is unauthenticated and verifies database and cache readiness.

## Celery Worker and Beat Readiness

A Celery app is configured. Worker and beat can run from the same image. Beat schedules the existing expired OTP cleanup behavior through a Celery task.

## Release Command Strategy

Single-instance MVP can use entrypoint flags:

- `RUN_DEPLOY_CHECK=True`
- `RUN_MIGRATIONS=True`
- `RUN_COLLECTSTATIC=True`

Multi-instance production should run migrations as a one-off release command and set `RUN_MIGRATIONS=False` on replicas.

## Backup and Restore Strategy

See `docs/BACKUP_RESTORE.md`.

## Smoke Test Checklist

See `docs/PRODUCTION_SMOKE_TESTS.md`.

## Security Checklist

See `docs/PRODUCTION_SECURITY_CHECKLIST.md`.

## Validation Commands

Run:

```powershell
.\.venv\Scripts\python.exe app\manage.py makemigrations --check --dry-run
.\scripts\validate_backend.ps1 -DeployCheck
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pytest app\apps\common\tests_phase5_deployment.py
```

Also run the Phase 1-4 focused suites before release.

## Known Limitations

- Docker build was not run unless Docker is available locally.
- Bash validation cannot run on Windows without Bash installed.
- S3-compatible media storage is documented as a future migration path.
- External APM/error tracking is not configured.

## Remaining Operational Risks

- Real Coolify routing, HTTPS, and WSS must be verified in the target environment.
- Backup restore drills must be completed before launch.
- Multi-instance migration workflow requires an operational release job.
- Media object storage remains future work.

## Final Readiness Assessment

The backend is ready for a controlled Coolify production deployment when the validation commands pass, environment variables are set in Coolify, PostgreSQL/Redis/media backups are configured, and the smoke/security checklists are completed.
