#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RSSHUB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PNPM_SPEC="$(sed -n 's/.*"packageManager"[[:space:]]*:[[:space:]]*"\(pnpm@[^"]*\)".*/\1/p' "$RSSHUB_DIR/package.json" | head -n 1)"

if [[ -z "$PNPM_SPEC" ]]; then
    echo "[rsshub] packageManager must pin pnpm in package.json" >&2
    exit 1
fi

if command -v corepack >/dev/null 2>&1; then
    exec corepack "$PNPM_SPEC" "$@"
fi

if command -v npx >/dev/null 2>&1; then
    exec npx --yes "$PNPM_SPEC" "$@"
fi

echo "[rsshub] neither corepack nor npx is available for $PNPM_SPEC" >&2
exit 1
