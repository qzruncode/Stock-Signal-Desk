#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FIRECRAWL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_DIR="$FIRECRAWL_DIR/app"
FIRECRAWL_REPO="${FIRECRAWL_REPO:-https://github.com/firecrawl/firecrawl.git}"
FIRECRAWL_TAG="${FIRECRAWL_TAG:-v2.11.0}"
FIRECRAWL_REF="${FIRECRAWL_REF:-ef12eb36b2f3382838dfe0a0c1a5add3d5df7fe5}"
API_DIR="$APP_DIR/apps/api"
PLAYWRIGHT_DIR="$APP_DIR/apps/playwright-service-ts"
INSTALL_MARKER="$APP_DIR/.dsa-installed"
PATCH_DIR="$FIRECRAWL_DIR/patches"
BUILD_ID="${FIRECRAWL_REF}:local-runtime-v2"

if [[ ! -d "$APP_DIR/.git" ]]; then
    rm -rf "$APP_DIR"
    git clone --depth 1 --branch "$FIRECRAWL_TAG" --single-branch "$FIRECRAWL_REPO" "$APP_DIR"
fi

cd "$APP_DIR"
if [[ "$(git rev-parse HEAD)" != "$FIRECRAWL_REF" ]]; then
    git fetch --depth 1 origin "$FIRECRAWL_REF"
    git checkout --quiet --detach FETCH_HEAD
fi

for patch_file in "$PATCH_DIR"/*.patch; do
    [[ -e "$patch_file" ]] || continue
    if git apply --reverse --check "$patch_file" >/dev/null 2>&1; then
        continue
    fi
    git apply --check "$patch_file"
    git apply "$patch_file"
done

echo "[firecrawl] source ready at $(git rev-parse --short HEAD)"

if [[ -f "$INSTALL_MARKER" ]] && [[ "$(tr -d '[:space:]' < "$INSTALL_MARKER")" == "$BUILD_ID" ]] \
    && [[ -f "$API_DIR/dist/src/index.js" ]] && [[ -f "$PLAYWRIGHT_DIR/dist/api.js" ]]; then
    echo "[firecrawl] dependencies already built"
    exit 0
fi

if [[ -s "$HOME/.nvm/nvm.sh" ]]; then
    unset npm_config_prefix
    # shellcheck disable=SC1090
    source "$HOME/.nvm/nvm.sh"
    nvm use 22 >/dev/null
elif [[ "$(node -p 'process.versions.node.split(`.`)[0]')" != "22" ]]; then
    echo "[firecrawl] Node.js 22 is required" >&2
    exit 1
fi

if ! command -v redis-server >/dev/null 2>&1; then
    if command -v brew >/dev/null 2>&1; then
        echo "[firecrawl] installing local Redis runtime…"
        brew install redis
    else
        echo "[firecrawl] redis-server is required" >&2
        exit 1
    fi
fi

export PATH="$HOME/.cargo/bin:$PATH"

if ! command -v rustup >/dev/null 2>&1; then
    echo "[firecrawl] installing Rust toolchain manager for the native parser…"
    curl --retry 3 --retry-all-errors --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
        | sh -s -- -y --profile minimal --no-modify-path
fi

if ! cargo --version >/dev/null 2>&1; then
    echo "[firecrawl] installing Rust stable toolchain for the native parser…"
    rustup set auto-self-update disable
    rustup toolchain install stable --profile minimal
    rustup default stable
fi

echo "[firecrawl] installing and building API source…"
if [[ ! -d "$API_DIR/node_modules" ]] \
    || ! find "$API_DIR/native" -maxdepth 1 -name 'firecrawl-rs.*.node' -print -quit | grep -q .; then
    pnpm --dir "$API_DIR" install --frozen-lockfile
else
    echo "[firecrawl] API dependencies already installed"
fi
(
    cd "$API_DIR/sharedLibs/go-html-to-md"
    export GOPROXY="${FIRECRAWL_GOPROXY:-https://goproxy.cn,direct}"
    go mod download
    go build -o libhtml-to-markdown.dylib -buildmode=c-shared html-to-markdown.go
)
pnpm --dir "$API_DIR" run build

echo "[firecrawl] installing and building Playwright source…"
if [[ ! -d "$PLAYWRIGHT_DIR/node_modules" ]]; then
    pnpm --dir "$PLAYWRIGHT_DIR" install --frozen-lockfile
else
    echo "[firecrawl] Playwright dependencies already installed"
fi
pnpm --dir "$PLAYWRIGHT_DIR" exec playwright install chromium
pnpm --dir "$PLAYWRIGHT_DIR" run build

printf '%s\n' "$BUILD_ID" > "$INSTALL_MARKER"
echo "[firecrawl] local source build ready"
