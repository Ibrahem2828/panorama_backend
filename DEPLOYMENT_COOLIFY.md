# Panorama Backend Coolify Deployment

## Coolify Settings

- Build pack: Dockerfile
- Base directory: `/`
- Dockerfile location: `/Dockerfile`
- Exposed port: `8000`
- Environment variable: `PORT=8000`
- Healthcheck path: `/api/v1/health/`

Do not configure the backend as `3000:3000`. Port `3000` is for the Next.js dashboard. The Django backend container listens on `8000`, and the Coolify domain must route to port `8000`.

## Required Environment Variables

```env
DJANGO_SETTINGS_MODULE=config.settings.production
SECRET_KEY=change-me-to-a-long-random-secret
DEBUG=False
DJANGO_DEBUG=False

ALLOWED_HOSTS=api.example.com,localhost,127.0.0.1
# DJANGO_ALLOWED_HOSTS is also supported as a fallback.

CORS_ALLOWED_ORIGINS=https://dashboard.example.com,http://localhost:3000
CSRF_TRUSTED_ORIGINS=https://api.example.com,https://dashboard.example.com

DATABASE_URL=postgres://user:pass@host:5432/dbname
DATABASE_SSL_REQUIRE=True

REDIS_URL=redis://redis:6379/0
PORT=8000

DJANGO_SECURE_SSL_REDIRECT=False
DJANGO_SESSION_COOKIE_SECURE=False
DJANGO_CSRF_COOKIE_SECURE=False
DJANGO_USE_X_FORWARDED_HOST=True
LOG_LEVEL=INFO

RUN_MIGRATIONS=True
RUN_COLLECTSTATIC=True
RUN_SETUP_ADMIN_ACCOUNTS=True
RUN_SEED_DATA=False

DJANGO_SUPERUSER_EMAIL=it@example.com
DJANGO_SUPERUSER_PHONE=+963900000001
DJANGO_SUPERUSER_PASSWORD=change-this-password
DJANGO_SUPERUSER_FULL_NAME=Panorama IT Support

DASHBOARD_ADMIN_EMAIL=admin@example.com
DASHBOARD_ADMIN_PHONE=+963900000002
DASHBOARD_ADMIN_PASSWORD=change-this-password
DASHBOARD_ADMIN_FULL_NAME=Panorama Admin

PRINT_STAFF_EMAIL=print@example.com
PRINT_STAFF_PHONE=+963900000003
PRINT_STAFF_PASSWORD=change-this-password
PRINT_STAFF_FULL_NAME=Panorama Print Staff

RESET_ADMIN_PASSWORDS=False
```

`ALLOWED_HOSTS` has priority over `DJANGO_ALLOWED_HOSTS`. `DEBUG` has priority over `DJANGO_DEBUG`. `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS` also support `DJANGO_CORS_ALLOWED_ORIGINS` and `DJANGO_CSRF_TRUSTED_ORIGINS` as fallback names.

## Current sslip.io Example

For:

```text
http://al9ox91yxkddpggmpmz9zg09.76.13.155.172.sslip.io
```

Use:

```env
ALLOWED_HOSTS=al9ox91yxkddpggmpmz9zg09.76.13.155.172.sslip.io,76.13.155.172,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=http://al9ox91yxkddpggmpmz9zg09.76.13.155.172.sslip.io,https://al9ox91yxkddpggmpmz9zg09.76.13.155.172.sslip.io
```

Set `CORS_ALLOWED_ORIGINS` to the deployed dashboard URL when the dashboard is on a separate domain.

## First Deployment

1. Set the environment variables above.
2. Deploy the Dockerfile app in Coolify.
3. Keep `RUN_MIGRATIONS=True` for automatic migrations.
4. Keep `RUN_COLLECTSTATIC=True` for static files.
5. Keep `RUN_SETUP_ADMIN_ACCOUNTS=True` after setting strong account passwords.
6. Use `RUN_SEED_DATA=True` only when demo/initial data is explicitly needed.

The container starts Daphne:

```sh
daphne -b 0.0.0.0 -p ${PORT:-8000} config.asgi:application
```

This serves HTTP and Django Channels WebSockets.

## Verification

- Open `/api/v1/health/`
- Open `/api/docs/`
- Login to the dashboard with the dashboard admin account.
- Login to Django admin with the IT support superuser if `/admin/` is exposed.

Manual validation commands:

```sh
python app/manage.py check --settings=config.settings.testing
python app/manage.py makemigrations --check --dry-run --settings=config.settings.testing
python app/manage.py spectacular --settings=config.settings.testing --validate --fail-on-warn --file NUL
pytest
```

Production settings smoke check:

```sh
$env:DJANGO_SETTINGS_MODULE="config.settings.production"
$env:SECRET_KEY="test-secret"
$env:ALLOWED_HOSTS="localhost,127.0.0.1"
$env:DATABASE_URL="postgres://user:pass@localhost:5432/panorama"
$env:REDIS_URL="redis://localhost:6379/0"
python app/manage.py check
```

The smoke check validates settings import and Django configuration. It does not require a live database unless a command opens a database connection.

## Static, Media, and Redis

Static files are collected into `STATIC_ROOT` and served by Whitenoise. Uploaded media uses local `MEDIA_ROOT`; production media should eventually move to object storage such as S3, Cloudflare R2, or DigitalOcean Spaces.

`REDIS_URL` is required for production WebSockets through Django Channels and for Celery-ready broker/result settings.

## HTTPS Security Flags

For temporary plain HTTP sslip.io testing:

```env
DJANGO_SECURE_SSL_REDIRECT=False
DJANGO_SESSION_COOKIE_SECURE=False
DJANGO_CSRF_COOKIE_SECURE=False
```

When a real HTTPS domain is configured, set:

```env
DJANGO_SECURE_SSL_REDIRECT=True
DJANGO_SESSION_COOKIE_SECURE=True
DJANGO_CSRF_COOKIE_SECURE=True
```

Coolify runs behind a reverse proxy, so production settings use:

```python
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
```

## Troubleshooting

- `ALLOWED_HOSTS not found`: set `ALLOWED_HOSTS` or `DJANGO_ALLOWED_HOSTS`; do not use `*` in production.
- `DisallowedHost`: include the exact Coolify hostname and any sslip.io hostname in `ALLOWED_HOSTS`.
- CSRF errors: add backend and dashboard origins, including scheme, to `CSRF_TRUSTED_ORIGINS`.
- CORS errors: add the dashboard origin to `CORS_ALLOWED_ORIGINS`.
- Database errors: verify `DATABASE_URL` and set `DATABASE_SSL_REQUIRE=True` if the provider requires SSL.
- Redis/WebSocket errors: verify `REDIS_URL` points to the Coolify Redis service.
- Port mismatch: backend exposed port and `PORT` must both be `8000`.
- Collectstatic errors: check `RUN_COLLECTSTATIC` and static file permissions.
- Migration errors: check migration output, database connectivity, and `RUN_MIGRATIONS`.
