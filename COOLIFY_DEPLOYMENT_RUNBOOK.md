# Coolify deployment runbook

## Preconditions

- A green CI run for the exact Git SHA, including dependency, image and config scans.
- An immutable `ghcr.io/<owner>/<repo>:<sha>` image and its SBOM/provenance artifact.
- A completed Staging backup/restore drill and a reviewed change window.
- All required values in `docs/operations/COOLIFY_ENVIRONMENT_MATRIX.md` stored in Coolify. No secrets are passed as build arguments.

This repository currently has no evidence of a successful remote Coolify deployment. Follow this runbook in Staging before production.

## Configure the service

1. Point Coolify at `docker-compose.coolify.yml`, choose the release image SHA as `IMAGE_TAG`, and set `RELEASE_VERSION` and UTC `BUILD_DATE`.
2. Choose one data mode:
   - Managed: provide provider `DATABASE_URL` and `REDIS_URL`; do not enable `self-hosted`.
   - Self-hosted: set `COMPOSE_PROFILES=self-hosted`, use `postgres` and `redis` internal DNS names in those URLs, and set the three `POSTGRES_*` values. Neither service exposes a host port.
3. Configure the Coolify HTTP health path as `/api/v1/health/ready/`. Admission requires HTTP 200. The response must carry `code=READY`; verify this in the post-deploy smoke test because Coolify versions may only assert HTTP status.
4. Keep Docker's own health check unchanged: it calls `/api/v1/health/live/` and validates the JSON `LIVE` payload. A 404 cannot pass either check.
5. Configure private R2/S3 policy before deployment: public access disabled, least-privilege service key, restricted CORS origins, and lifecycle rules for temporary objects.

## Release sequence

1. Scale web replicas to zero or ensure Coolify does not route traffic yet.
2. Run exactly one pre-deployment release command: `sh /app/docker/release.sh`. It runs `check --deploy`, migrations, static collection, OpenAPI validation, and only explicitly enabled idempotent seed commands.
3. If it fails, stop. Capture the log, retain the prior image SHA, and use the failed-migration procedure below. Do not start another release replica.
4. Start web, worker, and beat from the same `IMAGE_TAG`. Confirm worker `inspect ping`, beat logs, and one synthetic idempotent task exactly once.
5. Route traffic only after readiness is 200 and `SMOKE_BASE_URL=https://<staging-host> SMOKE_BEARER_TOKEN=<synthetic-token> python scripts/smoke_test.py` succeeds.

## Normal rollback

1. Stop traffic and select the previous immutable `IMAGE_TAG` in Coolify.
2. Confirm the current migration is backward-compatible. Use expand/migrate/contract: deploy additive schema first, migrate data, and remove old fields only in a later release.
3. Deploy the old image, verify live/ready/auth smoke tests, then restore traffic.
4. If schema/data are not backward-compatible, do not image-roll back; use the failed-migration and restore runbooks.

## Failed migration

1. Leave web traffic disabled and preserve the migration error plus release SHA.
2. Determine whether the migration was atomic and whether application code has run against it.
3. Prefer a forward corrective migration. Restore only to Staging first and only after explicit incident approval.
4. Record the decision, affected migration, backup ID, and post-recovery smoke output.
