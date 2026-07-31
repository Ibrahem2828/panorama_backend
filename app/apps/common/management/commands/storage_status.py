from __future__ import annotations

import os
import shutil
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Report safe storage status; --write-test verifies a temporary storage round trip."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--write-test",
            action="store_true",
            help="Save, read, and delete a randomly named healthcheck object.",
        )

    def handle(self, *args, **options) -> None:
        storage_mode = getattr(settings, "STORAGE_BACKEND", "local")
        media_root = Path(settings.MEDIA_ROOT)
        directory_exists = media_root.exists()
        readable = False
        writable = False
        if directory_exists and media_root.is_dir():
            readable = os.access(media_root, os.R_OK)
            writable = os.access(media_root, os.W_OK)

        self.stdout.write(f"storage_backend={default_storage.__class__.__name__}")
        self.stdout.write(f"storage_mode={storage_mode}")
        self.stdout.write(f"media_directory_exists={str(directory_exists).lower()}")
        self.stdout.write(f"media_directory_readable={str(readable).lower()}")
        self.stdout.write(f"media_directory_writable={str(writable).lower()}")
        if storage_mode == "local" and directory_exists and media_root.is_dir():
            self.stdout.write(f"free_disk_bytes={shutil.disk_usage(media_root).free}")
        else:
            self.stdout.write("free_disk_bytes=unavailable")

        if not options["write_test"]:
            return
        if not (directory_exists and readable and writable):
            raise CommandError("Local media storage is not available for a write test.")

        object_name = f"healthchecks/storage-status-{uuid4().hex}.tmp"
        saved_name: str | None = None
        try:
            saved_name = default_storage.save(object_name, ContentFile(b"panorama-storage-status"))
            if not default_storage.exists(saved_name):
                raise CommandError("Storage write test could not find the saved object.")
            with default_storage.open(saved_name, "rb") as stored_file:
                if stored_file.read() != b"panorama-storage-status":
                    raise CommandError("Storage write test read unexpected content.")
            default_storage.delete(saved_name)
            if default_storage.exists(saved_name):
                raise CommandError("Storage write test could not delete the temporary object.")
        except CommandError:
            raise
        except Exception as exc:  # Do not include paths, provider details, or credentials in output.
            raise CommandError(f"Storage write test failed: {type(exc).__name__}") from exc
        finally:
            if saved_name and default_storage.exists(saved_name):
                default_storage.delete(saved_name)

        self.stdout.write(self.style.SUCCESS("write_test=passed"))
