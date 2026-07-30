# Backup and restore runbook

## Policy

- Run an encrypted PostgreSQL backup daily and before every release. Keep daily backups 35 days, weekly backups 12 weeks, and monthly backups 12 months, subject to approved retention requirements.
- Store backups outside the Coolify host in an encrypted, private bucket with separate backup credentials. `scripts/backup_postgres.sh` uses an `age` recipient key and produces a checksum.
- Redis uses AOF (`appendfsync everysec`) in self-hosted mode. It is a cache/broker: short-lived cache data can be lost, but Celery tasks rely on retry/idempotency. Do not treat Redis as the system of record.
- Restore to an isolated Staging database at least monthly and before production promotion. A backup is not accepted until restore plus smoke checks are recorded.

## Create backup

From a hardened backup runner with `pg_dump`, `age`, and `sha256sum` installed:

```sh
DATABASE_URL='...' BACKUP_DIR=/secure/backups AGE_RECIPIENT='age1...' sh scripts/backup_postgres.sh
```

Upload both `.dump.age` and `.sha256`, verify the checksum after upload, and record the backup ID, source release SHA, retention expiry, operator, and result in the change record. Never retain the unencrypted temporary dump; the script removes it on exit.

## Staging restore drill

1. Create an isolated Staging database and disable outbound email, push, and external webhooks.
2. Verify the encrypted artifact checksum, then run:

```sh
DATABASE_URL='...' AGE_IDENTITY_FILE=/secure/age-key.txt BACKUP_FILE=/secure/panorama.dump.age \
RESTORE_ENVIRONMENT=staging RESTORE_CONFIRMATION=RESTORE_STAGING sh scripts/restore_postgres_staging.sh
```

3. Run migrations only if the selected Staging application version requires them, then run `check --deploy`, readiness/startup endpoints, and `scripts/smoke_test.py` with a synthetic account.
4. Record duration, RPO timestamp, RTO, row-count checks, and failures. Do not promote based on an unrecorded drill.

## Production recovery

Production restore requires an incident commander, an approved maintenance window, a known-good backup, and a tested Staging restore. The provided restore script intentionally refuses production. Use a provider-controlled restore process only after capturing the current database and documenting the reason a forward migration is not feasible.
