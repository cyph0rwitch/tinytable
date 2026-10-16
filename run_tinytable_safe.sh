#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

# Force the local database path. This intentionally ignores an inherited
# container value such as /data/tinytable.db.
export TINYTABLE_DB="$PROJECT_ROOT/data/tinytable.db"
export TINYTABLE_PROVIDER_MODEL="${TINYTABLE_PROVIDER_MODEL:-llama3.2}"
export TINYTABLE_REPLIKA_OUTBOUND_ENABLED=0
export TINYTABLE_MULTI_AGENT_ENABLED=0
export TINYTABLE_AGENT_FOLLOWUP_ENABLED=0

exec "$PROJECT_ROOT/.venv/bin/uvicorn" \
  app.main:app \
  --app-dir backend \
  --reload
