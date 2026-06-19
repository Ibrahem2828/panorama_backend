# Production Environment Reference

Use Coolify environment variables or secrets for production values. Do not commit `.env`, real credentials, private keys, dumps, or media files.

For a deployment-owner checklist with exact current sslip values, final HTTPS examples, and replacement notes, see `docs/PRODUCTION_ENV_VALUES_TO_REPLACE.md`.

## Required Core Settings

- `DJANGO_SETTINGS_MODULE=config.settings.production`
- `SECRET_KEY`: required, long random Django secret. Do not use placeholders.
- `DEBUG=False` and `DJANGO_DEBUG=False`
- `ALLOWED_HOSTS`: required comma-separated backend hostnames, no `*`.
- `CSRF_TRUSTED_ORIGINS`: required comma-separated origins including scheme.
- `CORS_ALLOWED_ORIGINS`: required comma-separated dashboard/mobile web origins including scheme.

Fallback names supported by settings:

- `DJANGO_ALLOWED_HOSTS`
- `DJANGO_CSRF_TRUSTED_ORIGINS`
- `DJANGO_CORS_ALLOWED_ORIGINS`

## Database

Preferred:

- `DATABASE_URL=postgres://user:password@host:5432/database`

Also supported:

- `DB_NAME`
- `DB_USER`
- `DB_PASSWORD`
- `DB_HOST`
- `DB_PORT`

Set `DATABASE_SSL_REQUIRE=True` only when the PostgreSQL provider requires SSL.

## Redis

- `REDIS_URL=redis://host:6379/0`

Production uses Redis for Django cache, DRF throttling, Channels, Celery broker, and Celery result backend.

## JWT

- `JWT_ACCESS_TOKEN_LIFETIME_MINUTES=60`
- `JWT_REFRESH_TOKEN_LIFETIME_DAYS=14`

Refresh tokens rotate on use, old refresh tokens are blacklisted after rotation, and successful JWT auth updates `last_login`.

## Throttling

- `THROTTLE_LOGIN=5/minute`
- `THROTTLE_REGISTER=5/hour`
- `THROTTLE_OTP_SEND=3/10min`
- `THROTTLE_OTP_VERIFY=5/10min`
- `THROTTLE_PASSWORD_RESET=3/15min`
- `THROTTLE_VERIFICATION_SUBMIT=3/hour`
- `THROTTLE_CHAT_MESSAGE=30/minute`
- `THROTTLE_SUPPORT_TICKET_CREATE=5/hour`
- `THROTTLE_PRINT_ORDER_CREATE=10/hour`

Production throttling uses the Django Redis cache configured by `REDIS_URL`.

## Security

- `SECURE_SSL_REDIRECT=True` after HTTPS is configured.
- `SESSION_COOKIE_SECURE=True` after HTTPS is configured.
- `CSRF_COOKIE_SECURE=True` after HTTPS is configured.
- `SECURE_HSTS_SECONDS=31536000` after HTTPS is stable.
- `SECURE_HSTS_INCLUDE_SUBDOMAINS=True` only when every subdomain is HTTPS-ready.
- `SECURE_HSTS_PRELOAD=True` only when the domain is ready for browser preload requirements.
- `USE_X_FORWARDED_HOST=True` for Coolify reverse proxy deployments.
- `SECURE_PROXY_SSL_HEADER=True` when the proxy sends `X-Forwarded-Proto=https`.
- `SECURE_REFERRER_POLICY=same-origin`
- `X_FRAME_OPTIONS=DENY`

## Static and Media

Static files:

- `STATIC_URL=/static/` is configured in code.
- `STATIC_ROOT=/app/staticfiles` in the production image.
- `RUN_COLLECTSTATIC=True` runs `collectstatic --noinput` at container startup.

Media files:

- `MEDIA_URL=/media/` is configured in code.
- `MEDIA_ROOT=/app/media` in the production image.
- Mount a persistent volume to `/app/media`.
- Do not expose `/media/` directly through the proxy for private files.
- Sensitive media is accessed through short-lived protected URLs from token endpoints.

Protected media settings:

- `PROTECTED_MEDIA_TOKEN_TTL_SECONDS=300`

Current protected media endpoints:

- `POST /api/v1/files/{file_id}/download-token/`
- `POST /api/v1/dashboard/files/{file_id}/preview-token/`
- `POST /api/v1/dashboard/verifications/{id}/card-preview-token/`
- `POST /api/v1/dashboard/printing/orders/{id}/file-preview-token/`
- `GET /api/v1/protected-media/{token}/`

## WebSocket Tokens

- `GROUP_CHAT_WS_TOKEN_TTL_SECONDS=120`
- `ALLOW_WEBSOCKET_ACCESS_TOKEN_AUTH=False`

Production clients must request a short-lived token with:

```text
POST /api/v1/groups/{group_id}/chat/ws-token/
```

Then connect with:

```text
ws://host/ws/v1/groups/{group_id}/chat/?token={ws_token}
```

Use `wss://` after HTTPS/TLS is configured. Do not use JWT access tokens directly in production WebSocket URLs.

## Runtime

- `PORT=8000`
- `HEALTHCHECK_HOST`: optional host header for internal health checks. Use the public API hostname if `ALLOWED_HOSTS` does not include `localhost`.
- `LOG_LEVEL=INFO`
- `RUN_DEPLOY_CHECK=True`
- `RUN_MIGRATIONS=True`
- `RUN_COLLECTSTATIC=True`
- `RUN_SETUP_ADMIN_ACCOUNTS=False` for normal production after first bootstrap.
- `RUN_SEED_DATA=False`
- `WAIT_FOR_DATABASE=True`
- `WAIT_FOR_REDIS=True`
- `WAIT_TIMEOUT_SECONDS=60`

For multi-instance production, run migrations as a release command or one-off job and set `RUN_MIGRATIONS=False` on web replicas.

## Celery

- `CELERY_TASK_TIME_LIMIT=300`
- `CELERY_LOG_LEVEL=INFO`
- `OTP_CLEANUP_RETENTION_DAYS=1`
- `OTP_CLEANUP_INTERVAL_SECONDS=86400`

Worker and beat use `REDIS_URL`. They should set:

- `RUN_DEPLOY_CHECK=False`
- `RUN_MIGRATIONS=False`
- `RUN_COLLECTSTATIC=False`
- `RUN_SETUP_ADMIN_ACCOUNTS=False`
- `RUN_SEED_DATA=False`

## Optional Integrations

- `FCM_SERVER_KEY`: set only if push notifications are used.
- Admin bootstrap variables from `.env.example`: set strong values for first deploy, then keep `RUN_SETUP_ADMIN_ACCOUNTS=False`.

External Sentry/email storage settings are not implemented in this phase. Add them only with matching code and tests.
