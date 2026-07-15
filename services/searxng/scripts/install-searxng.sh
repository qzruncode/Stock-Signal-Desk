#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SEARXNG_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_DIR="$SEARXNG_DIR/app"
VENV_DIR="$SEARXNG_DIR/.venv"
SEARXNG_REPO="${SEARXNG_REPO:-https://github.com/searxng/searxng.git}"
SEARXNG_REF="${SEARXNG_REF:-58e02a01ae1d3a422f0d4aead2a30804e5b59e1b}"
INSTALL_MARKER="$VENV_DIR/.dsa-installed"
BUILD_ID="${SEARXNG_REF}:source-runtime-v1"

if [[ ! -d "$APP_DIR/.git" ]]; then
    if [[ -e "$APP_DIR" ]]; then
        echo "[searxng] $APP_DIR exists but is not a git checkout" >&2
        exit 1
    fi
    git clone --filter=blob:none "$SEARXNG_REPO" "$APP_DIR"
fi

cd "$APP_DIR"
if [[ "$(git rev-parse HEAD)" != "$SEARXNG_REF" ]]; then
    git fetch --depth 1 origin "$SEARXNG_REF"
    git checkout --quiet --detach FETCH_HEAD
fi
echo "[searxng] source ready at $(git rev-parse --short HEAD)"

if [[ -f "$INSTALL_MARKER" ]] \
    && [[ "$(tr -d '[:space:]' < "$INSTALL_MARKER")" == "$BUILD_ID" ]] \
    && [[ -x "$VENV_DIR/bin/granian" ]]; then
    echo "[searxng] dependencies already installed"
    exit 0
fi

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel
"$VENV_DIR/bin/python" -m pip install \
    -r "$APP_DIR/requirements.txt" \
    -r "$APP_DIR/requirements-server.txt"
"$VENV_DIR/bin/python" -m pip install --no-build-isolation -e "$APP_DIR"

printf '%s\n' "$BUILD_ID" > "$INSTALL_MARKER"
echo "[searxng] local source runtime ready"
