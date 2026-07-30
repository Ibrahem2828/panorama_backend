#!/bin/sh
# Restore is intentionally restricted to Staging. It may replace all target database data.
set -eu

: "${DATABASE_URL:?DATABASE_URL is required}"
: "${AGE_IDENTITY_FILE:?AGE_IDENTITY_FILE is required}"
: "${BACKUP_FILE:?BACKUP_FILE is required}"
: "${RESTORE_ENVIRONMENT:?RESTORE_ENVIRONMENT must be staging}"
: "${RESTORE_CONFIRMATION:?Set RESTORE_CONFIRMATION=RESTORE_STAGING}"

if [ "$RESTORE_ENVIRONMENT" != "staging" ] || [ "$RESTORE_CONFIRMATION" != "RESTORE_STAGING" ]; then
  echo "Refusing restore: this script only restores an explicitly confirmed staging database." >&2
  exit 64
fi

command -v age >/dev/null 2>&1 || { echo "age is required" >&2; exit 127; }
command -v pg_restore >/dev/null 2>&1 || { echo "pg_restore is required" >&2; exit 127; }

age --decrypt --identity "$AGE_IDENTITY_FILE" "$BACKUP_FILE" | pg_restore --clean --if-exists --no-owner --no-privileges --dbname="$DATABASE_URL"
printf '%s\n' "Staging restore completed. Run scripts/smoke_test.py and application checks before any promotion."
