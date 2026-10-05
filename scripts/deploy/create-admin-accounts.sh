#!/usr/bin/env bash
# Run ON THE SERVER after a successful deploy. Creates the IT Support, Admin and Print Staff accounts in the
# new `panorama` database, reusing the e-mail, phone and name of the corresponding accounts in the OLD
# `postgres` database, with freshly generated random passwords.
#
# Passwords are written ONLY to /root/panorama-credentials.txt (mode 600) and are never printed.
# Read the file, change the passwords from the dashboard, then delete the file.
set -euo pipefail

IMAGE_TAG="${1:?usage: create-admin-accounts.sh <image-tag, e.g. 92ef103>}"
APP=/opt/panorama/backend
OLD_DB_CONTAINER=e6340upm0y8v73v3b9ni6yp5
OUT=/root/panorama-credentials.txt

[ ! -e "$OUT" ] || { echo "$OUT already exists; move it first"; exit 1; }
umask 077

old() { docker exec "$OLD_DB_CONTAINER" psql -U postgres -d postgres -At -F '|' -c "$1"; }
pw()  { head -c 24 /dev/urandom | base64 | tr -d '/+=' | cut -c1-20; }

# role -> (first active account of that role in the old database)
IFS='|' read -r IT_EMAIL IT_PHONE IT_NAME < <(old "select email,phone_number,full_name from accounts_user where role='it_support' and is_active order by id limit 1")
IFS='|' read -r AD_EMAIL AD_PHONE AD_NAME < <(old "select email,phone_number,full_name from accounts_user where role='admin' and is_active order by id limit 1")
IFS='|' read -r PR_EMAIL PR_PHONE PR_NAME < <(old "select email,phone_number,full_name from accounts_user where role='print_staff' and is_active order by id limit 1")

IT_PW=$(pw); AD_PW=$(pw); PR_PW=$(pw)

docker run --rm --network coolify --env-file "$APP/.env" \
  -e DJANGO_SETTINGS_MODULE=config.settings.production -e PORT=8000 -e HEALTHCHECK_HOST=api.xn--mgbaab0cxheq.tech \
  -e DJANGO_SUPERUSER_EMAIL="$IT_EMAIL" -e DJANGO_SUPERUSER_PHONE="$IT_PHONE" -e DJANGO_SUPERUSER_FULL_NAME="$IT_NAME" -e DJANGO_SUPERUSER_PASSWORD="$IT_PW" \
  -e DASHBOARD_ADMIN_EMAIL="$AD_EMAIL" -e DASHBOARD_ADMIN_PHONE="$AD_PHONE" -e DASHBOARD_ADMIN_FULL_NAME="$AD_NAME" -e DASHBOARD_ADMIN_PASSWORD="$AD_PW" \
  -e PRINT_STAFF_EMAIL="$PR_EMAIL" -e PRINT_STAFF_PHONE="$PR_PHONE" -e PRINT_STAFF_FULL_NAME="$PR_NAME" -e PRINT_STAFF_PASSWORD="$PR_PW" \
  "panorama-backend:$IMAGE_TAG" python manage.py setup_admin_accounts

{
  echo "Panorama accounts (created $(date -u +%FT%TZ)). Change these passwords, then delete this file."
  echo "IT Support : $IT_EMAIL / $IT_PW"
  echo "Admin      : $AD_EMAIL / $AD_PW"
  echo "Print staff: $PR_EMAIL / $PR_PW"
} > "$OUT"
chmod 600 "$OUT"
echo "Done. Credentials are in $OUT (not printed here)."

# The first release also seeded the idempotent production defaults; switch that flag back off.
sed -i 's/^RUN_SEED_PRODUCTION_DEFAULTS=.*/RUN_SEED_PRODUCTION_DEFAULTS=False/' "$APP/.env"
