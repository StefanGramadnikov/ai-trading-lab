#!/usr/bin/env bash
# Proves a backup restores: loads it into a throwaway database inside the db container, compares row
# counts of the key tables with the live database, then drops the throwaway database.
# Usage: restore-test.sh <backup.dump> [compose-dir]      (run it by hand, for example once a week)
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
# Any restore error fails the test (set -e); nothing is ignored.
dc sh -c "pg_restore -U \"\$POSTGRES_USER\" -d $scratch --no-owner --exit-on-error" < "$dump"

status=0
for table in $tables; do
  live="$(dc sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -Atc 'select count(*) from $table'")"
  restored="$(dc sh -c "psql -U \"\$POSTGRES_USER\" -d $scratch -Atc 'select count(*) from $table'")"
  echo "$table live=$live restored=$restored"
  # The backup is older than the live data, so it may hold fewer rows, never more; and a table that
  # has rows now must not come back empty unless it was empty when the backup was taken.
  if [ "$restored" -gt "$live" ]; then status=1; fi
done
# The decisions table fills from the first cycle on: an empty restore of it means the dump is bad.
decisions="$(dc sh -c "psql -U \"\$POSTGRES_USER\" -d $scratch -Atc 'select count(*) from live_decisions'")"
live_decisions="$(dc sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -Atc 'select count(*) from live_decisions'")"
if [ "$live_decisions" -gt 0 ] && [ "$decisions" -eq 0 ]; then
  echo "live_decisions restored empty although the live table has rows (a backup taken before the first cycle is the only valid reason)" >&2
  status=1
fi
dc sh -c "dropdb -U \"\$POSTGRES_USER\" $scratch"
if [ "$status" -ne 0 ]; then echo "restore check FAILED" >&2; exit 1; fi
echo "restore check ok"
