#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FIRECRAWL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_DIR="$(cd "$FIRECRAWL_DIR/../.." && pwd)"
APP_DIR="$FIRECRAWL_DIR/app"
API_DIR="$APP_DIR/apps/api"
PLAYWRIGHT_DIR="$APP_DIR/apps/playwright-service-ts"
REDIS_PORT="${FIRECRAWL_REDIS_PORT:-6380}"
PLAYWRIGHT_PORT="${FIRECRAWL_PLAYWRIGHT_PORT:-3003}"
SEARXNG_ENDPOINT="${SEARXNG_ENDPOINT:-http://127.0.0.1:8888}"
SEARXNG_ENGINES="${SEARXNG_ENGINES:-bing,yahoo,360search,sogou,google}"

if [[ -s "$HOME/.nvm/nvm.sh" ]]; then
    unset npm_config_prefix
    # shellcheck disable=SC1090
    source "$HOME/.nvm/nvm.sh"
    nvm use 22 >/dev/null
fi

mkdir -p "$PROJECT_DIR/logs"
printf '%s\n' "$$" > "$PROJECT_DIR/logs/firecrawl-supervisor.pid"

redis_pid=""
playwright_pid=""
api_pid=""

cleanup() {
    [[ -n "$api_pid" ]] && kill "$api_pid" 2>/dev/null || true
    [[ -n "$playwright_pid" ]] && kill "$playwright_pid" 2>/dev/null || true
    [[ -n "$redis_pid" ]] && kill "$redis_pid" 2>/dev/null || true
    rm -f "$PROJECT_DIR/logs/firecrawl-supervisor.pid"
}
trap 'cleanup; exit 0' INT TERM
trap cleanup EXIT

redis-server \
    --bind 127.0.0.1 \
    --port "$REDIS_PORT" \
    --protected-mode yes \
    --save "" \
    --appendonly no &
redis_pid=$!

for _ in $(seq 1 30); do
    redis-cli -h 127.0.0.1 -p "$REDIS_PORT" ping >/dev/null 2>&1 && break
    sleep 0.2
done
redis-cli -h 127.0.0.1 -p "$REDIS_PORT" ping >/dev/null

env \
    PORT="$PLAYWRIGHT_PORT" \
    BLOCK_MEDIA=true \
    ALLOW_SYNTHETIC_EGRESS=true \
    MAX_CONCURRENT_PAGES=3 \
    pnpm --dir "$PLAYWRIGHT_DIR" start &
playwright_pid=$!

env \
    PORT=3002 \
    HOST=127.0.0.1 \
    ENV=local \
    USE_DB_AUTHENTICATION=false \
    ALLOW_SYNTHETIC_EGRESS=true \
    REDIS_URL="redis://127.0.0.1:$REDIS_PORT" \
    REDIS_RATE_LIMIT_URL="redis://127.0.0.1:$REDIS_PORT" \
    REDIS_EVICT_URL="redis://127.0.0.1:$REDIS_PORT" \
    PLAYWRIGHT_MICROSERVICE_URL="http://127.0.0.1:$PLAYWRIGHT_PORT/scrape" \
    SEARXNG_ENDPOINT="$SEARXNG_ENDPOINT" \
    SEARXNG_ENGINES="$SEARXNG_ENGINES" \
    BULL_AUTH_KEY=daily-stock-local \
    LOGGING_LEVEL=WARN \
    pnpm --dir "$API_DIR" server:production:nobuild &
api_pid=$!

while kill -0 "$redis_pid" 2>/dev/null \
    && kill -0 "$playwright_pid" 2>/dev/null \
    && kill -0 "$api_pid" 2>/dev/null; do
    sleep 2
done

exit 1
