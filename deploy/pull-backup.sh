#!/usr/bin/env bash
# Run on YOUR Mac: copies the newest server backup (and its checksum) into a local folder and verifies it.
# Usage: pull-backup.sh <user@server> [local-dir]
set -euo pipefail
umask 077

server="${1:?usage: pull-backup.sh <user@server> [local-dir]}"
local_dir="${2:-$HOME/atl-backups}"
remote_dir="/var/backups/atl"

mkdir -p "$local_dir"
latest="$(ssh "$server" "ls -1t $remote_dir/atl-*.dump 2>/dev/null | head -n 1")"
if [ -z "$latest" ]; then echo "no backup found on $server" >&2; exit 1; fi
scp -q "$server:$latest" "$server:$latest.sha256" "$local_dir/"
(cd "$local_dir" && shasum -a 256 -c "$(basename "$latest").sha256")
echo "$local_dir/$(basename "$latest")"
