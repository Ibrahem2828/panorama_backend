# Backend Foundation Hardening

This document records the Phase 1 production foundation rules for the Panorama backend.

Production backend base URL:

```text
http://eby52x8qksscjvfeqxf0eob7.76.13.155.172.sslip.io
```

## Release Hygiene

Production releases must never include:

- `.env` or `.env.*` files except the safe tracked `.env.example`
- `.git`
- `__pycache__/`, `*.pyc`, `*.pyo`, or `*.pyd`
- local `media/` uploads
- `logs/` or `*.log`
- local database files such as `db.sqlite3`, `*.sqlite3`, `*.sqlite`, or `*.db`
- local virtual environments, editor folders, coverage reports, or build artifacts

Use Git-based archives for handoff releases so ignored files are not packaged.

## Required Production Environment

Required:

- `DJANGO_SETTINGS_MODULE=config.settings.production`
- `SECRET_KEY`
- `DEBUG=False` or `DJANGO_DEBUG=False`
- `ALLOWED_HOSTS` or `DJANGO_ALLOWED_HOSTS`
- `CORS_ALLOWED_ORIGINS` or `DJANGO_CORS_ALLOWED_ORIGINS`
- `CSRF_TRUSTED_ORIGINS` or `DJANGO_CSRF_TRUSTED_ORIGINS`
- `DATABASE_URL` or all `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`
- `REDIS_URL`

Security controls:

- `SECURE_SSL_REDIRECT`
- `SESSION_COOKIE_SECURE`
- `CSRF_COOKIE_SECURE`
- `SECURE_HSTS_SECONDS`
- `SECURE_HSTS_INCLUDE_SUBDOMAINS`
- `SECURE_HSTS_PRELOAD`
- `SECURE_PROXY_SSL_HEADER` or `DJANGO_SECURE_PROXY_SSL_HEADER`
- `USE_X_FORWARDED_HOST` or `DJANGO_USE_X_FORWARDED_HOST`

`SECURE_SSL_REDIRECT` remains env-driven because the current deployment may still serve HTTP through the platform URL.

## API Response Contract

Success responses use:

```json
{
  "success": true,
  "message": "OK",
  "data": {}
}
```

Error responses use:

```json
{
  "success": false,
  "message": "Validation error",
  "errors": {},
  "request_id": "..."
}
```

Field-level validation errors remain under `errors`.

Paginated responses are wrapped under `data.count`, `data.next`, `data.previous`, and `data.results`.

## Request ID

Every request accepts `X-Request-ID`. If it is missing or invalid, the backend generates a UUID request ID. The ID is attached to `request.request_id`, returned as the `X-Request-ID` response header, included in normalized API errors, and stored on audit logs when a request is available.

## JWT Refresh Rotation

SimpleJWT is configured with:

- `ROTATE_REFRESH_TOKENS=True`
- `BLACKLIST_AFTER_ROTATION=True`
- `UPDATE_LAST_LOGIN=True`

The existing paths are preserved:

- `/api/v1/auth/login/`
- `/api/v1/auth/token/refresh/`
- `/api/v1/auth/logout/`
- `/api/v1/auth/me/`

Logout blacklists the submitted refresh token.

## Throttle Scopes

DRF scoped throttling uses Redis cache in production. Auth and OTP scopes key by IP plus hashed identifier where available.

- `login`: `5/minute`
- `register`: `5/hour`
- `otp_send`: `3/10min`
- `otp_verify`: `5/10min`
- `password_reset`: `3/15min`
- `verification_submit`: `3/hour`
- `chat_message`: `30/minute`
- `support_ticket_create`: `5/hour`
- `print_order_create`: `10/hour`

## Phase 2 Finalization

Phase 2 adds tokenized protected-media and WebSocket flows:

- Mobile file downloads: `POST /api/v1/files/{file_id}/download-token/`
- Dashboard file preview: `POST /api/v1/dashboard/files/{file_id}/preview-token/`
- Dashboard verification card preview: `POST /api/v1/dashboard/verifications/{id}/card-preview-token/`
- Dashboard print file preview: `POST /api/v1/dashboard/printing/orders/{id}/file-preview-token/`
- Protected media serving: `GET /api/v1/protected-media/{token}/`
- Group chat WebSocket token: `POST /api/v1/groups/{group_id}/chat/ws-token/`

Production WebSocket clients must use `ws_token`, not JWT access tokens, in the WebSocket URL.

Production API contract files:

- `docs/api/panorama_mobile_api_collection_v2_production.json`
- `docs/api/panorama_dashboard_api_collection_v2_production.json`

Deployment value replacement guide:

- `docs/PRODUCTION_ENV_VALUES_TO_REPLACE.md`
