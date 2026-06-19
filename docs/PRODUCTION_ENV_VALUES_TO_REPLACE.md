# Production Environment Values To Replace

This guide is for the project owner or deployer before final production deployment. Keep real values in the deployment platform secret store or `.env` on the server. Do not commit them.

Current backend base URL:

```text
http://eby52x8qksscjvfeqxf0eob7.76.13.155.172.sslip.io
```

For the current backend deployment, `ALLOWED_HOSTS` and `DJANGO_ALLOWED_HOSTS` must include:

```text
eby52x8qksscjvfeqxf0eob7.76.13.155.172.sslip.io
```

## Core Backend Values

| Variable | Current/example value | Replace with | Where to get it | Required | Notes |
| --- | --- | --- | --- | --- | --- |
| `DJANGO_SETTINGS_MODULE` | `config.settings.production` | Keep this for production | Project setting | Yes | Use `config.settings.testing` only for tests. |
| `SECRET_KEY` | `replace-with-a-generated-django-secret-key` | A long random Django secret | Generate once with a secure password/secret generator | Yes | Never reuse the example value and never commit the real value. |
| `DEBUG` | `False` | `False` | Deployment config | Yes | Must be false in production. |
| `DJANGO_DEBUG` | `False` | `False` | Deployment config | Yes | Supported fallback/alias; keep aligned with `DEBUG`. |
| `ALLOWED_HOSTS` | `api.example.com` | Backend hostnames without scheme | Backend deployment domain | Yes | Do not use `*` in production. |
| `DJANGO_ALLOWED_HOSTS` | `api.example.com` | Same as `ALLOWED_HOSTS` | Backend deployment domain | Recommended | Supported fallback/alias. |
| `PORT` | `8000` | Platform-provided port if different | Hosting provider | Yes | Keep `8000` for the current container unless the platform injects another port. |
| `HEALTHCHECK_HOST` | `api.example.com` | Backend hostname used for health checks | Backend deployment domain | Optional | Useful if internal health checks fail due host validation. |

Current-stage example:

```env
ALLOWED_HOSTS=eby52x8qksscjvfeqxf0eob7.76.13.155.172.sslip.io,localhost,127.0.0.1
DJANGO_ALLOWED_HOSTS=eby52x8qksscjvfeqxf0eob7.76.13.155.172.sslip.io,localhost,127.0.0.1
```

## CORS And CSRF

Replace placeholder origins before deployment:

```env
CSRF_TRUSTED_ORIGINS=https://api.example.com,https://dashboard.example.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://api.example.com,https://dashboard.example.com
CORS_ALLOWED_ORIGINS=https://dashboard.example.com
DJANGO_CORS_ALLOWED_ORIGINS=https://dashboard.example.com
```

- Replace `https://api.example.com` with the real backend origin if HTTPS is used.
- For the current HTTP sslip backend, use `http://eby52x8qksscjvfeqxf0eob7.76.13.155.172.sslip.io`.
- Replace `https://dashboard.example.com` with the real dashboard frontend URL.
- If the dashboard is local during development, include `http://localhost:3000`.
- If the dashboard is deployed later, include its actual HTTPS domain.
- `CSRF_TRUSTED_ORIGINS` must include scheme, for example `http://` or `https://`.
- `CORS_ALLOWED_ORIGINS` must include scheme and exact origin.
- Do not use `*` in production.

Current-stage HTTP example:

```env
CSRF_TRUSTED_ORIGINS=http://eby52x8qksscjvfeqxf0eob7.76.13.155.172.sslip.io,http://localhost:3000
DJANGO_CSRF_TRUSTED_ORIGINS=http://eby52x8qksscjvfeqxf0eob7.76.13.155.172.sslip.io,http://localhost:3000
CORS_ALLOWED_ORIGINS=http://localhost:3000
DJANGO_CORS_ALLOWED_ORIGINS=http://localhost:3000
```

Final HTTPS production example:

```env
CSRF_TRUSTED_ORIGINS=https://api.your-domain.com,https://dashboard.your-domain.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://api.your-domain.com,https://dashboard.your-domain.com
CORS_ALLOWED_ORIGINS=https://dashboard.your-domain.com
DJANGO_CORS_ALLOWED_ORIGINS=https://dashboard.your-domain.com
```

