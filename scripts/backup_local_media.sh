#!/bin/sh
set -eu

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
  echo "Usage: $0 <volume-name> <existing-output-directory> [--dry-run]" >&2
  exit 64
fi

volume_name=$1
output_directory=$2
dry_run=${3:-}

if [ "$dry_run" != "" ] && [ "$dry_run" != "--dry-run" ]; then
  echo "Unknown option: $dry_run" >&2
  exit 64
fi
if [ ! -d "$output_directory" ] || [ ! -w "$output_directory" ]; then
  echo "Output directory must already exist and be writable." >&2
  exit 64
fi

docker volume inspect "$volume_name" >/dev/null
archive_name="${volume_name}-media-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
archive_path="$output_directory/$archive_name"

if [ "$dry_run" = "--dry-run" ]; then
  echo "Would archive read-only volume '$volume_name' to '$archive_path'."
  exit 0
fi
if [ -e "$archive_path" ]; then
  echo "Refusing to overwrite existing archive." >&2
  exit 65
fi

docker run --rm \
  -v "$volume_name:/source:ro" \
  -v "$output_directory:/backup" \
  alpine:3.20 \
  sh -c "tar -C /source -czf /backup/$archive_name ."
sha256sum "$archive_path" > "$archive_path.sha256"
echo "Created $archive_path and $archive_path.sha256"
