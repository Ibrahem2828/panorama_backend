#!/usr/bin/env bash
# Redeploy the Panorama backend on the Docker host once deploy-backend.sh has already run (the Coolify container is gone).
#
# Ship the source, then run this ON THE SERVER as root:
#   git -C projects/backend archive origin/main | ssh root@76.13.155.172 \
#     'rm -rf /opt/panorama/backend/src.new && mkdir -p /opt/panorama/backend/src.new && tar -x -C /opt/panorama/backend/src.new'
#   ssh root@76.13.155.172 'bash -s -- <git-sha>' < scripts/deploy/redeploy-backend.sh
#
# Order: database backup -> swap source -> build -> release job (check --deploy, migrations, collectstatic) -> start the new
# image -> wait for /health/ready/ three times in a row -> otherwise restart the previous image tag.
# Migrations that were already applied stay applied (the pre-deploy dump is the way back for a schema change).
set -euo pipefail

SHA="${1:?usage: redeploy-backend.sh <git-sha>}"
APP=/opt/panorama/backend
DOMAIN=api.xn--mgbaab0cxheq.tech
NET=coolify
P="docker compose -p panorama -f $APP/src/docker-compose.coolify.yml -f $APP/override.yml --env-file $APP/.env"

[ -d "$APP/src.new" ] || { echo "ship the source to $APP/src.new first"; exit 1; }
[ -f "$APP/.env" ] && [ -f "$APP/override.yml" ] || { echo "run deploy-backend.sh once first"; exit 1; }

PREVIOUS="$(grep '^IMAGE_TAG=' "$APP/.env" | cut -d= -f2)"
echo "previous image tag: $PREVIOUS; new: $SHA"

# 1. Backup first (the daily script verifies the dump before reporting success).
/opt/panorama/backup/backup-panorama.sh

# 2. Swap the source tree, keeping the old one until the deploy succeeds.
rm -rf "$APP/src.old"
mv "$APP/src" "$APP/src.old"
mv "$APP/src.new" "$APP/src"

setenv() { grep -v "^$1=" "$APP/.env" > "$APP/.env.tmp" || true; echo "$1=$2" >> "$APP/.env.tmp"; mv "$APP/.env.tmp" "$APP/.env"; chmod 600 "$APP/.env"; }

rollback() {
  echo "ROLLING BACK to $PREVIOUS"
  rm -rf "$APP/src"; mv "$APP/src.old" "$APP/src"
  setenv IMAGE_TAG "$PREVIOUS"
  $P up -d web worker beat </dev/null
}

setenv IMAGE_TAG "$SHA"
setenv BUILD_DATE "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# 3. Build and release. A failure here has not touched the running containers yet.
# </dev/null: the script itself arrives on stdin and `docker compose run` would swallow the rest of it.
if ! { $P build web </dev/null && $P --profile release run --rm release </dev/null; }; then
  echo "build or release job failed; the running containers were not touched"
  rm -rf "$APP/src"; mv "$APP/src.old" "$APP/src"
  setenv IMAGE_TAG "$PREVIOUS"
  exit 1
fi

# 4. Swap traffic (compose recreates only containers whose image changed).
$P up -d web worker beat </dev/null

# 5. Readiness: three consecutive 200s.
ok=0
for i in $(seq 1 36); do
  ip="$(docker inspect -f '{{(index .NetworkSettings.Networks "'$NET'").IPAddress}}' panorama-web-1 2>/dev/null || true)"
  code=000
  [ -n "$ip" ] && code=$(curl -s -o /dev/null -m 10 -w '%{http_code}' -H "Host: $DOMAIN" -H 'X-Forwarded-Proto: https' "http://$ip:8000/api/v1/health/ready/" || true)
  [ "$code" = 200 ] && ok=$((ok+1)) || ok=0
  [ "$ok" -ge 3 ] && break
  sleep 5
done
if [ "$ok" -lt 3 ]; then
  rollback
  echo "NOT READY - rolled back to $PREVIOUS"
  exit 1
fi

rm -rf "$APP/src.old"
echo "Deployed $SHA (was $PREVIOUS)."
docker ps --format '{{.Names}} {{.Image}} {{.Status}}' | grep -i panorama
