"""Settings for running the full stack locally or in CI (backend + web + dashboard) without external services.

Never used in production: it returns development OTPs, keeps data in a local SQLite file and relaxes throttles.
"""

import os

from .testing import *  # noqa: F403
from .testing import BASE_DIR, REST_FRAMEWORK

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("E2E_DB_PATH", str(BASE_DIR / "e2e.sqlite3")),
    }
}

ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
CORS_ALLOWED_ORIGINS = [
    "http://127.0.0.1:3000",
    "http://localhost:3000",
    "http://127.0.0.1:3100",
    "http://localhost:3100",
]
CORS_ALLOW_CREDENTIALS = True
CELERY_TASK_ALWAYS_EAGER = True
OTP_EMAIL_ASYNC = False
RETURN_DEVELOPMENT_OTP = True

# Journeys register and log in many times from one address; keep the limits but make them roomy.
_rates: dict[str, str] = REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]  # type: ignore[assignment]
REST_FRAMEWORK = {**REST_FRAMEWORK, "DEFAULT_THROTTLE_RATES": {name: "100000/hour" for name in _rates}}
