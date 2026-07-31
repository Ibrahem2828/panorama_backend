from __future__ import annotations

import json
import shutil
import subprocess
import tempfile

from django.core.management.base import BaseCommand

from apps.lectures.document_pipeline import document_pipeline_capabilities


class Command(BaseCommand):
    help = "Report safe document-pipeline capabilities without processing a document."

    @staticmethod
    def _version(binary: str) -> str:
        try:
            result = subprocess.run(
                [binary, "--version"],
                check=False,
                capture_output=True,
                shell=False,
                timeout=5,
                text=True,
            )
        except (OSError, subprocess.TimeoutExpired):
            return "unavailable"
        if result.returncode != 0:
            return "unavailable"
        return (result.stdout or result.stderr).splitlines()[0][:160] or "available"

    def handle(self, *args, **options):
        capabilities = document_pipeline_capabilities()
        soffice = shutil.which("soffice")
        with tempfile.TemporaryDirectory(prefix="panorama-pipeline-status-") as directory:
            capabilities["temporary_directory_writable"] = bool(directory)
        capabilities["libreoffice_version"] = self._version(soffice) if soffice else "unavailable"
        capabilities["worker_queue"] = "conversion"
        capabilities.pop("libreoffice_path", None)
        capabilities.pop("poppler_path", None)
        self.stdout.write(json.dumps(capabilities, sort_keys=True))
