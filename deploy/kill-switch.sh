#!/usr/bin/env bash
# Stop the loop right now. Paper positions stay in the database; nothing is deleted.
# Usage: kill-switch.sh [compose-dir]   (start again with: docker compose ... up -d live)
set -euo pipefail
compose_dir="${1:-/opt/atl/ai-trading-lab}"
env_file="${ATL_ENV_FILE:-/opt/atl/.env.prod}"
cd "$compose_dir"
docker compose -f compose.prod.yaml --env-file "$env_file" stop live
docker compose -f compose.prod.yaml --env-file "$env_file" ps
