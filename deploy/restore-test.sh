#!/usr/bin/env bash
# Proves a backup restores: loads it into a throwaway database inside the db container, compares row
# counts of the key tables with the live database, then drops the throwaway database.
# Usage: restore-test.sh <backup.dump> [compose-dir]
set -euo pipefail

dump="${1:?usage: restore-test.sh <backup.dump> [compose-dir]}"
compose_dir="${2:-/opt/atl/ai-trading-lab}"
env_file="${ATL_ENV_FILE:-/opt/atl/.env.prod}"
scratch="atl_restore_test"
tables="live_strategies live_decisions live_equity live_candles live_strategy_audit"

cd "$compose_dir"
dc() { docker compose -f compose.prod.yaml --env-file "$env_file" exec -T db "$@"; }

(cd "$(dirname "$dump")" && sha256sum -c "$(basename "$dump").sha256")
dc sh -c "dropdb -U \"\$POSTGRES_USER\" --if-exists $scratch && createdb -U \"\$POSTGRES_USER\" $scratch"
dc sh -c "pg_restore -U \"\$POSTGRES_USER\" -d $scratch --no-owner" < "$dump"

status=0
for table in $tables; do
  live="$(dc sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -Atc 'select count(*) from $table'")"
  restored="$(dc sh -c "psql -U \"\$POSTGRES_USER\" -d $scratch -Atc 'select count(*) from $table'")"
  echo "$table live=$live restored=$restored"
  # Rows may have been added since the backup was taken, never removed.
  if [ "$restored" -gt "$live" ]; then status=1; fi
done
dc sh -c "dropdb -U \"\$POSTGRES_USER\" $scratch"
if [ "$status" -ne 0 ]; then echo "restore check FAILED" >&2; exit 1; fi
echo "restore check ok"
