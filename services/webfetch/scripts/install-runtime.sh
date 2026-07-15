#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
MARKER="$PROJECT_DIR/logs/.webfetch-runtime-v1"
RUNTIME_ID="scrapling-0.4.11-playwright-patchright"

runtime_ready() {
    python3 - <<'PY' >/dev/null 2>&1
from scrapling.fetchers import Fetcher, StealthyFetcher
import patchright
import playwright
PY
}

if runtime_ready && [[ -f "$MARKER" ]] && [[ "$(tr -d '[:space:]' < "$MARKER")" == "$RUNTIME_ID" ]]; then
    echo "[webfetch] Scrapling / Patchright runtime ready"
    exit 0
fi

echo "[webfetch] installing Scrapling / Playwright / Patchright runtime…"
python3 -m pip install "scrapling[fetchers]==0.4.11"
scrapling install
runtime_ready
mkdir -p "$(dirname "$MARKER")"
printf '%s\n' "$RUNTIME_ID" > "$MARKER"
echo "[webfetch] local crawler runtime ready"
