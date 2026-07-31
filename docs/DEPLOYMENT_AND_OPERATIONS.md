# Deployment and operations

Owner: Platform Operations Team  
Last reviewed: 2026-07-31

## Services

The Coolify compose stack has a one-shot `release` job, Daphne `web`, normal
Celery `worker`, singleton `beat`, and `conversion-worker`. Web uses the small
runtime target; conversion worker uses `conversion-runtime`. PostgreSQL and
Redis may be Coolify-managed or self-hosted profiles, but are not published to
the public internet.

All runtime containers run as `panorama` (UID/GID 10001), use a read-only root
filesystem, drop capabilities, enable `no-new-privileges`, and use a limited
`/tmp` tmpfs. The media named volume and static volume are the permitted durable
writes.

## Coolify deployment

1. Set runtime secrets/variables from `.env.example`; never set them as build arguments.
2. Attach `panorama_media` at `/app/app/media` to web and both workers.
3. Build image tags from immutable Git SHA values. Build the conversion target separately.
4. Run only the release job (`check --deploy`, migrations, collectstatic, schema validation, optional idempotent seed).
5. Start services. Gate traffic on `GET /api/v1/health/ready/` returning HTTP 200 and JSON code `READY`.
6. Run protected asset, lecture-viewer authorization, and smoke checks after deployment.

Liveness (`/health/live/`) checks only the process. Readiness checks PostgreSQL,
Redis, migrations, critical configuration, and the media mount. Startup is
stricter. SMTP and document-conversion capability never determine liveness.

## Rollback

Roll back to the prior immutable web and conversion image references only after
confirming the migration is backward-compatible. Use expand/migrate/contract:
add nullable/additive schema first, deploy readers/writers that tolerate both
forms, backfill separately, and only later remove the old form. Do not reverse a
destructive/data migration during an incident; restore an isolated database if
needed. Validate health, auth, protected files, Celery, WebSocket, and lecture
viewer after rollback.

## Operational checks

```sh
python manage.py check --deploy
python manage.py storage_status --write-test
python manage.py document_pipeline_status
python manage.py showmigrations
```

An unavailable Docker daemon, missing LibreOffice/Poppler in conversion worker,
failed readiness, failed release job, or untested backup restore blocks release.
