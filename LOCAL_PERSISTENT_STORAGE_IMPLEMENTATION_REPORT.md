# Panorama Backend — Local Persistent Storage Implementation Report

Date: 2026-07-31  
Scope: replace the active provider-specific object-storage requirement with a
private, persistent local Coolify volume. This report records local evidence
only; it is not a production-release certification.

## A. Executive Summary

The active storage mode is now `STORAGE_BACKEND=local`. Django uses a
`PrivateFileSystemStorage` subclass of `FileSystemStorage` at
`/app/app/media` in the container. It randomizes stored filenames, preserves
logical folders, and keeps private downloads behind authenticated, user-bound
access tickets. Static files remain separate under `/app/staticfiles` and keep
the existing WhiteNoise backend.

The old provider-specific startup requirement has been removed. The image was
not built in this workstation because the Docker Linux daemon is unavailable;
the named volume was therefore not mounted on a real Coolify instance.

## B. Root Cause

`app/config/settings/production.py` previously rejected startup unless a legacy
boolean was true, then required provider credentials and an attestation flag.
`docker-compose.coolify.yml` and `.env.example` supplied the same provider
configuration. This made production boot fail before Django could start when a
local persistent volume was the intended storage.

## C. Files Changed

| File | Change |
| --- | --- |
| `.dockerignore` | Excludes `media/`, `*.env`, and `.env*` while allowing `.env.example`. |
| `.env.example` | Adds active local variables and only commented future generic S3 variables. |
| `Dockerfile` | Keeps UID/GID 10001 media directory creation; Docker liveness now validates the live JSON payload. |
| `docker-compose.coolify.yml` | Removes cloud variables, configures local media settings, and mounts `panorama_media` for web/worker/beat. |
| `requirements/base.txt`, `requirements.lock`, `requirements-dev.lock` | Removes unused S3 SDK dependencies and regenerates hash locks with pip-tools. |
| `app/config/settings/storage.py` | New provider-neutral storage-mode parsing, local configuration, legacy compatibility, and future generic-S3 validation. |
| `app/config/settings/base.py`, `testing.py`, `production.py` | Installs a complete `STORAGES` mapping containing `default` and `staticfiles`; production enforces a non-temporary local path. |
| `app/apps/common/storage.py` | Randomized private local object keys with traversal/null-byte rejection. |
| `app/apps/common/health_views.py` | Readiness checks local media exists/is-directory/readable/writable without a write probe; liveness remains dependency-free. |
| `app/apps/common/management/commands/storage_status.py` | Safe storage status and optional write/read/delete probe with cleanup. |
| `app/apps/common/tests_storage_local.py`, `tests_production_hardening.py` | Behavioral tests for local storage, traversal, legacy mode, readiness, and ticket ownership. |
| `app/apps/files/views.py`, `chat/views.py`, `printing/views.py`, `support/views.py`, `verification/views.py` | Stream endpoints now require authentication, ticket ownership, current authorization, safe content disposition, and private response headers. |
| `docs/api/openapi.json`, `docs/api/openapi.yaml`, `docs/api/postman_collection.json` | Regenerated contracts after the protected-endpoint security change. |
| `docs/COOLIFY_LOCAL_MEDIA_STORAGE_AR.md` | Exact Coolify Volume configuration and permission verification. |
| `docs/LOCAL_MEDIA_BACKUP_RESTORE_AR.md` | Coherent PostgreSQL + media-volume backup/restore procedure. |
| `docs/STORAGE_MIGRATION_PLAN_AR.md` | Future generic S3-compatible migration and rollback plan; no migration is executed. |
| `docs/STORAGE_TECHNOLOGY_ALTERNATIVES_AR.md` | Official-source comparison and current/future recommendation. |
| `scripts/backup_local_media.sh`, `scripts/verify_local_media_backup.sh` | Argument-required, non-destructive backup integrity helpers. |
| `README.md`, `DEPLOYMENT_COOLIFY.md`, `COOLIFY_DEPLOYMENT_RUNBOOK.md`, `INCIDENT_RUNBOOK.md` | Active deployment and incident documentation now describes local named-volume storage. |
| `docs/operations/COOLIFY_ENVIRONMENT_MATRIX.md`, `docs/operations/SECURITY_AND_PRODUCTION_OPERATIONS_AR.md`, `docs/security/ASVS_L2_MATRIX.md`, `SECURITY_ROTATION_REQUIRED.md` | Environment, security, rotation, and ASVS guidance aligned to the new mode. |
| Historical evidence reports | Terminology updated so the current operational model is not contradicted. |

