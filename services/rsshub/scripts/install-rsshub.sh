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

# Apply project-local patches on top of the pinned RSSHub commit. These fix
# upstream route bugs that aren't merged upstream (or not yet in our pinned ref).
# Patches live in the main repo's services/rsshub/patches/ (gitignored app is
# wiped on every checkout, so they must be re-applied each install). Idempotent:
# a patch already applied is skipped via --check.
PATCH_DIR="$RSSHUB_DIR/patches"
PATCH_APPLIED=0
if [[ -d "$PATCH_DIR" ]]; then
    for patch in "$PATCH_DIR"/*.patch; do
        [[ -e "$patch" ]] || continue
        if git apply --check "$patch" 2>/dev/null; then
            git apply "$patch"
            echo "[rsshub] applied patch: $(basename "$patch")"
            PATCH_APPLIED=1
        else
            echo "[rsshub] patch already applied or inapplicable, skipping: $(basename "$patch")"
        fi
    done
fi

# A freshly applied patch changed lib/ source, but dist/ is built artefact not
# tracked by git — git checkout left the stale dist in place. Drop it so the
# next `npm run start` (which builds when dist/index.mjs is missing) regenerates
# dist from the patched source instead of serving stale bytecode.
if [[ "$PATCH_APPLIED" == "1" && -d "$APP_DIR/dist" ]]; then
    rm -rf "$APP_DIR/dist"
    echo "[rsshub] removed stale dist/ after patching; will rebuild on next start"
fi

pnpm install --registry="$NPM_REGISTRY" --frozen-lockfile

# Install the Chromium build the pinned playwright expects. Several routes
# (xueqiu/* via parseToken, and any puppeteer/playwright route) launch a
# headless browser; pnpm install only pulls the playwright *driver*, not the
# browser binary, so without this step they crash at runtime with
# `browserType.launch: Executable doesn't exist` and RSSHub returns 503. The
# binary lives in the global playwright cache (~/.cache/ms-playwright or
# ~/Library/Caches/ms-playwright), which survives reinstalls of the app but is
# wiped if the cache is cleared — so re-run it every install. Idempotent:
# playwright skips an already-installed revision. `pnpm exec` resolves the
# app's own playwright CLI through pnpm's symlink layout, so the revision
# matches the installed playwright version exactly (e.g. 1.60.0 → chromium
# revision 1223).
echo "[rsshub] installing playwright chromium (needed by xueqiu/puppeteer routes)…"
if ! pnpm exec playwright install chromium; then
    echo "[rsshub] WARN: playwright chromium install failed — xueqiu/puppeteer routes will 503 until installed manually" >&2
fi
