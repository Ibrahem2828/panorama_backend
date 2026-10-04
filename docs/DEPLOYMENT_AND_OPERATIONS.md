# Deployment and operations

Owner: Platform Operations Team  
Last reviewed: 2026-09-19

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
7. Ensure a single Beat service is running so deferred account-deletion work is
   performed exactly once per schedule. Keep `account_deletion_enabled` false
   until the legal retention and support process have been accepted.

Liveness (`/health/live/`) checks only the process. Readiness checks PostgreSQL,
Redis, migrations, critical configuration, and the media mount. Startup is
stricter. SMTP and document-conversion capability never determine liveness.

## Wiring the release job in Coolify (required every deploy)

`docker/entrypoint.sh` deliberately does **not** migrate: the `web` container
only starts ASGI, so replicas never race on schema changes. `migrate` runs
exclusively in `docker/release.sh`. If nothing invokes that script, the database
stays on the previous schema, `/health/ready/` reports pending migrations, and
returns HTTP 503 indefinitely while `/health/live/` keeps returning 200. This is
the single most likely cause of a persistent readiness failure after a deploy
that otherwise looks successful.

Wire it one of these two ways, and never rely on a human remembering it:

**A — Coolify pre-deployment command (preferred).** In the application's
configuration, set the pre-deployment command to `sh /app/docker/release.sh` and
set the pre-deployment container to the `web` service, so it runs inside the
freshly built image with the same environment before new containers take traffic.

**B — explicit one-shot release container.** Run, from the deploy host, before
starting or promoting the new revision:

```sh
docker compose -f docker-compose.coolify.yml --profile release run --rm release
```

Either way the deploy must **fail** if that step fails: `release.sh` runs
`check --deploy`, `validate_production_env`, `migrate`, `collectstatic`, and
OpenAPI validation under `set -eu`, so a non-zero exit is a genuine stop signal.

Coolify's traffic gate and the Docker `HEALTHCHECK` are two different things and
are deliberately configured differently: the compose `HEALTHCHECK` targets
`/health/live/` (a container must not be restarted merely because Postgres blipped),
while Coolify's own HTTP health check must target `GET /api/v1/health/ready/` and
require HTTP 200 with JSON `code` equal to `READY`. Pointing Coolify's check at
`/health/live/` would let a broken revision receive production traffic.

### Verifying a deploy actually migrated

```sh
curl -fsS https://<api-host>/api/v1/health/live/    # expect 200, code=LIVE
curl -fsS https://<api-host>/api/v1/health/ready/   # expect 200, code=READY
docker compose -f docker-compose.coolify.yml exec web python manage.py showmigrations --plan | grep '\[ \]'
```

The third command must print nothing. Any `[ ]` line is an unapplied migration,
which means the release job did not run against this database.

### Readiness triage

`/health/ready/` returns an intentionally opaque body, so diagnose by elimination
in the order the checks execute in `apps/common/health_views.py`:

| Order | Failing check | How to confirm | Fix |
| --- | --- | --- | --- |
| 1 | PostgreSQL | `manage.py dbshell` or `showmigrations` errors | Fix `DATABASE_URL`/network/SSL; check the managed database is running |
| 2 | Redis cache | `redis-cli -u "$REDIS_URL" ping` | Fix `REDIS_URL`; confirm the service is reachable from the app network |
| 3 | Pending migrations | `showmigrations --plan \| grep '\[ \]'` | Run the release job (above) |
| 4 | Critical configuration | `manage.py check --deploy`, `validate_production_env` | Set the missing variable; `FIELD_ENCRYPTION_KEY` must be a valid Fernet key |
| 5 | Media mount | `manage.py storage_status --write-test` | Attach `panorama_media` at `/app/app/media` and make it writable by UID 10001 |

Server logs carry the precise dependency name: search for
`health_dependency_check_failed` (with a `dependency` field) or
`health_pending_migrations`. The HTTP response never exposes which check failed,
by design.

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
