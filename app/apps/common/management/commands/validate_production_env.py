"""Validate the production environment without ever printing secret values."""

from __future__ import annotations

import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

BASE_REQUIRED = (
    "SECRET_KEY",
    "FIELD_ENCRYPTION_KEY",
    "ALLOWED_HOSTS",
    "CSRF_TRUSTED_ORIGINS",
    "CORS_ALLOWED_ORIGINS",
    "DATABASE_URL",
    "REDIS_URL",
    "EMAIL_HOST",
    "EMAIL_HOST_USER",
    "EMAIL_HOST_PASSWORD",
    "APP_BASE_URL",
)
S3_REQUIRED = (
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
    "S3_BUCKET_NAME",
    "S3_ENDPOINT_URL",
    "S3_REGION",
    "S3_SIGNATURE_VERSION",
)
WEB_PUSH_VARS = ("WEB_PUSH_VAPID_PUBLIC_KEY", "WEB_PUSH_VAPID_PRIVATE_KEY", "WEB_PUSH_VAPID_SUBJECT")


def web_push_warnings() -> list[str]:
    """Advisory only: browser push is optional, so a missing or partial configuration never fails the release."""

    present = [name for name in WEB_PUSH_VARS if os.environ.get(name, "").strip()]
    if not present:
        return ["Browser Web Push is disabled (WEB_PUSH_VAPID_* not set)."]
    warnings = []
    if len(present) != len(WEB_PUSH_VARS):
        missing = sorted(set(WEB_PUSH_VARS) - set(present))
        warnings.append("Browser Web Push is disabled: incomplete configuration, missing " + ", ".join(missing) + ".")
    subject = os.environ.get("WEB_PUSH_VAPID_SUBJECT", "").strip()
    if subject and not subject.startswith(("mailto:", "https://")):
        warnings.append("WEB_PUSH_VAPID_SUBJECT should be a mailto: or https:// contact URI.")
    return warnings


class Command(BaseCommand):
    help = "Report missing production environment variable names only; secrets are never displayed."

    def handle(self, *args, **options):
        missing = [name for name in BASE_REQUIRED if not os.environ.get(name, "").strip()]
        storage_backend = str(getattr(settings, "STORAGE_BACKEND", "local")).lower()
        if storage_backend == "s3":
            missing.extend(name for name in S3_REQUIRED if not os.environ.get(name, "").strip())
        if storage_backend not in {"local", "s3"}:
            missing.append("STORAGE_BACKEND(valid value local or s3)")
        if missing:
            raise CommandError("Missing or invalid production configuration: " + ", ".join(sorted(set(missing))))
        for warning in web_push_warnings():
            self.stdout.write(self.style.WARNING(f"warning: {warning}"))
        self.stdout.write(
            self.style.SUCCESS(
                f"Production environment validation passed (storage_backend={storage_backend}; values redacted)."
            )
        )
