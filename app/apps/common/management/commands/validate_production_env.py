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
        self.stdout.write(
            self.style.SUCCESS(
                f"Production environment validation passed (storage_backend={storage_backend}; values redacted)."
            )
        )