## Security Toggles

| Variable | Current/example value | Replace with | Required | Notes |
| --- | --- | --- | --- | --- |
| `SECURE_SSL_REDIRECT` | `True` | `False` for current HTTP, `True` after HTTPS | Yes | Do not force HTTPS until the backend is actually served over HTTPS. |
| `DJANGO_SECURE_SSL_REDIRECT` | `True` | Same as `SECURE_SSL_REDIRECT` | Recommended | Supported fallback/alias. |
| `SESSION_COOKIE_SECURE` | `True` | `False` for current HTTP, `True` after HTTPS | Yes | Secure cookies require HTTPS. |
| `DJANGO_SESSION_COOKIE_SECURE` | `True` | Same as `SESSION_COOKIE_SECURE` | Recommended | Supported fallback/alias. |
| `CSRF_COOKIE_SECURE` | `True` | `False` for current HTTP, `True` after HTTPS | Yes | Secure cookies require HTTPS. |
| `DJANGO_CSRF_COOKIE_SECURE` | `True` | Same as `CSRF_COOKIE_SECURE` | Recommended | Supported fallback/alias. |
| `SECURE_HSTS_SECONDS` | `31536000` | `0` for current HTTP, `31536000` after stable HTTPS | Yes | Enable HSTS only after HTTPS is stable. |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | `True` | `False` unless every subdomain is HTTPS-ready | Yes | Avoid locking subdomains before they are ready. |
| `SECURE_HSTS_PRELOAD` | `False` | `True` only when preload requirements are met | Optional | Preload is hard to reverse. |
| `SECURE_PROXY_SSL_HEADER` | `True` | `True` when proxy sends `X-Forwarded-Proto=https` | Recommended | Project maps this to Django's `SECURE_PROXY_SSL_HEADER`. |
| `DJANGO_SECURE_PROXY_SSL_HEADER` | `True` | Same as `SECURE_PROXY_SSL_HEADER` | Recommended | Supported fallback/alias. |
| `USE_X_FORWARDED_HOST` | `True` | `True` behind Coolify/reverse proxy | Recommended | Keep false only if the proxy does not preserve host headers. |
| `DJANGO_USE_X_FORWARDED_HOST` | `True` | Same as `USE_X_FORWARDED_HOST` | Recommended | Supported fallback/alias. |

## Database

| Variable | Current/example value | Replace with | Where to get it | Required | Notes |
| --- | --- | --- | --- | --- | --- |
| `DATABASE_URL` | `postgres://db_user:db_password@db_host:5432/db_name` | Production PostgreSQL URL | Database provider | Preferred | Do not commit real credentials. |
| `DATABASE_SSL_REQUIRE` | `False` | `True` only if provider requires SSL | Database provider docs | Optional | Keep false for local Docker. |
| `DB_NAME` | `panorama_db` | Production database name | Database provider | Required if no `DATABASE_URL` | Django fallback. |
| `DB_USER` | `panorama_user` | Production database user | Database provider | Required if no `DATABASE_URL` | Django fallback. |
| `DB_PASSWORD` | `replace-with-database-password` | Production database password | Database provider | Required if no `DATABASE_URL` | Secret. |
| `DB_HOST` | `db` | Production database host | Database provider | Required if no `DATABASE_URL` | Use service DNS in Docker. |
| `DB_PORT` | `5432` | Production database port | Database provider | Required if no `DATABASE_URL` | Usually `5432`. |
| `POSTGRES_DB` | `panorama_db` | Provisioned DB name | Docker/Postgres service | If provisioning Postgres container | Used by `docker-compose.yml`, not the Django settings directly. |
| `POSTGRES_USER` | `panorama_user` | Provisioned DB user | Docker/Postgres service | If provisioning Postgres container | Keep aligned with Django DB values. |
| `POSTGRES_PASSWORD` | `panorama_password` | Strong database password | Secret generator | If provisioning Postgres container | Never use the compose example in production. |

## Redis, Channels, Cache, And Celery

