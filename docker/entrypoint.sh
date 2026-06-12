#!/bin/sh
set -e

cd /app/app

is_true() {
  case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
    1|true|t|yes|y|on) return 0 ;;
    *) return 1 ;;
  esac
}

wait_for_database() {
  python - <<'PY'
import os
import socket
import time
from urllib.parse import urlparse

timeout = int(os.environ.get("WAIT_TIMEOUT_SECONDS", "60"))
database_url = os.environ.get("DATABASE_URL", "")
if database_url:
    parsed = urlparse(database_url)
    host = parsed.hostname
    port = parsed.port or 5432
else:
    host = os.environ.get("DB_HOST")
    port = int(os.environ.get("DB_PORT", "5432"))

if not host:
    raise SystemExit("Database host is not configured.")

deadline = time.time() + timeout
while True:
    try:
        with socket.create_connection((host, port), timeout=3):
            break
    except OSError:
        if time.time() >= deadline:
            raise SystemExit("Database did not become reachable before timeout.")
        time.sleep(2)
PY
}

wait_for_redis() {
  python - <<'PY'
import os
import socket
import time
from urllib.parse import urlparse

timeout = int(os.environ.get("WAIT_TIMEOUT_SECONDS", "60"))
redis_url = os.environ.get("REDIS_URL", "")
parsed = urlparse(redis_url)
host = parsed.hostname
port = parsed.port or 6379

if not host:
    raise SystemExit("Redis host is not configured.")

deadline = time.time() + timeout
while True:
    try:
        with socket.create_connection((host, port), timeout=3):
            break
    except OSError:
        if time.time() >= deadline:
            raise SystemExit("Redis did not become reachable before timeout.")
        time.sleep(2)
PY
}

if is_true "${WAIT_FOR_DATABASE:-true}"; then
  echo "Waiting for database..."
  wait_for_database
fi

if is_true "${WAIT_FOR_REDIS:-true}"; then
  echo "Waiting for Redis..."
  wait_for_redis
fi

if is_true "${RUN_DEPLOY_CHECK:-true}"; then
  python manage.py check --deploy
fi

if is_true "${RUN_MIGRATIONS:-true}"; then
  python manage.py migrate --noinput
fi

if is_true "${RUN_COLLECTSTATIC:-true}"; then
  python manage.py collectstatic --noinput
fi

if is_true "${RUN_SETUP_ADMIN_ACCOUNTS:-true}"; then
  python manage.py setup_admin_accounts
fi

if is_true "${RUN_SEED_DATA:-false}"; then
  python manage.py seed_initial_data
fi

exec "$@"
