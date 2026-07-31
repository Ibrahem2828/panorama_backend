# Required Security Rotation

The local `.env` file and operational media files are not release artifacts.
Before deploying this revision, rotate every secret that may have appeared in a
previous delivery bundle, CI log, backup, or developer machine. Do not record
values, screenshots, or generated replacements in this repository.

Rotate and revoke, where applicable:

- `SECRET_KEY`
- `FIELD_ENCRYPTION_KEY`
- `DATABASE_URL` and the underlying PostgreSQL password
- `REDIS_URL` and the underlying Redis password
- `EMAIL_HOST_PASSWORD`
- Any legacy cloud-storage credential that was ever issued (the current local
  storage release does not use or require one)
- `FCM_SERVER_KEY`
- `EXPO_ACCESS_TOKEN`
- JWT signing material if it is configured independently
- Every administrator, dashboard, print-staff, and bootstrap-account password

Store replacements only in the deployment secret manager. Verify that the
running service starts with the new values, then invalidate old credentials and
review access logs for their possible use.
