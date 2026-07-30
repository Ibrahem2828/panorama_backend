#!/bin/sh
# Create an encrypted PostgreSQL custom-format backup. Run from a controlled backup runner.
set -eu

: "${DATABASE_URL:?DATABASE_URL is required}"
: "${BACKUP_DIR:?BACKUP_DIR is required}"
: "${AGE_RECIPIENT:?AGE_RECIPIENT is required}"

command -v pg_dump >/dev/null 2>&1 || { echo "pg_dump is required" >&2; exit 127; }
command -v age >/dev/null 2>&1 || { echo "age is required" >&2; exit 127; }
command -v sha256sum >/dev/null 2>&1 || { echo "sha256sum is required" >&2; exit 127; }

umask 077
mkdir -p "$BACKUP_DIR"
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
plain_archive="$BACKUP_DIR/.panorama-$timestamp.dump"
encrypted_archive="$BACKUP_DIR/panorama-$timestamp.dump.age"
trap 'rm -f "$plain_archive"' 0 1 2 3 15

pg_dump --format=custom --no-owner --no-privileges --file "$plain_archive" "$DATABASE_URL"
age --recipient "$AGE_RECIPIENT" --output "$encrypted_archive" "$plain_archive"
sha256sum "$encrypted_archive" > "$encrypted_archive.sha256"
printf '%s\n' "backup=$encrypted_archive" "sha256=$encrypted_archive.sha256"
