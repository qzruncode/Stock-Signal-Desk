#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
MARKER="$PROJECT_DIR/logs/.webfetch-runtime-v2"
RUNTIME_ID="scrapling-0.4.11-trafilatura-2.1.0-markitdown-0.1.6"

runtime_ready() {
    python3 - <<'PY' >/dev/null 2>&1
from scrapling.fetchers import Fetcher
from markitdown import MarkItDown
import trafilatura
import patchright
import playwright
PY
}

if runtime_ready && [[ -f "$MARKER" ]] && [[ "$(tr -d '[:space:]' < "$MARKER")" == "$RUNTIME_ID" ]]; then
    echo "[webfetch] crawler, article and document runtime ready"
    exit 0
fi

echo "[webfetch] installing webfetch HTTP, browser, article and document runtime…"
python3 -m pip install \
    "scrapling[fetchers]==0.4.11" \
    "trafilatura==2.1.0" \
    "markitdown[pdf,docx,pptx,xlsx,xls]==0.1.6"
scrapling install
runtime_ready
mkdir -p "$(dirname "$MARKER")"
printf '%s\n' "$RUNTIME_ID" > "$MARKER"
echo "[webfetch] local crawler and document runtime ready"