No database model or migration was added, changed, reversed, or deleted.

## D. Storage Architecture

```text
Current:  web / worker / beat → Django Storage API → named Volume /app/app/media
Future:   web / worker / beat → same Django Storage API → generic S3 adapter
```

Only `local` is enabled. `s3` is an explicit future mode: it validates all
`S3_*` names together and then stops because no adapter is shipped in this
release. It never selects a provider automatically. A supplied
`STORAGE_BACKEND` takes precedence over the deprecated legacy boolean; legacy
`False` maps to local with one value-free deprecation warning, while legacy
`True` fails with an explicit migration instruction.

## E. Removal of the Previous Provider Coupling

- Removed provider credentials, endpoint, bucket, and privacy-attestation
  requirements from production settings, Compose, example environment, and
  active deployment documentation.
- Removed the unused provider SDK dependencies from both locked runtime and
  development dependency graphs.
- Final recursive search for provider-specific names returned **no matches**.
- The only remaining legacy symbol is `USE_S3_STORAGE`, deliberately retained
  for one release as the documented compatibility path; it cannot activate a
  provider.

## F. Security Review

- Production does not route `/media/`; `static(settings.MEDIA_URL, ...)` is
  still gated by `DEBUG` only.
- New uploads receive UUID-based keys; original upload names are not used as
  storage keys or response headers.
- `PrivateFileSystemStorage` rejects absolute paths, parent traversal, and null
  bytes. Existing upload validators still enforce signature/type/size rules.
- Protected file, chat attachment, print item, support attachment, and
  verification-card streams now require authentication and the same user who
  received the ticket. They re-check current resource access/RBAC where
  applicable and hide invalid/foreign tickets with 404.
- Stream responses use safe `Content-Disposition`, `Cache-Control: private,
  no-store`, and `X-Content-Type-Options: nosniff`.
- No direct `FieldFile.path` or public storage URL is used by the protected
  file flows.

## G. Coolify Configuration

Use **Application → Storages → Add Persistent Storage → Volume**:

```text
Name: panorama_media
Destination Path: /app/app/media
```

Runtime variables only:

```dotenv
STORAGE_BACKEND=local
MEDIA_ROOT=/app/app/media
MEDIA_URL=/media/
```

The image runs as `panorama` UID/GID `10001`. Verify with `id`, `ls -ld
/app/app/media`, a temporary `touch`/`rm`, and `python manage.py storage_status
--write-test`. Do not publish `/media/` in Coolify.

## H. Dependency Changes

Removed: `boto3`, `django-storages[s3]`, and their transitive S3 transfer
packages from the resolved production graph. They had no business-runtime use
after the local backend was selected. `requirements.lock` and
`requirements-dev.lock` were regenerated with `pip-compile --generate-hashes
--no-upgrade`; unrelated direct package versions were not intentionally
upgraded.

## I. Test Evidence

