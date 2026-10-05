#!/usr/bin/env bash
# Deploy the Panorama backend straight to the Docker host, without the Coolify UI.
#
# Run ON THE SERVER as root, after shipping the source from your machine:
#   git -C projects/backend archive origin/main | ssh root@76.13.155.172 \
#     'mkdir -p /opt/panorama/backend/src && rm -rf /opt/panorama/backend/src/* && tar -x -C /opt/panorama/backend/src'
#   ssh root@76.13.155.172 'bash -s -- <git-sha>' < ops/deploy-backend.sh
#
# What it does: builds the image, runs the release job (check --deploy, migrations,
# collectstatic), stops the old Coolify-managed container, starts web + worker + beat
# behind the existing Traefik proxy, and rolls back if /health/ready/ never turns 200.
# Secrets are read from the running container into /opt/panorama/backend/.env (mode 600)
# and are never printed.
set -euo pipefail

SHA="${1:?usage: deploy-backend.sh <git-sha>}"
APP=/opt/panorama/backend
OLD=eby52x8qksscjvfeqxf0eob7-162936558838     # Coolify container being replaced
DOMAIN=api.xn--mgbaab0cxheq.tech
NET=coolify
P="docker compose -p panorama -f $APP/src/docker-compose.coolify.yml -f $APP/override.yml --env-file $APP/.env"

[ -f "$APP/src/docker-compose.coolify.yml" ] || { echo "source missing in $APP/src"; exit 1; }

# 1. Environment file, taken from the container that is currently serving production.
if [ ! -f "$APP/.env" ]; then
  umask 077
  docker exec "$OLD" env | grep -E '^(SECRET_KEY|FIELD_ENCRYPTION_KEY|ALLOWED_HOSTS|CSRF_TRUSTED_ORIGINS|CORS_ALLOWED_ORIGINS|DATABASE_URL|REDIS_URL|EMAIL_[A-Z_]+|DEFAULT_FROM_EMAIL|SERVER_EMAIL|SUPPORT_EMAIL|OTP_[A-Z_]+|JWT_[A-Z_]+|LOG_LEVEL|SECURE_HSTS_[A-Z_]+)=' > "$APP/.env"
fi
setenv() { grep -v "^$1=" "$APP/.env" > "$APP/.env.tmp" || true; echo "$1=$2" >> "$APP/.env.tmp"; mv "$APP/.env.tmp" "$APP/.env"; chmod 600 "$APP/.env"; }
setenv IMAGE_TAG "$SHA"
setenv RELEASE_VERSION "2.0.0"
setenv BUILD_DATE "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
setenv PORT 8000
setenv APP_BASE_URL "https://$DOMAIN"
setenv HEALTHCHECK_HOST "$DOMAIN"
setenv DATABASE_SSL_REQUIRE True
setenv STORAGE_BACKEND local
setenv USE_X_FORWARDED_HOST True
# HTTPS only: the temporary HTTP mode is switched off here.
setenv SECURE_SSL_REDIRECT True
setenv SESSION_COOKIE_SECURE True
setenv CSRF_COOKIE_SECURE True
# HSTS starts conservative (1 day, no subdomains, no preload) because the apex domain also
# hosts other apps; raise it once everything is confirmed to be HTTPS-only.
setenv SECURE_HSTS_SECONDS 86400
setenv SECURE_HSTS_INCLUDE_SUBDOMAINS False
setenv SECURE_HSTS_PRELOAD False
# Compose interpolates every service, including the profile-gated self-hosted postgres,
# which we do not run (Postgres and Redis are the existing Coolify containers).
setenv POSTGRES_DB unused
setenv POSTGRES_USER unused
setenv POSTGRES_PASSWORD unused
# Email OTP goes through the Celery worker started below.
setenv OTP_EMAIL_ASYNC True

