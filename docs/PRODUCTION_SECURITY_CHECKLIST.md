# Production Security Checklist

Complete this before public launch.

- `DEBUG=False`.
- `SECRET_KEY` is strong, unique, and not a placeholder.
- `ALLOWED_HOSTS` uses explicit hostnames and does not contain `*`.
- `CORS_ALLOWED_ORIGINS` is limited to trusted frontend origins.
- `CSRF_TRUSTED_ORIGINS` is limited to trusted HTTPS origins.
- HTTPS is enabled in Coolify.
- `SECURE_SSL_REDIRECT=True` after HTTPS is active.
- `SESSION_COOKIE_SECURE=True`.
- `CSRF_COOKIE_SECURE=True`.
- HSTS values are intentionally chosen for the domain.
- `.env` is not committed.
- Media uploads are not committed.
- No secrets appear in logs.
- PostgreSQL is not publicly exposed.
- Redis is not publicly exposed.
- Admin, IT, and print staff credentials are strong and changed after bootstrap.
- Demo users are disabled or have non-demo passwords.
- `RUN_SEED_DATA=False` in production unless intentionally seeding.
- FCM key is configured only if push notifications are used.
- Backups are configured.
- Restore drill has been tested.
- Protected file view has been verified.
- Direct public media exposure is disabled or reviewed.
- DRF throttling uses Redis.
- WebSockets use Redis channel layer.
- `python app/manage.py check --deploy --settings config.settings.production` passes.
- `scripts/validate_backend.ps1 -DeployCheck` passes before release.