| Variable | Current/example value | Replace with | Required | Notes |
| --- | --- | --- | --- | --- |
| `REDIS_URL` | `redis://redis:6379/0` | Production Redis URL | Yes | Used by cache, DRF throttling, Channels, Celery broker, and Celery result backend. |
| `CACHE_DEFAULT_TIMEOUT` | `300` | Keep or tune | Optional | Default cache timeout. |
| `CELERY_TASK_TIME_LIMIT` | `300` | Keep or tune | Optional | Worker task hard limit. |
| `CELERY_LOG_LEVEL` | `INFO` | `INFO` or deployment standard | Optional | Worker log verbosity. |

Redis must be reachable from web, worker, and beat processes. Throttling depends on Django cache, which uses `REDIS_URL` in production.

## JWT

| Variable | Current/example value | Replace with | Required | Notes |
| --- | --- | --- | --- | --- |
| `JWT_ACCESS_TOKEN_LIFETIME_MINUTES` | `60` | Security-approved access lifetime | Optional | Keep short enough for production risk tolerance. |
| `JWT_REFRESH_TOKEN_LIFETIME_DAYS` | `14` | Security-approved refresh lifetime | Optional | Refresh rotation and blacklist are enabled in settings. |

The project uses SimpleJWT with refresh rotation, blacklist-after-rotation, and last-login updates enabled. No separate JWT signing key is currently configured; `SECRET_KEY` signs tokens.

## Protected Media

| Variable | Current/example value | Replace with | Required | Notes |
| --- | --- | --- | --- | --- |
| `PROTECTED_MEDIA_TOKEN_TTL_SECONDS` | `300` | Short preview/download TTL, usually `300` | Yes | Used by protected media token validation. |
| `MEDIA_ROOT` | Code default | Persistent server media path if settings are extended | Deployment-specific | Current settings use local storage path. |
| `MEDIA_URL` | `/media/` | Keep internal; do not expose private media directly | Yes | Sensitive files must be accessed through token endpoints. |

Current implementation uses local Django storage and serves sensitive files through:

```text
GET /api/v1/protected-media/{token}/
```

Future S3 or Cloudflare R2 migration should replace the storage backend behind the same service abstraction. Planned values for that future work may include `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_STORAGE_BUCKET_NAME`, `AWS_S3_ENDPOINT_URL`, and `CLOUDFLARE_R2_ACCOUNT_ID`; do not add real values until the storage backend is implemented.

## WebSocket

| Variable | Current/example value | Replace with | Required | Notes |
| --- | --- | --- | --- | --- |
| `GROUP_CHAT_WS_TOKEN_TTL_SECONDS` | `120` | Short connection-token TTL, usually `120` | Yes | Used by `POST /api/v1/groups/{group_id}/chat/ws-token/`. |
| `ALLOW_WEBSOCKET_ACCESS_TOKEN_AUTH` | `False` | Keep `False` in production | Yes | Production must use `ws_token`, not JWT access tokens in the URL. |

Use `ws://` while the backend is HTTP:

```text
ws://eby52x8qksscjvfeqxf0eob7.76.13.155.172.sslip.io/ws/v1/groups/{group_id}/chat/?token={ws_token}
```

Use `wss://` after HTTPS/TLS is configured:

```text
wss://api.your-domain.com/ws/v1/groups/{group_id}/chat/?token={ws_token}
```

## Admin Bootstrap