# 2. Compose override: join the Coolify network (Postgres/Redis/Traefik live there),
#    pass the whole .env to every service, route the domain, and make the Docker
#    health check survive SECURE_SSL_REDIRECT by announcing https like the proxy does.
cat > "$APP/override.yml" <<YML
services:
  web:
    env_file: [$APP/.env]
    build:
      target: runtime   # the Dockerfile's last stage is the 1GB+ LibreOffice conversion image
    healthcheck:
      test: ["CMD", "python", "-c", "import json,http.client,os,sys; c=http.client.HTTPConnection('127.0.0.1',int(os.environ['PORT']),timeout=3); c.request('GET','/api/v1/health/live/',headers={'Host':os.environ['HEALTHCHECK_HOST'],'X-Forwarded-Proto':'https'}); r=c.getresponse(); b=json.loads(r.read()); sys.exit(0 if r.status==200 and b.get('code')=='LIVE' else 1)"]
    labels:
      - traefik.enable=true
      - traefik.docker.network=$NET
      - traefik.http.middlewares.panorama-gzip.compress=true
      - traefik.http.middlewares.panorama-https.redirectscheme.scheme=https
      - traefik.http.services.panorama-api.loadbalancer.server.port=8000
      - traefik.http.routers.panorama-api-http.entryPoints=http
      - traefik.http.routers.panorama-api-http.rule=Host(\`$DOMAIN\`)
      - traefik.http.routers.panorama-api-http.middlewares=panorama-https
      - traefik.http.routers.panorama-api-http.service=panorama-api
      - traefik.http.routers.panorama-api.entryPoints=https
      - traefik.http.routers.panorama-api.rule=Host(\`$DOMAIN\`)
      - traefik.http.routers.panorama-api.middlewares=panorama-gzip
      - traefik.http.routers.panorama-api.service=panorama-api
      - traefik.http.routers.panorama-api.tls=true
      - traefik.http.routers.panorama-api.tls.certresolver=letsencrypt
  worker:
    env_file: [$APP/.env]
  beat:
    env_file: [$APP/.env]
  release:
    env_file: [$APP/.env]
networks:
  default:
    name: $NET
    external: true
YML

# 3. Build, then run the release job (stops here if checks or migrations fail).
$P build web
[ "${STOP_AFTER:-}" = build ] && { echo "Built $SHA; stopping before the release job (STOP_AFTER=build)."; exit 0; }
$P --profile release run --rm release

# 4. Swap traffic: stop (do not remove) the old container so it can be restarted.
docker stop "$OLD"
$P up -d web worker beat

# 5. Wait for readiness; roll back to the old container if it never gets there.
ok=0
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -m 10 -w '%{http_code}' -H "Host: $DOMAIN" -H 'X-Forwarded-Proto: https' "http://$(docker inspect -f '{{(index .NetworkSettings.Networks "'$NET'").IPAddress}}' panorama-web-1):8000/api/v1/health/ready/" || true)
  [ "$code" = 200 ] && ok=$((ok+1)) || ok=0
  [ "$ok" -ge 3 ] && break
  sleep 5
done
if [ "$ok" -lt 3 ]; then
  echo "NOT READY - rolling back to the previous container"
  $P down
  docker start "$OLD"
  echo "note: migrations already applied stay applied; restore /root/backups/*.dump if the old image fails"
  exit 1
fi
echo "Deployed $SHA. Check: curl -s https://$DOMAIN/api/v1/health/ready/"

# Optional cleanup (dry run unless you pass CLEAN=yes): old Panorama images and build cache.
# Volumes and running containers' images are never touched.
if [ "${CLEAN:-no}" = yes ]; then
  docker image prune -f
  docker builder prune -f --filter until=24h
  docker images 'panorama-backend' --format '{{.Tag}}' | grep -v "^$SHA$" | tail -n +3 | xargs -r -I{} docker rmi panorama-backend:{}
else
  echo "Cleanup preview:"; docker image prune --filter dangling=true -f --dry-run 2>/dev/null || docker images -f dangling=true
fi
