#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

if [[ ! -f "$PROJECT_ROOT/.env" ]]; then
  echo "Missing $PROJECT_ROOT/.env" >&2
  exit 1
fi

set -a
source "$PROJECT_ROOT/.env"
set +a

# The receiver must never send a customer message, even if .env contains 1.
export TINYTABLE_REPLIKA_OUTBOUND_ENABLED=0

exec "$PROJECT_ROOT/.venv/bin/python" \
  replika_provider.py \
  --bootstrap-file "$PROJECT_ROOT/replika_bootstrap_current.md" \
  --receive-only \
  --receive-with-read-only-bootstrap \
  --tinytable-api "${TINYTABLE_API_URL:-http://127.0.0.1:18000/api}"
