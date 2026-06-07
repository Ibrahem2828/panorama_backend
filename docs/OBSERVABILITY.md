# Observability

## Current Baseline

The backend currently provides:

- Console logging controlled by `LOG_LEVEL`.
- `X-Request-ID` request correlation.
- Production log lines with `request_id=<value>`.
- Health and readiness endpoints.
- Audit records for domain actions.
- Safe redaction helper for structured diagnostic data.

## Request IDs

The backend accepts client-provided `X-Request-ID` values that contain only letters, numbers, `.`, `_`, `:`, or `-` and are at most 128 characters.

Invalid or missing values are replaced with a generated UUID.

## Safe Logging

Use `apps.common.logging.sanitize_for_logging` before logging structured payloads. Sensitive keys such as `password`, `otp`, `token`, `authorization`, `secret`, and file/image fields are replaced with `[REDACTED]`.

Do not log request bodies, raw authorization headers, OTP codes, uploaded files, or password reset data.

## Health Signals

- `/api/v1/health/` verifies the service can answer requests.
- `/api/v1/health/db/` verifies database connectivity.
- `/api/v1/health/ready/` verifies database and cache availability.

Readiness returns the existing unified response envelope and does not expose connection strings or credentials.

## Future Integrations

Recommended next steps once the production runtime is selected:

- Add external error tracking with request ID tagging.
- Add database slow-query monitoring at the platform or PostgreSQL layer.
- Add uptime checks against `/api/v1/health/ready/`.
- Add log retention and alert routing in the hosting provider.
