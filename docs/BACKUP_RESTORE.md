# Backup and Restore

Backups must be configured and restore-tested before a real student launch.

## PostgreSQL Backup

Use Coolify resource backups if PostgreSQL is managed by Coolify. For manual backups, run from a trusted operator machine or one-off container:

```bash
pg_dump "$DATABASE_URL" --format=custom --file=panorama-YYYYMMDD.dump
```

Store backups encrypted outside the application server. Keep at least daily backups for production.

## PostgreSQL Restore

Restore into a clean database or a verified maintenance window:

```bash
pg_restore --clean --if-exists --dbname "$DATABASE_URL" panorama-YYYYMMDD.dump
```

After restore:

```bash
python app/manage.py migrate --noinput
python app/manage.py check --deploy --settings config.settings.production
```

## Media Volume Backup

The production MVP stores uploads in `/app/media`. Back up the named media volume together with the database snapshot.

Example placeholder:

```bash
tar -czf panorama-media-YYYYMMDD.tar.gz /path/to/coolify/media-volume
```

Do not publish media archives. They may contain student cards, print uploads, and private file resources.

## Media Restore

Stop writes first, then restore files to the mounted media volume:

```bash
tar -xzf panorama-media-YYYYMMDD.tar.gz -C /path/to/coolify/media-volume
```

Ensure ownership allows the container user to read and write the restored files.

## Redis Persistence

Redis is used for cache, throttling, Channels, and Celery messaging. It is not the source of truth. Persisting Redis is optional for MVP, but losing it may drop transient websocket/session-like runtime state and queued Celery messages.

## Audit Logs

Audit logs live in PostgreSQL. Retention policy should be chosen before launch and aligned with local privacy requirements.

## Schedule

- Daily PostgreSQL backup.
- Daily media volume backup.
- Weekly restore drill to a non-production environment.
- Backup before destructive migrations, bulk imports, or manual data repair.

## Before Destructive Migrations

1. Stop Celery beat and worker.
2. Create PostgreSQL and media backups.
3. Verify backup files exist and are non-empty.
4. Run migration on staging first.
5. Keep rollback image tag available.

## Emergency Rollback Checklist

1. Stop new deploy rollout.
2. Capture logs and failing request IDs.
3. Revert to the previous image tag.
4. Restore database only if the migration or data write is not backward-compatible.
5. Restore media only if files were deleted or corrupted.
6. Run readiness and smoke tests.
7. Document the incident and recovery time.
