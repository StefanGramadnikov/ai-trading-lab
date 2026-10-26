#!/usr/bin/env bash
# Nightly logical backup of the application database, kept for 7 days. Run by atl-backup.timer.
# Usage: backup.sh [compose-dir] [backup-dir]
set -euo pipefail
umask 077

compose_dir="${1:-/opt/atl/ai-trading-lab}"
backup_dir="${2:-/var/backups/atl}"
env_file="${ATL_ENV_FILE:-/opt/atl/.env.prod}"
keep_days=7

mkdir -p "$backup_dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$backup_dir/atl-$stamp.dump"
partial="$target.partial"

cd "$compose_dir"
# -Fc: compressed custom format, restorable with pg_restore. Written to a partial name first so a
# half-written file is never mistaken for a backup.
docker compose -f compose.prod.yaml --env-file "$env_file" exec -T db \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$partial"

if [ ! -s "$partial" ]; then
  rm -f "$partial"
  echo "backup is empty, refusing to keep it" >&2
  exit 1
fi
mv "$partial" "$target"
# Relative name inside the file, so it verifies wherever the pair is copied (server or Mac).
(cd "$backup_dir" && sha256sum "$(basename "$target")" > "$(basename "$target").sha256")
find "$backup_dir" -maxdepth 1 -name 'atl-*.dump*' -mtime +"$keep_days" -delete
echo "$target"
