#!/usr/bin/env bash
# Nightly logical backup of the application database; keeps the newest 7. Run by atl-backup.timer as `atl`.
# Usage: backup.sh [compose-dir] [backup-dir]
set -euo pipefail
umask 077

compose_dir="${1:-/opt/atl/ai-trading-lab}"
backup_dir="${2:-/var/backups/atl}"
env_file="${ATL_ENV_FILE:-/opt/atl/.env.prod}"
keep=7
min_free_kb=$((512 * 1024))

mkdir -p "$backup_dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$backup_dir/atl-$stamp.dump"
partial="$target.partial"
trap 'rm -f "$partial"' EXIT

free_kb="$(df -Pk "$backup_dir" | awk 'NR==2 {print $4}')"
if [ "$free_kb" -lt "$min_free_kb" ]; then
  echo "less than 512 MB free in $backup_dir, refusing to back up" >&2
  exit 1
fi

cd "$compose_dir"
dc() { docker compose -f compose.prod.yaml --env-file "$env_file" "$@"; }

# -Fc: compressed custom format, restorable with pg_restore. Written under a partial name first so a
# half-written file is never mistaken for a backup.
dc exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$partial"

# The dump must be readable as an archive that contains our tables before it replaces anything.
toc="$(dc exec -T db pg_restore -l < "$partial")"
if ! printf '%s\n' "$toc" | grep -q 'TABLE DATA public live_decisions'; then
  echo "backup does not contain the expected tables, keeping the previous backups" >&2
  exit 1
fi

mv "$partial" "$target"
# Relative name inside the file, so it verifies wherever the pair is copied (server or Mac).
(cd "$backup_dir" && sha256sum "$(basename "$target")" > "$(basename "$target").sha256")

# Prune by count, never by age: a clock jump cannot delete the good backups.
(
  cd "$backup_dir"
  # shellcheck disable=SC2012
  ls -1 atl-*.dump 2>/dev/null | sort -r | tail -n +$((keep + 1)) | while read -r old; do
    rm -f -- "$old" "$old.sha256"
  done
)
echo "$target"
