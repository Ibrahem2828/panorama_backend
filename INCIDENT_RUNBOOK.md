# Incident runbook

## First five minutes

1. Assign an incident commander, record time, release SHA, Coolify deployment ID, and user impact.
2. Preserve structured logs, release output, scan artifacts, and relevant provider events. Do not paste credentials, JWTs, user content, or signed URLs into tickets.
3. Stop unsafe rollout activity. Prefer traffic reduction or maintenance mode to an untested rollback.

## Service not ready or 5xx surge

- Compare `/api/v1/health/live/`, `/api/v1/health/ready/`, and `/api/v1/health/startup/`. A live process with failed readiness points to DB, Redis, migration, or configuration.
- Check Coolify logs and database/Redis availability. Do not bypass readiness to admit traffic.
- Roll back only to a migration-compatible immutable SHA; run authenticated smoke checks before restoring traffic.

## Migration failure

- Keep traffic off the candidate release, preserve the error, and identify whether the migration was applied.
- Prefer a forward corrective migration. If restore is considered, follow `BACKUP_RESTORE_RUNBOOK.md` and prove the path on Staging first.

## Redis failure or queue lag

- Confirm broker connectivity, memory eviction/persistence status, worker `inspect ping`, beat logs, and queue depth.
- Pause nonessential task producers if idempotency is uncertain. Restart workers one at a time after Redis recovery and inspect duplicate side effects.

## Object storage outage or public-access suspicion

- Disable uploads and signed-ticket issuance if private storage cannot be verified.
- Check bucket public-access block, policy, CORS, lifecycle, access logs, and service-key scope. Rotate the S3/R2 key if exposure is plausible.
- Do not expose permanent media URLs as a workaround.

## Mail or push outage

- Confirm provider status and credential validity. Rate-limit retries and preserve OTP expiry semantics.
- Do not return OTP values or provider errors to clients. Use an owned test inbox/device for recovery validation.

## Security alert

- For suspected secret leakage, rotate the affected credential using the environment matrix, invalidate sessions when `SECRET_KEY` is involved, and determine whether encrypted data requires re-encryption before changing `FIELD_ENCRYPTION_KEY`.
- For suspected IDOR, JWT replay, SSRF, upload bypass, or pricing tampering, disable the affected route/feature flag if possible, preserve request IDs, and run focused regression tests before re-enabling.
