# Panorama Backend Security Policy

## Reporting a vulnerability

Send vulnerability reports privately to `panoramacompany31@gmail.com`. Do not include student-card images, credentials, OTP values, access tokens, or private file links in ordinary email. Provide a minimal reproduction, affected endpoint, expected impact, and a safe contact method.

## Secrets

- Never commit `.env`, Gmail App Passwords, database credentials, JWT signing keys, Redis credentials, storage keys, or encryption keys.
- `EMAIL_HOST_PASSWORD` must be a Gmail App Password created after enabling two-step verification; it is not the normal Gmail password.
- Production must define a unique `FIELD_ENCRYPTION_KEY`, a strong `SECRET_KEY`, and private object-storage credentials.

## Security acceptance gate

A release is not production-approved until CI, migrations, automated tests, dependency audit, staging smoke tests, backup/restore validation, load tests, and an independent security review have passed.
