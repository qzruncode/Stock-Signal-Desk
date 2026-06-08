#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RSSHUB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_DIR="$RSSHUB_DIR/app"
RSSHUB_REPO="${RSSHUB_REPO:-https://github.com/DIYgod/RSSHub.git}"
RSSHUB_REF="${RSSHUB_REF:-5eb4e3701d88cf31420a50fcd1dbf26ed6448408}"
NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmjs.org/}"

if [[ -n "${NVM_BIN:-}" ]]; then
    export PATH="$NVM_BIN:$PATH"
fi

if [[ ! -d "$APP_DIR/.git" ]]; then
    rm -rf "$APP_DIR"
    git clone --depth 1 "$RSSHUB_REPO" "$APP_DIR"
fi

cd "$APP_DIR"
git fetch --depth 1 origin "$RSSHUB_REF"
git checkout --quiet FETCH_HEAD

pnpm install --registry="$NPM_REGISTRY" --frozen-lockfile
