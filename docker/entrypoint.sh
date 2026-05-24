#!/bin/sh
set -e

cd /app/app

if [ "${RUN_MIGRATIONS:-True}" = "True" ] || [ "${RUN_MIGRATIONS:-True}" = "true" ] || [ "${RUN_MIGRATIONS:-True}" = "1" ]; then
  python manage.py migrate --noinput
fi

if [ "${RUN_COLLECTSTATIC:-True}" = "True" ] || [ "${RUN_COLLECTSTATIC:-True}" = "true" ] || [ "${RUN_COLLECTSTATIC:-True}" = "1" ]; then
  python manage.py collectstatic --noinput
fi

if [ "${RUN_SETUP_ADMIN_ACCOUNTS:-True}" = "True" ] || [ "${RUN_SETUP_ADMIN_ACCOUNTS:-True}" = "true" ] || [ "${RUN_SETUP_ADMIN_ACCOUNTS:-True}" = "1" ]; then
  python manage.py setup_admin_accounts
fi

if [ "${RUN_SEED_DATA:-False}" = "True" ] || [ "${RUN_SEED_DATA:-False}" = "true" ] || [ "${RUN_SEED_DATA:-False}" = "1" ]; then
  python manage.py seed_initial_data
fi

exec "$@"
