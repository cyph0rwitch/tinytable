#!/usr/bin/env bash
set -euo pipefail

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

if [[ $# -lt 1 || $# -gt 2 || -z "$1" ]]; then
    echo "Usage: $0 \"message to send\" [bootstrap-file]" >&2
    exit 2
fi

bootstrap_file=${2:-${REPLIKA_BOOTSTRAP_FILE:-$project_dir/replika_send_current.md}}
if [[ "$bootstrap_file" != /* ]]; then
    bootstrap_file="$project_dir/$bootstrap_file"
fi
if [[ ! -f "$bootstrap_file" || ! -r "$bootstrap_file" ]]; then
    echo "Missing bootstrap file: $bootstrap_file" >&2
    exit 1
fi

tinytable_api=${TINYTABLE_API_URL:-http://127.0.0.1:8000/api}
if [[ -n "${TINYTABLE_PYTHON:-}" ]]; then
    python_bin="$TINYTABLE_PYTHON"
elif [[ -x "$project_dir/.venv/bin/python" ]]; then
    python_bin="$project_dir/.venv/bin/python"
else
    python_bin=python3
fi
exec "$python_bin" "$project_dir/replika_provider.py" \
    --bootstrap-file "$bootstrap_file" \
    --send-text "$1" \
    --enable-outbound \
    --tinytable-api "$tinytable_api"
