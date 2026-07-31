"""Provider-neutral storage primitives for private user uploads."""

from __future__ import annotations

import posixpath
from os import PathLike, fspath
from pathlib import PurePosixPath
from uuid import uuid4

from django.core.exceptions import SuspiciousFileOperation
from django.core.files.storage import FileSystemStorage


class PrivateFileSystemStorage(FileSystemStorage):
    """Local storage that retains the logical folder but never trusts upload names."""

    def generate_filename(self, filename: str | PathLike[str]) -> str:
        normalized = fspath(filename).replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts or chr(0) in normalized:
            raise SuspiciousFileOperation("Unsafe upload filename.")

        directory = str(path.parent)
        suffix = path.suffix.lower()
        if len(suffix) > 16 or any(not (character.isalnum() or character == ".") for character in suffix):
            suffix = ""
        generated_name = f"{uuid4().hex}{suffix}"
        return generated_name if directory == "." else posixpath.join(directory, generated_name)

    def save(self, name, content, max_length=None):
        """Randomize names for direct ``default_storage.save`` callers too."""

        return super().save(self.generate_filename(name), content, max_length=max_length)
