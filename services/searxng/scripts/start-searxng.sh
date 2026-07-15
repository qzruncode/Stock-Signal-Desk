#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SEARXNG_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_DIR="$(cd "$SEARXNG_DIR/../.." && pwd)"
APP_DIR="$SEARXNG_DIR/app"
VENV_DIR="$SEARXNG_DIR/.venv"
SETTINGS_PATH="$SEARXNG_DIR/settings.yml"
PORT="${SEARXNG_PORT:-8888}"

mkdir -p "$PROJECT_DIR/logs"
printf '%s\n' "$$" > "$PROJECT_DIR/logs/searxng-supervisor.pid"

cd "$APP_DIR"
exec env \
    SEARXNG_SETTINGS_PATH="$SETTINGS_PATH" \
    SEARXNG_BIND_ADDRESS=127.0.0.1 \
    SEARXNG_PORT="$PORT" \
    "$VENV_DIR/bin/granian" \
        --interface wsgi \
        --host 127.0.0.1 \
        --port "$PORT" \
        --workers 1 \
        --blocking-threads 4 \
        --no-ws \
        --log-level warning \
        searx.webapp:app
