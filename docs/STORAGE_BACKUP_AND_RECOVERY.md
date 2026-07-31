# Storage, backup, and recovery

Owner: Platform Operations Team  
Last reviewed: 2026-07-31

## Current storage

Production uses `STORAGE_BACKEND=local`, `MEDIA_ROOT=/app/app/media`, and
`MEDIA_URL=/media/`. In Coolify add **Application → Storages → Add Persistent
Storage → Volume** named `panorama_media` at `/app/app/media`. Set these as
runtime variables only; no bucket, provider token, or S3 endpoint is required.
The `panorama` user (UID/GID 10001) must be able to write the mount:

```sh
id
ls -ld /app/app/media
touch /app/app/media/.write-test
rm /app/app/media/.write-test
python manage.py storage_status --write-test
```

Mount the same volume for web, normal Celery worker, beat where needed, and the
conversion worker. Never copy media into an image, delete the volume during
deployment, expose `/media/`, or run `docker system prune --volumes`.

## Backup and restore

PostgreSQL references files but does not store their content. Back up PostgreSQL
and the media volume as one consistency point: pause writes or use a consistent
snapshot, make a checksum manifest and encrypted archive, copy it to a separate
access-controlled location, then record retention and expiry. The repository
scripts require an explicit volume argument and never remove source data.

Restore to an isolated environment first. Verify database object counts, media
file count/size/checksum, representative protected streams, and application
health before declaring recovery successful. Measure and retain actual RPO/RTO;
a written runbook is not evidence of a restore test.

## Future generic S3 migration

Do not change model fields or API paths. Freeze writes, back up PostgreSQL and
the volume, create a private generic S3-compatible bucket with least-privilege
credentials, dry-run a key-preserving transfer, compare counts/sizes/checksums,
test authorized reads, switch `STORAGE_BACKEND=s3`, and retain the original
volume until the rollback window closes. No provider is the default.
