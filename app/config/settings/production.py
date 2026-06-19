from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .env import database_from_url, get_bool_env, get_csv_env, get_env, get_int_env, get_required_env

UNSAFE_SECRET_KEYS = {
    "unsafe-development-secret-key",
    "change-me",
    "changeme",
    "change_me",
    "replace-me",
    "replace-with-a-generated-django-secret-key",
}


def _require_env(name: str, *, message: str | None = None) -> str:
    return get_required_env(name, message=message)


SECRET_KEY = _require_env("SECRET_KEY", message="Production requires SECRET_KEY.")
if SECRET_KEY.strip().lower() in UNSAFE_SECRET_KEYS:
    raise ImproperlyConfigured("Production SECRET_KEY must be replaced with a generated secret.")

DEBUG = get_bool_env("DEBUG", "DJANGO_DEBUG", default=False)
if DEBUG:
    raise ImproperlyConfigured("Production DEBUG must be False.")

RETURN_DEVELOPMENT_OTP = False
ALLOWED_HOSTS = get_csv_env(
    "ALLOWED_HOSTS",
    "DJANGO_ALLOWED_HOSTS",
    required_message=(
        "Production requires ALLOWED_HOSTS or DJANGO_ALLOWED_HOSTS. "
        "Example: ALLOWED_HOSTS=api.example.com,localhost,127.0.0.1"
    ),
)

if "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("Production ALLOWED_HOSTS must not contain '*'. Configure explicit hostnames.")

database_url = get_env("DATABASE_URL")
DATABASE_SSL_REQUIRE = get_bool_env("DATABASE_SSL_REQUIRE", default=False)
if database_url:
    DATABASES["default"] = database_from_url(database_url, ssl_require=DATABASE_SSL_REQUIRE)  # noqa: F405
else:
    for db_env_name in ("DB_NAME", "DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT"):
        _require_env(db_env_name, message=f"Production requires DATABASE_URL or {db_env_name}.")

REDIS_URL = _require_env("REDIS_URL", message="Production requires REDIS_URL for cache, throttling, Celery, and Channels.")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "TIMEOUT": get_int_env("CACHE_DEFAULT_TIMEOUT", default=300),
    }
}
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [REDIS_URL]},
    }
}

SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = get_env("SECURE_REFERRER_POLICY", default="same-origin")
X_FRAME_OPTIONS = get_env("X_FRAME_OPTIONS", default="DENY")
# These flags are environment-driven because some managed platforms terminate
# TLS before Django. Use True when the application directly sees HTTPS.
SECURE_SSL_REDIRECT = get_bool_env("SECURE_SSL_REDIRECT", "DJANGO_SECURE_SSL_REDIRECT", default=False)
SECURE_HSTS_INCLUDE_SUBDOMAINS = get_bool_env("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False)
SECURE_HSTS_PRELOAD = get_bool_env("SECURE_HSTS_PRELOAD", default=False)
SECURE_HSTS_SECONDS = get_int_env("SECURE_HSTS_SECONDS", default=0)
if get_bool_env("SECURE_PROXY_SSL_HEADER", "DJANGO_SECURE_PROXY_SSL_HEADER", default=True):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
else:
    SECURE_PROXY_SSL_HEADER = None
USE_X_FORWARDED_HOST = get_bool_env("USE_X_FORWARDED_HOST", "DJANGO_USE_X_FORWARDED_HOST", default=True)
SESSION_COOKIE_SECURE = get_bool_env("SESSION_COOKIE_SECURE", "DJANGO_SESSION_COOKIE_SECURE", default=False)
CSRF_COOKIE_SECURE = get_bool_env("CSRF_COOKIE_SECURE", "DJANGO_CSRF_COOKIE_SECURE", default=False)
CSRF_TRUSTED_ORIGINS = get_csv_env(
    "CSRF_TRUSTED_ORIGINS",
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    required_message="Production requires CSRF_TRUSTED_ORIGINS or DJANGO_CSRF_TRUSTED_ORIGINS.",
)
CORS_ALLOWED_ORIGINS = get_csv_env(
    "CORS_ALLOWED_ORIGINS",
    "DJANGO_CORS_ALLOWED_ORIGINS",
    required_message="Production requires CORS_ALLOWED_ORIGINS or DJANGO_CORS_ALLOWED_ORIGINS.",
)

LOG_LEVEL = get_env("LOG_LEVEL", default="INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {
            "()": "apps.common.logging.RequestIDLogFilter",
        },
    },
    "formatters": {
        "console": {
            "format": "%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "console",
            "filters": ["request_id"],
        },
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
    },
}
