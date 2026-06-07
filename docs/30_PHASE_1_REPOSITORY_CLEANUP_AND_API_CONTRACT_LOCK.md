# Phase 1: Repository Cleanup and API Contract Lock

## Purpose

Phase 1 prepares the Panorama backend repository for production hardening while preserving the existing mobile and dashboard API contract. It focuses on repository hygiene, safe configuration, production validation, and route contract protection.

## What Changed

- Expanded `.gitignore` to protect secrets, environment files, Python caches, test caches, media uploads, logs, local databases, IDE folders, temporary files, backups, and archives.
- Added `docs/REPOSITORY_HYGIENE.md` with clean archive guidance, secret handling rules, media handling notes, and rotation steps.
- Made `.env.example` explicit and safe, with placeholders for production values and no real secrets.
- Tightened `config.settings.production` so unsafe production configuration fails during settings import.
- Added API collection contract tests for `docs/api/mobile_api_collection.json` and `docs/api/dashboard_api_collection.json`.
- Added unified response envelope tests for `success_response` and `error_response`.
- Added `scripts/validate_backend.ps1` for repeatable local validation.

## Intentionally Not Changed

- No endpoint paths were changed.
- No HTTP methods were changed.
- No request fields or response fields were renamed or removed.
- The unified API response envelope was not changed.
- Existing serializers, views, permissions, routes, models, and migrations were not removed.
- No product features or business workflows were added.
- No new Python dependencies were added.
- API collection JSON files were not modified.

## API Compatibility Guarantee

The existing mobile and dashboard contracts remain the source of truth. Phase 1 adds tests that lock the documented route surface without requiring successful authenticated workflow execution for every endpoint.

The contract tests verify:

- Collection files exist and parse as JSON.
- Each endpoint declares `name`, `method`, `path`, and `auth_required`.
- Multi-method declarations such as `GET|POST|PATCH|DELETE` split into valid HTTP verbs.
- Paths are syntactically valid and template variables such as `{{id}}` resolve to representative values.
- Documented routes resolve through Django's URL resolver for declared methods as far as possible without database fixtures or external services.

## Repository Hygiene

The repository now ignores local-only artifacts including `.env`, `.env.*`, `media/`, `staticfiles/`, `logs/`, `db.sqlite3`, `*.sqlite3`, pytest caches, Python bytecode, IDE folders, temporary files, backup files, and archives.

Use Git-based archives for clean handoffs:

```powershell
git archive --format zip --output panorama-backend-clean.zip HEAD
```

## Environment Configuration Notes

Production settings require explicit values for:

- `SECRET_KEY`
- `ALLOWED_HOSTS` or `DJANGO_ALLOWED_HOSTS`
- `CSRF_TRUSTED_ORIGINS` or `DJANGO_CSRF_TRUSTED_ORIGINS`
- `CORS_ALLOWED_ORIGINS` or `DJANGO_CORS_ALLOWED_ORIGINS`
- `DATABASE_URL` or the full `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` set

Production settings reject:

- `DEBUG=True`
- Empty or placeholder `SECRET_KEY`
- `ALLOWED_HOSTS=*`

Secure cookie and SSL flags remain environment-driven to support deployments where TLS is terminated by a proxy or platform.

## Validation Commands

Run the standard backend validation:

```powershell
.\scripts\validate_backend.ps1
```

Run the validation including Django deploy checks with validation-only placeholder environment defaults:

```powershell
.\scripts\validate_backend.ps1 -DeployCheck
```

Equivalent manual commands:

```powershell
.\.venv\Scripts\python.exe app\manage.py check
.\.venv\Scripts\python.exe -m pytest
```

For a production deploy check against real deployment variables:

```powershell
.\.venv\Scripts\python.exe app\manage.py check --deploy --settings config.settings.production
```

## Known Limitations

- Contract tests validate route existence and method registration, not authenticated business success for every endpoint.
- Dashboard CRUD collection entries document a single `{{id}}` path for all CRUD methods; the contract test also validates the corresponding DRF list route for `POST`.
- Deploy checks do not connect to external services such as PostgreSQL, Redis, FCM, or object storage.
- Secret rotation and media storage remain operational responsibilities for the deployment environment.

## Next Recommended Phase

Phase 2 should focus on security hardening: authentication/session policy review, permission audits, rate limiting, upload validation, audit coverage, deployment headers, and operational monitoring.
