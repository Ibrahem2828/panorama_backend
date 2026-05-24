from decouple import config
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .env import database_from_url, get_bool_env, get_csv_env, get_env

SECRET_KEY = config("SECRET_KEY")
DEBUG = get_bool_env("DEBUG", "DJANGO_DEBUG", default=False)
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

SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_SSL_REDIRECT = get_bool_env("SECURE_SSL_REDIRECT", "DJANGO_SECURE_SSL_REDIRECT", default=False)
SECURE_HSTS_INCLUDE_SUBDOMAINS = get_bool_env("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False)
SECURE_HSTS_PRELOAD = get_bool_env("SECURE_HSTS_PRELOAD", default=False)
SECURE_HSTS_SECONDS = config("SECURE_HSTS_SECONDS", default=0, cast=int)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = get_bool_env("USE_X_FORWARDED_HOST", "DJANGO_USE_X_FORWARDED_HOST", default=True)
SESSION_COOKIE_SECURE = get_bool_env("SESSION_COOKIE_SECURE", "DJANGO_SESSION_COOKIE_SECURE", default=False)
CSRF_COOKIE_SECURE = get_bool_env("CSRF_COOKIE_SECURE", "DJANGO_CSRF_COOKIE_SECURE", default=False)
CSRF_TRUSTED_ORIGINS = get_csv_env("CSRF_TRUSTED_ORIGINS", "DJANGO_CSRF_TRUSTED_ORIGINS", default=[])
CORS_ALLOWED_ORIGINS = get_csv_env("CORS_ALLOWED_ORIGINS", "DJANGO_CORS_ALLOWED_ORIGINS", default=[])

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
