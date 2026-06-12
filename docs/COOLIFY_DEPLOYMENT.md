# Coolify Deployment Guide

## Prerequisites

- Coolify project with repository access.
- PostgreSQL resource or the included compose PostgreSQL service.
- Redis resource or the included compose Redis service.
- Backend domain with HTTPS.
- Dashboard/mobile frontend origins known before launch.

## Services

Required:

- `web`: Django ASGI app served by Daphne.
- PostgreSQL: source-of-truth database.
- Redis: cache, throttling, Channels, Celery broker/result backend.

Optional:

- `worker`: Celery worker.
- `beat`: Celery beat scheduler for OTP cleanup.

## Deployment Modes

Use the `Dockerfile` app mode when Coolify provides managed PostgreSQL and Redis resources.

Use `docker-compose.coolify.yml` for an all-in-one stack. The compose file does not publish PostgreSQL or Redis ports.

## Repository Build Setup

- Build pack: Dockerfile or Docker Compose.
- Dockerfile path: `Dockerfile`.
- Compose path: `docker-compose.coolify.yml` for compose mode.
- Exposed application port: `8000`.
- Start command: keep image default unless intentionally overriding.

The default command is:

```bash
daphne -b 0.0.0.0 -p ${PORT:-8000} config.asgi:application
```

This supports HTTP and Django Channels WebSockets.

## Environment Variables

Set variables from `docs/ENVIRONMENT_PRODUCTION.md` in Coolify secrets/environment UI.

Minimum required values:

```env
DJANGO_SETTINGS_MODULE=config.settings.production
SECRET_KEY=replace-in-coolify
DEBUG=False
ALLOWED_HOSTS=api.example.com
CSRF_TRUSTED_ORIGINS=https://api.example.com,https://dashboard.example.com
CORS_ALLOWED_ORIGINS=https://dashboard.example.com
DATABASE_URL=postgres://user:password@host:5432/database
REDIS_URL=redis://host:6379/0
PORT=8000
```

Do not commit these values.

## Domain and HTTPS

1. Attach the backend domain to the `web` service.
2. Enable HTTPS in Coolify.
3. Add the exact backend hostname to `ALLOWED_HOSTS`.
4. Add backend and dashboard HTTPS origins to `CSRF_TRUSTED_ORIGINS`.
5. Add the dashboard HTTPS origin to `CORS_ALLOWED_ORIGINS`.

For WebSockets, clients should use WSS through the same backend domain:

```text
wss://api.example.com/ws/v1/groups/<group_id>/chat/
```

Do not change the WebSocket path.

## Static Files

WhiteNoise serves collected static files from `STATIC_ROOT`.

Keep:

```env
RUN_COLLECTSTATIC=True
```

The Docker image creates `/app/staticfiles`; compose mode mounts `static_data` there.

## Media Files

For MVP, use a persistent volume mounted to `/app/media`.

Do not expose `/media/` directly through the reverse proxy for private uploads. The backend protected file view must remain the access path for private files.

Back up the media volume with the database.

Future high-scale production should move media to S3-compatible object storage with signed/protected access.

## Migration Strategy

Single-instance MVP:

```env
RUN_MIGRATIONS=True
```

The entrypoint runs migrations before starting Daphne.

Multi-instance future:

1. Run migrations as a release command or one-off job.
2. Set `RUN_MIGRATIONS=False` on web replicas.
3. Roll web containers after migrations complete.

## Deploy Check

Keep this enabled for normal web startup:

```env
RUN_DEPLOY_CHECK=True
```

Worker and beat services should set it to `False`.

## Healthcheck

Use:

```text
/api/v1/health/ready/
```

The endpoint is public, fast, and checks database plus cache readiness. Set `HEALTHCHECK_HOST` to the backend hostname if internal health checks fail due `ALLOWED_HOSTS`.

## First Deploy Checklist

1. Set all required environment variables.
2. Create PostgreSQL and Redis resources.
3. Attach persistent media volume.
4. Deploy web service.
5. Confirm migrations and collectstatic completed.
6. Bootstrap admin accounts only if needed.
7. Set `RUN_SETUP_ADMIN_ACCOUNTS=False` after bootstrap.
8. Verify `/api/v1/health/ready/`.
9. Run smoke tests from `docs/PRODUCTION_SMOKE_TESTS.md`.
10. Confirm logs include request IDs and no secrets.

## Worker and Beat

The project includes a Celery app and one scheduled task for expired OTP cleanup.

Compose mode enables worker and beat with:

```bash
docker compose --profile celery -f docker-compose.coolify.yml up
```

In Coolify service mode, create separate services from the same image:

```bash
celery -A config worker --loglevel=INFO
celery -A config beat --loglevel=INFO
```

Set migration/static/admin flags to `False` for worker and beat.

## Backups

Configure PostgreSQL and media backups before launch. See `docs/BACKUP_RESTORE.md`.

## Rollback

1. Keep previous image tag available.
2. Stop worker and beat if data writes are failing.
3. Roll web back to the previous image.
4. Restore database only when needed for non-backward-compatible migrations or bad data writes.
5. Run readiness and smoke tests.

## Common Issues

- `DisallowedHost`: add the exact backend host to `ALLOWED_HOSTS`; set `HEALTHCHECK_HOST` for internal checks.
- CSRF failure: include scheme and host in `CSRF_TRUSTED_ORIGINS`.
- CORS failure: include dashboard origin in `CORS_ALLOWED_ORIGINS`.
- WebSocket failure: verify WSS routing and `REDIS_URL`.
- Static missing: confirm `RUN_COLLECTSTATIC=True`.
- Media missing after deploy: confirm persistent `/app/media` volume.
- Deploy check fails: fix environment values before exposing the service.
