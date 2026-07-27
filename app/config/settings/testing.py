from .base import *  # noqa: F403

DEBUG = True
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
    middleware
    for middleware in MIDDLEWARE
    if middleware != "whitenoise.middleware.WhiteNoiseMiddleware"
]

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
