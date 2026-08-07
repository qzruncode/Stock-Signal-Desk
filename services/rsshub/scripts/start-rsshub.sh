#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RSSHUB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_DIR="$RSSHUB_DIR/app"
MODE="${1:-start}"

if [[ -n "${NVM_BIN:-}" ]]; then
    export PATH="$NVM_BIN:$PATH"
fi

export PORT="${PORT:-1200}"
export CACHE_TYPE="${CACHE_TYPE:-memory}"
export CACHE_EXPIRE="${CACHE_EXPIRE:-300}"

if [[ "$MODE" == "dev" ]]; then
    exec bash "$RSSHUB_DIR/scripts/run-pnpm.sh" --dir "$APP_DIR" dev
fi

exec bash "$RSSHUB_DIR/scripts/run-pnpm.sh" --dir "$APP_DIR" start
