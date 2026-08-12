#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# Local secrets stay in .env (ignored by Git) and are exported only to this process.
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

exec python -m uvicorn backend.main:app --host "${ASKDATA_HOST:-127.0.0.1}" --port "${ASKDATA_PORT:-8000}"
