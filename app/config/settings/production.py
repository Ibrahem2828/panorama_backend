from cryptography.fernet import Fernet
from decouple import config
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .env import database_from_url, get_bool_env, get_csv_env, require_env
from .storage import build_storage_settings

SECRET_KEY = require_env("SECRET_KEY")
if len(SECRET_KEY) < 50:
    raise ImproperlyConfigured("Production SECRET_KEY must be at least 50 characters and generated randomly.")
DEBUG = get_bool_env("DEBUG", "DJANGO_DEBUG", default=False)
RETURN_DEVELOPMENT_OTP = False
API_DOCS_ENABLED = get_bool_env("API_DOCS_ENABLED", default=False)
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

database_url = require_env("DATABASE_URL")
DATABASE_SSL_REQUIRE = get_bool_env("DATABASE_SSL_REQUIRE", default=True)
DATABASES["default"] = database_from_url(database_url, ssl_require=DATABASE_SSL_REQUIRE)  # noqa: F405

REDIS_URL = require_env("REDIS_URL")
CACHES["default"]["LOCATION"] = REDIS_URL  # noqa: F405
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
SECURE_SSL_REDIRECT = get_bool_env("SECURE_SSL_REDIRECT", "DJANGO_SECURE_SSL_REDIRECT", default=True)
SECURE_HSTS_INCLUDE_SUBDOMAINS = get_bool_env("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=True)
SECURE_HSTS_PRELOAD = get_bool_env("SECURE_HSTS_PRELOAD", default=True)
SECURE_HSTS_SECONDS = int(require_env("SECURE_HSTS_SECONDS"))
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = get_bool_env("USE_X_FORWARDED_HOST", "DJANGO_USE_X_FORWARDED_HOST", default=True)
SESSION_COOKIE_SECURE = get_bool_env("SESSION_COOKIE_SECURE", "DJANGO_SESSION_COOKIE_SECURE", default=True)
CSRF_COOKIE_SECURE = get_bool_env("CSRF_COOKIE_SECURE", "DJANGO_CSRF_COOKIE_SECURE", default=True)
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

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "no-referrer"

FIELD_ENCRYPTION_KEY = require_env("FIELD_ENCRYPTION_KEY")
try:
    Fernet(FIELD_ENCRYPTION_KEY.encode("ascii"))
except Exception as exc:  # noqa: BLE001
    raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY must be a valid Fernet key.") from exc
EMAIL_HOST = require_env("EMAIL_HOST")
EMAIL_HOST_USER = require_env("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = require_env("EMAIL_HOST_PASSWORD")

# The only enabled production mode in this release is local storage backed by
# the named Coolify volume at /app/app/media. Generic S3 is validated only
# when explicitly selected and never defaults to a particular provider.
globals().update(
    build_storage_settings(
        base_dir=BASE_DIR,  # noqa: F405
        static_root=STATIC_ROOT,  # noqa: F405
        staticfiles_backend=STATICFILES_STORAGE,  # noqa: F405
        enforce_persistent_local_path=True,
    )
)

LOG_LEVEL = config("LOG_LEVEL", default="INFO").upper()
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "console": {
            "()": "apps.common.logging.JSONFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "console",
            "filters": ["sensitive_data"],
        },
    },
    "filters": {"sensitive_data": {"()": "apps.common.logging.SensitiveDataFilter"}},
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
