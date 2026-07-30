import os
from urllib.parse import unquote, urlparse

from decouple import UndefinedValueError, config
from django.core.exceptions import ImproperlyConfigured

TRUE_VALUES = {"1", "true", "t", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "f", "no", "n", "off"}


def get_env(primary_name: str, fallback_name: str | None = None, default: str | None = None) -> str | None:
    for name in (primary_name, fallback_name):
        if name and os.environ.get(name, "").strip():
            return os.environ[name].strip()
        if name:
            try:
                value = config(name)
            except UndefinedValueError:
                continue
            if str(value).strip():
                return str(value).strip()
    return default


def require_env(primary_name: str, fallback_name: str | None = None, *, message: str | None = None) -> str:
    """Return a non-empty setting or fail before the application starts."""

    value = get_env(primary_name, fallback_name)
    if value:
        return value
    names = " or ".join(name for name in (primary_name, fallback_name) if name)
    raise ImproperlyConfigured(message or f"Production requires {names}.")


def get_bool_env(primary_name: str, fallback_name: str | None = None, default: bool = False) -> bool:
    value = get_env(primary_name, fallback_name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return default


def get_csv_env(
    primary_name: str,
    fallback_name: str | None = None,
    default: list[str] | None = None,
    *,
    required_message: str | None = None,
) -> list[str]:
    value = get_env(primary_name, fallback_name)
    if value is None:
        if default is not None:
            return default
        if required_message:
            raise ImproperlyConfigured(required_message)
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def database_from_url(database_url: str, *, ssl_require: bool = False) -> dict:
    parsed = urlparse(database_url)
    engine_by_scheme = {
        "postgres": "django.db.backends.postgresql",
        "postgresql": "django.db.backends.postgresql",
    }
    engine = engine_by_scheme.get(parsed.scheme)
    if not engine:
        raise ImproperlyConfigured(
            "DATABASE_URL must use a supported scheme. Supported schemes: postgres, postgresql."
        )
    if not parsed.hostname or not parsed.path.strip("/"):
        raise ImproperlyConfigured("DATABASE_URL must include host and database name.")

    database = {
        "ENGINE": engine,
        "NAME": unquote(parsed.path.lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname,
        "PORT": str(parsed.port or 5432),
    }
    if ssl_require:
        database["OPTIONS"] = {"sslmode": "require"}
    return database
