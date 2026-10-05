#!/usr/bin/env bash
# Daily Panorama backup (run ON THE SERVER by cron; see install-backup.sh).
#
#   backup-panorama.sh            dump the database (+ media volume), verify, rotate
#   backup-panorama.sh --verify   additionally restore the newest dump into a scratch database and compare it
#
# Output: /var/backups/panorama/{daily,weekly}/. Exit code is non-zero on ANY failure, and the failure is also
# written to syslog (tag: panorama-backup) and /var/backups/panorama/LAST_STATUS so a monitor can alert on it.
set -Eeuo pipefail
umask 077

DB_CONTAINER="${DB_CONTAINER:-e6340upm0y8v73v3b9ni6yp5}"
DB_NAME="${DB_NAME:-panorama}"
DB_USER="${DB_USER:-postgres}"
ROOT="${BACKUP_ROOT:-/var/backups/panorama}"
KEEP_DAILY="${KEEP_DAILY:-14}"
KEEP_WEEKLY="${KEEP_WEEKLY:-8}"
MEDIA_VOLUME="${MEDIA_VOLUME:-panorama_panorama_media}"

DAILY="$ROOT/daily"; WEEKLY="$ROOT/weekly"
mkdir -p "$DAILY" "$WEEKLY"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
STATUS="$ROOT/LAST_STATUS"

log()  { logger -t panorama-backup -- "$*"; echo "$(date -u +%FT%TZ) $*"; }
fail() { log "FAILED: $*"; echo "FAILED $(date -u +%FT%TZ) $*" > "$STATUS"; exit 1; }
trap 'fail "unexpected error at line $LINENO"' ERR

DUMP="$DAILY/${DB_NAME}-${STAMP}.dump"

# 1. Dump to a temp file, then move into place only when complete.
docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc --no-owner > "$DUMP.tmp" || fail "pg_dump"
[ -s "$DUMP.tmp" ] || fail "empty dump"

# 2. Verify the archive is readable and really contains the schema and data.
TOC_ENTRIES=$(docker exec -i "$DB_CONTAINER" pg_restore -l < "$DUMP.tmp" | grep -c . || true)
[ "$TOC_ENTRIES" -gt 50 ] || fail "dump looks incomplete ($TOC_ENTRIES TOC entries)"
mv "$DUMP.tmp" "$DUMP"
( cd "$DAILY" && sha256sum "$(basename "$DUMP")" > "$(basename "$DUMP").sha256" )
log "database dump ok: $(basename "$DUMP") $(du -h "$DUMP" | cut -f1), $TOC_ENTRIES TOC entries"

# 3. Media volume (uploaded student cards, attachments). Skipped if the volume does not exist yet.
if docker volume inspect "$MEDIA_VOLUME" >/dev/null 2>&1; then
  MEDIA="$DAILY/media-${STAMP}.tar.gz"
  docker run --rm -v "$MEDIA_VOLUME":/data:ro --entrypoint tar "$(docker images --format '{{.Repository}}:{{.Tag}}' | grep '^panorama-backend:' | head -1)" \
    czf - -C /data . > "$MEDIA.tmp" || fail "media archive"
  tar tzf "$MEDIA.tmp" >/dev/null || fail "media archive unreadable"
  mv "$MEDIA.tmp" "$MEDIA"
  log "media archive ok: $(basename "$MEDIA") $(du -h "$MEDIA" | cut -f1)"
fi

# 4. Keep Sunday's dump as a weekly copy.
if [ "$(date -u +%u)" = 7 ]; then
  cp -p "$DUMP" "$DUMP.sha256" "$WEEKLY/"
fi

# 5. Rotation (names are timestamped, so lexical order is chronological).
prune() { find "$1" -maxdepth 1 -type f -name "$2" | sort | head -n -"$3" | while read -r f; do rm -f -- "$f" "$f.sha256"; done; }
prune "$DAILY"  "${DB_NAME}-*.dump"  "$KEEP_DAILY"
prune "$DAILY"  "media-*.tar.gz"     "$KEEP_DAILY"
prune "$WEEKLY" "${DB_NAME}-*.dump"  "$KEEP_WEEKLY"

# 6. Optional restore drill into a scratch database; compares table and row counts with the live database.
if [ "${1:-}" = "--verify" ]; then
  SCRATCH="${DB_NAME}_restore_check"
  q() { docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$1" -At -c "$2"; }
  docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d postgres -c "DROP DATABASE IF EXISTS $SCRATCH" >/dev/null
  docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d postgres -c "CREATE DATABASE $SCRATCH" >/dev/null
  docker exec -i "$DB_CONTAINER" pg_restore -U "$DB_USER" -d "$SCRATCH" --no-owner --exit-on-error --single-transaction < "$DUMP" \
    || { docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d postgres -c "DROP DATABASE IF EXISTS $SCRATCH" >/dev/null || true; fail "restore drill: pg_restore"; }
  # Tables and applied migrations only: live row counts move between the dump and this check.
  COUNT_SQL="select (select count(*) from information_schema.tables where table_schema='public'), (select count(*) from django_migrations)"
  LIVE="$(q "$DB_NAME" "$COUNT_SQL")"; COPY="$(q "$SCRATCH" "$COUNT_SQL")"
  docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d postgres -c "DROP DATABASE IF EXISTS $SCRATCH" >/dev/null
  [ "$LIVE" = "$COPY" ] || fail "restore drill mismatch: live=$LIVE restored=$COPY"
  log "restore drill ok (tables|migrations = $COPY)"
fi

echo "OK $(date -u +%FT%TZ) $(basename "$DUMP")" > "$STATUS"
log "backup finished"
