#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

if [[ ! -x "$PROJECT_ROOT/.venv/bin/uvicorn" ]]; then
  echo "Missing .venv/bin/uvicorn. Create the local environment first." >&2
  exit 1
fi

mkdir -p "$PROJECT_ROOT/data" "$PROJECT_ROOT/logs"
export TINYTABLE_DB="$PROJECT_ROOT/data/tinytable.db"
export TINYTABLE_STANDALONE=1
export TINYTABLE_REPLIKA_OUTBOUND_ENABLED=0
export TINYTABLE_MULTI_AGENT_ENABLED=0
export TINYTABLE_AGENT_FOLLOWUP_ENABLED=0
export TINYTABLE_PROVIDER_BASE_URL="${TINYTABLE_PROVIDER_BASE_URL:-http://127.0.0.1:11434/v1}"
export TINYTABLE_PROVIDER_MODEL="${TINYTABLE_PROVIDER_MODEL:-llama3.2}"
export TINYTABLE_PROVIDER_TIMEOUT="${TINYTABLE_PROVIDER_TIMEOUT:-120}"

exec "$PROJECT_ROOT/.venv/bin/uvicorn" \
  app.main:app \
  --app-dir backend \
  --host "${TINYTABLE_HOST:-127.0.0.1}" \
  --port "${TINYTABLE_PORT:-8000}"
