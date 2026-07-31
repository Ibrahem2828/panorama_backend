#!/bin/sh
set -eu
cd /app/app
python manage.py check --deploy
python manage.py validate_production_env
python manage.py migrate --noinput
python manage.py collectstatic --noinput
python manage.py spectacular --format openapi-json --validate --fail-on-warn --file /tmp/openapi.json
if [ "${RUN_SETUP_ADMIN_ACCOUNTS:-False}" = "True" ]; then
  python manage.py setup_admin_accounts
fi
if [ "${RUN_SEED_DATA:-False}" = "True" ]; then
  python manage.py seed_initial_data
fi
if [ "${RUN_SEED_PRODUCTION_DEFAULTS:-False}" = "True" ]; then
  python manage.py seed_production_defaults
fi
