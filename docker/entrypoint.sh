#!/bin/sh
set -eu

cd /app/app

# Runtime container starts the ASGI process only by default. Database schema
# changes belong in the release job to avoid races when replicas scale out.
if [ "${RUN_RUNTIME_MAINTENANCE:-False}" = "True" ]; then
  echo "WARNING: RUN_RUNTIME_MAINTENANCE is enabled; use only for a single-replica first deployment."
  python manage.py migrate --noinput
  python manage.py collectstatic --noinput
fi

exec "$@"
