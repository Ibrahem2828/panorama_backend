#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <archive-path> <checksum-path>" >&2
  exit 64
fi

archive_path=$1
checksum_path=$2
if [ ! -f "$archive_path" ] || [ ! -f "$checksum_path" ]; then
  echo "Archive and checksum files must exist." >&2
  exit 66
fi

sha256sum -c "$checksum_path"
tar -tzf "$archive_path" >/dev/null
echo "Archive checksum and tar structure verified."
