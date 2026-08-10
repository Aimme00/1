#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
exec python -m uvicorn backend.main:app --host "${ASKDATA_HOST:-127.0.0.1}" --port "${ASKDATA_PORT:-8000}"