| Command / environment | Result | Exit |
| --- | --- | --- |
| `pytest -q` (`config.settings.testing`) | 83 tests passed | 0 |
| `coverage run -m pytest -q`; `coverage report --fail-under=85` | 86% total (7,262 statements, 1,026 missed); XML and HTML generated | 0 |
| `python app/manage.py check --settings=config.settings.testing` | no issues | 0 |
| `python app/manage.py makemigrations --check --dry-run --settings=config.settings.testing` | no changes detected | 0 |
| `python app/manage.py storage_status` and `--write-test` | local backend readable/writable; round-trip passed and cleaned up | 0 |
| `python app/manage.py check --deploy --settings=config.settings.production` with generated test-only secrets, local mode, and no cloud variables | no issues | 0 |
| real `collectstatic` with a temporary `STATIC_ROOT` | passed; did not write to media | 0 |
| `ruff check app` | passed | 0 |
| `bandit -q -r app -ll ...` | no medium/high findings | 0 |
| `pip-audit --disable-pip -r requirements.lock` | No known vulnerabilities found | 0 |
| OpenAPI JSON/YAML validation and Postman generation | passed | 0 |
| `mypy` critical-module command | **54 pre-existing errors in 27 files**; no new storage-specific error remains | 1 |
| `ruff format --check .` | **57 pre-existing files need formatting**; all files changed in this scope are formatted | 1 |

`coverage.xml` and `htmlcov/` are ignored generated validation artifacts and
were refreshed locally; they are not source or media payloads.

## J. Docker Evidence

`docker compose -f docker-compose.coolify.yml config --quiet` passed with
synthetic runtime-only values and `STORAGE_BACKEND=local` (exit 0).

`docker build --no-cache -t panorama-backend:local-storage .` was attempted
after the local Buildx permission issue was retried outside the sandbox. It is
**BLOCKED** because `dockerDesktopLinuxEngine` is not running on this machine.
Consequently no image ID, digest, size, `docker image inspect`, container
runtime, or volume-restart evidence exists. The Dockerfile configuration is
reviewed but not represented as execution evidence.

## K. Alternative Technologies

See `docs/STORAGE_TECHNOLOGY_ALTERNATIVES_AR.md`. It compares local volume,
MinIO, AWS S3, Backblaze B2, Wasabi, DigitalOcean Spaces, Google Cloud Storage,
Azure Blob Storage, and Ceph RGW using official source links checked on
2026-07-31. Current recommendation: local named volume. Future recommendation:
a generic S3-compatible provider after an adapter, cost, migration, and
rollback validation.

## L. Deployment Steps

1. In Coolify, attach `panorama_media` at `/app/app/media` as a Runtime
   persistent Volume.
2. Set the three local-storage environment variables above; remove legacy
   provider variables from the Coolify environment.
3. Deploy the immutable image, run the one-shot release job, then verify
   `storage_status --write-test`, live/ready/startup, a protected upload, and a
   protected download using a synthetic user.
4. Admit traffic only when readiness is HTTP 200 and the synthetic smoke suite
   passes. Detailed steps are in `docs/COOLIFY_LOCAL_MEDIA_STORAGE_AR.md`.

## M. Rollback Plan

Retain the existing media Volume through the release rollback window. If this
release must be rolled back, deploy the previous compatible image while keeping
the same Volume and database. Do not delete the Volume or use a database reverse
migration for this storage configuration. For a future object-storage migration,
keep the local Volume read-only until counts, sizes, checksums, download paths,
and rollback are accepted.

## N. Known Remaining Risks

1. Docker build/runtime and Coolify named-volume persistence were not executed
   because the local Docker daemon and a real Coolify environment are absent.
2. Staging upload/download, backup/restore, malware-scanner integration, and
   operational reverse-proxy non-exposure still require an isolated deployment.
3. Mypy and repository-wide Ruff-format gates remain failing on unrelated
   existing files; they were not suppressed or hidden.
4. Gitleaks, Semgrep, and image scanning tools were not installed locally, so
   their evidence remains a CI/Staging requirement.
5. A single named volume is intentionally not a horizontal-scaling solution;
   use the documented generic S3-compatible migration before multi-node web
   deployment.

## O. Final Verdict

**NOT READY**

The code-level local storage path and local verification suite pass, but a real
Docker image/runtime plus a Coolify persistent-volume deployment have not been
executed. Existing Mypy/formatting failures also remain open quality gates.
