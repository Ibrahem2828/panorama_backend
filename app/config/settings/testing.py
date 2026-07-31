import tempfile
from pathlib import Path
from secrets import token_urlsafe

from .base import *  # noqa: F403
from .base import MIDDLEWARE as BASE_MIDDLEWARE
from .storage import build_storage_settings

DEBUG = True
SECRET_KEY = token_urlsafe(48)
RETURN_DEVELOPMENT_OTP = True

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

MIDDLEWARE = [
    middleware for middleware in BASE_MIDDLEWARE if middleware != "whitenoise.middleware.WhiteNoiseMiddleware"
]

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# Tests must never write fixture uploads into the repository's operational media directory.
MEDIA_ROOT = Path(tempfile.gettempdir()) / "panorama-test-media"
MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
globals().update(
    build_storage_settings(
        base_dir=BASE_DIR,  # noqa: F405
        static_root=STATIC_ROOT,  # noqa: F405
        staticfiles_backend=STATICFILES_STORAGE,  # noqa: F405
        media_root=MEDIA_ROOT,
    )
)
