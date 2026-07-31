"""Storage configuration shared by every Panorama settings module.

The deployed platform currently uses a private, persistent local volume. File
models and protected download endpoints depend on Django's storage interface,
which keeps a later generic S3-compatible migration possible without an API or
model rewrite.
"""

from __future__ import annotations

import logging
import tempfile
import warnings
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from .env import get_bool_env, get_env

storage_logger = logging.getLogger(__name__)
_legacy_warnings_emitted: set[str] = set()

LOCAL_STORAGE_BACKEND = "apps.common.storage.PrivateFileSystemStorage"
SUPPORTED_STORAGE_BACKENDS = frozenset({"local", "s3"})
S3_ENVIRONMENT_VARIABLES = (
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
    "S3_BUCKET_NAME",
    "S3_ENDPOINT_URL",
    "S3_REGION",
    "S3_SIGNATURE_VERSION",
)


def _warn_legacy_use_s3_storage(message: str) -> None:
    """Emit a visible, value-free warning for the one-release compatibility path."""

    if message in _legacy_warnings_emitted:
        return
    _legacy_warnings_emitted.add(message)
    storage_logger.warning(message)
    warnings.warn(message, DeprecationWarning, stacklevel=3)


def resolve_storage_backend() -> str:
    """Return the explicit storage mode without selecting a cloud provider implicitly."""

    configured_backend = get_env("STORAGE_BACKEND")
    if configured_backend:
        storage_backend = configured_backend.strip().lower()
        if storage_backend not in SUPPORTED_STORAGE_BACKENDS:
            supported = ", ".join(sorted(SUPPORTED_STORAGE_BACKENDS))
            raise ImproperlyConfigured(
                f"Unsupported STORAGE_BACKEND: {storage_backend}. Supported values: {supported}."
            )
        return storage_backend

    # USE_S3_STORAGE is deprecated and retained for one release only. It is
    # never a provider selector and must not silently activate a cloud backend.
    if get_env("USE_S3_STORAGE") is not None:
        if get_bool_env("USE_S3_STORAGE", default=False):
            message = (
                "USE_S3_STORAGE=True is deprecated and no longer selects a storage provider. "
                "Set STORAGE_BACKEND=s3 and configure the generic S3_* variables explicitly."
            )
            _warn_legacy_use_s3_storage(message)
            raise ImproperlyConfigured(message)
        _warn_legacy_use_s3_storage(
            "USE_S3_STORAGE=False is deprecated; treating it as STORAGE_BACKEND=local for this release."
        )

    return "local"


def collect_missing_s3_environment_variables() -> list[str]:
    """Return names only; callers must never log storage credential values."""

    return [name for name in S3_ENVIRONMENT_VARIABLES if not get_env(name)]


def _resolved_media_root(base_dir: Path, configured_media_root: Path | None = None) -> Path:
    raw_media_root = configured_media_root or Path(get_env("MEDIA_ROOT", default=str(base_dir / "media")) or "")
    if not raw_media_root:
        raise ImproperlyConfigured("MEDIA_ROOT must not be empty.")
    return raw_media_root.expanduser().resolve()


def _validated_media_url() -> str:
    media_url = get_env("MEDIA_URL", default="/media/") or "/media/"
    if not media_url.startswith("/") or not media_url.endswith("/") or "://" in media_url:
        raise ImproperlyConfigured("MEDIA_URL must be a relative URL path beginning and ending with '/'.")
    return media_url


def build_storage_settings(
    *,
    base_dir: Path,
    static_root: Path,
    staticfiles_backend: str,
    media_root: Path | None = None,
    enforce_persistent_local_path: bool = False,
) -> dict[str, object]:
    """Build the complete Django ``STORAGES`` mapping for the selected mode."""

    storage_backend = resolve_storage_backend()
    resolved_media_root = _resolved_media_root(base_dir, media_root)
    resolved_static_root = static_root.expanduser().resolve()

    if resolved_media_root == resolved_static_root or resolved_static_root in resolved_media_root.parents:
        raise ImproperlyConfigured("MEDIA_ROOT must be separate from STATIC_ROOT and must not live beneath it.")
    temporary_root = Path(tempfile.gettempdir()).resolve()
    if enforce_persistent_local_path and (
        resolved_media_root == temporary_root or temporary_root in resolved_media_root.parents
    ):
        raise ImproperlyConfigured("Production MEDIA_ROOT must be persistent and must not be located under /tmp.")

    if storage_backend == "s3":
        missing = collect_missing_s3_environment_variables()
        if missing:
            raise ImproperlyConfigured("Missing S3 storage environment variables: " + ", ".join(sorted(missing)))
        raise ImproperlyConfigured(
            "STORAGE_BACKEND=s3 is reserved for a future generic S3-compatible adapter. "
            "This release ships only STORAGE_BACKEND=local; install and validate the adapter before enabling s3."
        )

    media_url = _validated_media_url()
    return {
        "STORAGE_BACKEND": storage_backend,
        "MEDIA_ROOT": resolved_media_root,
        "MEDIA_URL": media_url,
        "STORAGES": {
            "default": {
                "BACKEND": LOCAL_STORAGE_BACKEND,
                "OPTIONS": {
                    "location": str(resolved_media_root),
                    "base_url": media_url,
                },
            },
            "staticfiles": {"BACKEND": staticfiles_backend},
        },
    }
