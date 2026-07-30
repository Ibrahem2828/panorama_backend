# Coolify environment matrix

Set these in Coolify's encrypted environment store only. Never add a production `.env` file to Git, image layers, build arguments, or CI logs. The compose file deliberately uses `${VAR:?message}` for values required at runtime.

| Class | Variables | Rule |
| --- | --- | --- |
| Required / secret | `SECRET_KEY`, `FIELD_ENCRYPTION_KEY` | Random, unique per environment; never log. `FIELD_ENCRYPTION_KEY` must be a valid Fernet key. |
| Required / database | `DATABASE_URL`, `DATABASE_SSL_REQUIRE` | Use a managed database URL or the internal `postgres` hostname. TLS is required for a remote database. |
| Required / Redis | `REDIS_URL` | Use a managed Redis URL or internal `redis`; do not publish Redis ports. |
| Required / storage | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_STORAGE_BUCKET_NAME`, `AWS_S3_ENDPOINT_URL`, `AWS_S3_REGION_NAME`, `AWS_QUERYSTRING_EXPIRE`, `S3_BUCKET_PRIVATE` | Bucket must be private, Block Public Access enabled (or R2 equivalent), CORS restricted, and signed URL expiry short. `S3_BUCKET_PRIVATE=True` is an operator attestation, not a replacement for provider-policy verification. |
| Required / routing | `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `CORS_ALLOWED_ORIGINS`, `APP_BASE_URL`, `PORT`, `HEALTHCHECK_HOST` | HTTPS origins only; hosts explicit and never `*`. `HEALTHCHECK_HOST` must be in `ALLOWED_HOSTS`. |
| Required / transport security | `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_HSTS_SECONDS`, `SECURE_HSTS_INCLUDE_SUBDOMAINS`, `SECURE_HSTS_PRELOAD`, `USE_X_FORWARDED_HOST` | Set after proxy/TLS verification. Do not enable HSTS preload until all subdomains are ready. |
| Required / email | `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | Use a dedicated SMTP credential. |
| Required / release metadata | `IMAGE_TAG`, `RELEASE_VERSION`, `BUILD_DATE`, `LOG_LEVEL` | `IMAGE_TAG` is the immutable Git SHA published by CI; do not use `latest`. |
| Self-hosted services only | `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Used only when `COMPOSE_PROFILES=self-hosted`. The application still consumes only `DATABASE_URL`. |
| Optional / feature flags | `API_DOCS_ENABLED`, `PUSH_NOTIFICATIONS_ENABLED`, `FEEDBACK_AI_TRIAGE_ENABLED`, `FCM_SERVER_KEY`, `EXPO_ACCESS_TOKEN`, `SENTRY_DSN` | Default off or empty; enable after scoped validation. |
| Optional / controlled release | `RUN_SETUP_ADMIN_ACCOUNTS`, `RUN_SEED_DATA`, `RUN_SEED_PRODUCTION_DEFAULTS` and required seed identity fields | Set only on the one-shot release job. Never enable runtime maintenance. |
| Generated / non-secret | `TRUSTED_PROXY_COUNT`, throttles, retention values, Celery concurrency/log level | Version in an approved Coolify environment template; do not duplicate aliases. |
| Rotatable | DB/Redis credentials, SMTP credential, S3 keys, Expo/FCM/Sentry tokens | Follow the rotation procedure below; update one canonical name only. |

## Rotation procedure

1. Record the current immutable image SHA and confirm `/api/v1/health/ready/` is 200.
2. Create the new credential in its provider; where possible, overlap old and new credentials.
3. Update the single Coolify variable, deploy the same image SHA, and run `scripts/smoke_test.py` against Staging first.
4. Promote only after readiness, authenticated smoke, and provider-specific verification pass. Revoke the old credential after the overlap window.
5. If validation fails, restore the former Coolify variable and redeploy the previous SHA; do not roll back a database migration blindly.

| Credential | Impact and special handling |
| --- | --- |
| `SECRET_KEY` | Invalidates Django sessions, CSRF-derived state, and signed values. Schedule user re-authentication; preserve old key only via an explicit, time-bounded dual-key migration if code supports it. |
| `FIELD_ENCRYPTION_KEY` | Existing encrypted channel data cannot be read after a simple replacement. Requires an application re-encryption migration with old and new keys; do not rotate by environment-only change. |
| DB / Redis | Update provider first, then all app/release services atomically. Validate worker and beat after redeploy. |
| SMTP | Send a controlled test message to an owned mailbox; never put the credential in a smoke-test URL or log. |
| S3/R2 | Grant new least-privilege key, validate private upload/download tickets, then revoke old key. |
| Expo / FCM / Sentry | Enable the new token with the feature flag, verify a synthetic event, then revoke the old token. |
