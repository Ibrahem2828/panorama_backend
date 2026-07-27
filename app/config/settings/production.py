from decouple import config
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .env import database_from_url, get_bool_env, get_csv_env, get_env

SECRET_KEY = config("SECRET_KEY")
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

database_url = get_env("DATABASE_URL")
DATABASE_SSL_REQUIRE = get_bool_env("DATABASE_SSL_REQUIRE", default=True)
if database_url:
    DATABASES["default"] = database_from_url(database_url, ssl_require=DATABASE_SSL_REQUIRE)  # noqa: F405

SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_SSL_REDIRECT = get_bool_env("SECURE_SSL_REDIRECT", "DJANGO_SECURE_SSL_REDIRECT", default=True)
SECURE_HSTS_INCLUDE_SUBDOMAINS = get_bool_env("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=True)
SECURE_HSTS_PRELOAD = get_bool_env("SECURE_HSTS_PRELOAD", default=True)
SECURE_HSTS_SECONDS = config("SECURE_HSTS_SECONDS", default=31536000, cast=int)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = get_bool_env("USE_X_FORWARDED_HOST", "DJANGO_USE_X_FORWARDED_HOST", default=True)
SESSION_COOKIE_SECURE = get_bool_env("SESSION_COOKIE_SECURE", "DJANGO_SESSION_COOKIE_SECURE", default=True)
CSRF_COOKIE_SECURE = get_bool_env("CSRF_COOKIE_SECURE", "DJANGO_CSRF_COOKIE_SECURE", default=True)
CSRF_TRUSTED_ORIGINS = get_csv_env("CSRF_TRUSTED_ORIGINS", "DJANGO_CSRF_TRUSTED_ORIGINS", default=[])
CORS_ALLOWED_ORIGINS = get_csv_env("CORS_ALLOWED_ORIGINS", "DJANGO_CORS_ALLOWED_ORIGINS", default=[])

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "no-referrer"

FIELD_ENCRYPTION_KEY = get_env("FIELD_ENCRYPTION_KEY", default=FIELD_ENCRYPTION_KEY)  # noqa: F405
if not FIELD_ENCRYPTION_KEY:  # noqa: F405
    raise ImproperlyConfigured("Production requires FIELD_ENCRYPTION_KEY for encrypted secrets.")
EMAIL_HOST_PASSWORD = get_env("EMAIL_HOST_PASSWORD", default=EMAIL_HOST_PASSWORD)  # noqa: F405
if EMAIL_BACKEND.endswith("smtp.EmailBackend") and not EMAIL_HOST_PASSWORD:  # noqa: F405
    raise ImproperlyConfigured("Production SMTP email delivery requires EMAIL_HOST_PASSWORD.")

USE_S3_STORAGE = get_bool_env("USE_S3_STORAGE", default=False)
if USE_S3_STORAGE:
    INSTALLED_APPS += ["storages"]  # noqa: F405
    AWS_ACCESS_KEY_ID = config("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = config("AWS_SECRET_ACCESS_KEY")
    AWS_STORAGE_BUCKET_NAME = config("AWS_STORAGE_BUCKET_NAME")
    AWS_S3_ENDPOINT_URL = config("AWS_S3_ENDPOINT_URL", default="") or None
    AWS_S3_REGION_NAME = config("AWS_S3_REGION_NAME", default="auto")
    AWS_QUERYSTRING_AUTH = True
    AWS_QUERYSTRING_EXPIRE = config("AWS_QUERYSTRING_EXPIRE", default=120, cast=int)
    AWS_DEFAULT_ACL = None
    AWS_S3_FILE_OVERWRITE = False
    STORAGES = {
        "default": {"BACKEND": "storages.backends.s3.S3Storage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }

LOG_LEVEL = config("LOG_LEVEL", default="INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "console": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "console",
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