| Variable | Current/example value | Replace with | Required | Notes |
| --- | --- | --- | --- | --- |
| `DJANGO_SUPERUSER_EMAIL` | `it@panorama.local` | Real IT support email | First deploy only | IT support bootstrap account. |
| `DJANGO_SUPERUSER_PHONE` | `+963900000001` | Real IT support phone | First deploy only | Must be unique. |
| `DJANGO_SUPERUSER_PASSWORD` | `replace-with-strong-it-support-password` | Strong unique password | First deploy only | Secret. |
| `DJANGO_SUPERUSER_FULL_NAME` | `Panorama IT Support` | Real display name | First deploy only |  |
| `DASHBOARD_ADMIN_EMAIL` | `admin@panorama.local` | Real admin email | First deploy only | Admin bootstrap account. |
| `DASHBOARD_ADMIN_PHONE` | `+963900000002` | Real admin phone | First deploy only | Must be unique. |
| `DASHBOARD_ADMIN_PASSWORD` | `replace-with-strong-admin-password` | Strong unique password | First deploy only | Secret. |
| `DASHBOARD_ADMIN_FULL_NAME` | `Panorama Admin` | Real display name | First deploy only |  |
| `PRINT_STAFF_EMAIL` | `print@panorama.local` | Real print staff email | First deploy only | Print staff bootstrap account. |
| `PRINT_STAFF_PHONE` | `+963900000003` | Real print staff phone | First deploy only | Must be unique. |
| `PRINT_STAFF_PASSWORD` | `replace-with-strong-print-staff-password` | Strong unique password | First deploy only | Secret. |
| `PRINT_STAFF_FULL_NAME` | `Panorama Print Staff` | Real display name | First deploy only |  |
| `RESET_ADMIN_PASSWORDS` | `False` | Keep `False` after bootstrap | Optional | Set true only for an intentional password reset operation. |

Set `RUN_SETUP_ADMIN_ACCOUNTS=True` only for first bootstrap or an intentional account update. For normal production restarts, use `False`.

## OTP And Notifications

| Variable | Current/example value | Replace with | Required | Notes |
| --- | --- | --- | --- | --- |
| `RETURN_DEVELOPMENT_OTP` | Not in `.env.example`; production setting forces `False` | Keep disabled in production | Yes | Development OTP responses must not be exposed in production. |
| `MAX_OTP_VERIFY_ATTEMPTS` | `5` | Keep or tune | Optional | Anti-bruteforce control. |
| `OTP_CLEANUP_RETENTION_DAYS` | `1` | Keep or tune | Optional | Celery cleanup retention. |
| `OTP_CLEANUP_INTERVAL_SECONDS` | `86400` | Keep or tune | Optional | Cleanup schedule interval. |
| `FCM_SERVER_KEY` | `replace-with-fcm-server-key-or-leave-empty-if-unused` | Real FCM server key if push is enabled | Optional | Push sending is a no-op until configured. |

No WhatsApp or SMS provider keys are implemented in this phase. Add provider variables only with matching code, docs, and tests.

## Dashboard And Mobile Frontend Values

Current-stage frontend values:

```env
NEXT_PUBLIC_API_BASE_URL=http://eby52x8qksscjvfeqxf0eob7.76.13.155.172.sslip.io
NEXT_PUBLIC_WS_BASE_URL=ws://eby52x8qksscjvfeqxf0eob7.76.13.155.172.sslip.io
MOBILE_API_BASE_URL=http://eby52x8qksscjvfeqxf0eob7.76.13.155.172.sslip.io
MOBILE_WS_BASE_URL=ws://eby52x8qksscjvfeqxf0eob7.76.13.155.172.sslip.io
```

Final HTTPS values:

```env
NEXT_PUBLIC_API_BASE_URL=https://api.your-domain.com
NEXT_PUBLIC_WS_BASE_URL=wss://api.your-domain.com
MOBILE_API_BASE_URL=https://api.your-domain.com
MOBILE_WS_BASE_URL=wss://api.your-domain.com
```

## Release Hygiene

- Never commit `.env` or `.env.*` files except the placeholder-only `.env.example`.
- Never ship `.git`.
- Never ship `__pycache__/`, `*.pyc`, or local virtual environments.
- Never ship local `media/`, local `logs/`, or local database files.
- Keep `.env.example` safe and placeholder-only.

## Final Checklist

- [ ] `SECRET_KEY` changed
- [ ] `DEBUG=False`
- [ ] `ALLOWED_HOSTS` set
- [ ] CORS set to dashboard domain only
- [ ] CSRF trusted origins set with scheme
- [ ] Database production URL set
- [ ] Redis URL set
- [ ] HTTPS security toggles configured appropriately
- [ ] Development OTP disabled
- [ ] Admin seed credentials changed
- [ ] Protected media TTL set
- [ ] WebSocket token TTL set
- [ ] API JSON files updated
- [ ] `pytest` passes
- [ ] deploy check passes
