#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
FRONTEND_DIR="$PROJECT_DIR/apps/dsa-web"
RSSHUB_DIR="$PROJECT_DIR/services/rsshub"
FIRECRAWL_DIR="$PROJECT_DIR/services/firecrawl"
SEARXNG_DIR="$PROJECT_DIR/services/searxng"
WEBFETCH_DIR="$PROJECT_DIR/services/webfetch"
RAG_LOCAL_SCRIPT="$PROJECT_DIR/scripts/rag-local.sh"
RAG_PID_DIR="$PROJECT_DIR/data/rag/runtime/pids"
DEFAULT_BACKEND_PORT=8000
FRONTEND_PORT=5173
RSSHUB_PORT=1200
FIRECRAWL_PORT=3002
FIRECRAWL_PLAYWRIGHT_PORT=3003
FIRECRAWL_REDIS_PORT=6380
SEARXNG_PORT=8888
BACKEND_PORT_FILE="$PROJECT_DIR/logs/backend.port"
BACKEND_MANAGED_FILE="$PROJECT_DIR/logs/backend.managed"
DEV_TUNNEL="${DEV_TUNNEL:-1}"
TUNNEL_SCREEN_NAME="dsa-web-pinggy"
TUNNEL_LOG="$PROJECT_DIR/logs/frontend-tunnel.log"
TUNNEL_URL_FILE="$PROJECT_DIR/logs/frontend-tunnel.url"
TUNNEL_PID_FILE="$PROJECT_DIR/logs/frontend-tunnel.pid"
PINGGY_HOST="${PINGGY_HOST:-free.pinggy.io}"

read_env_value() {
    local key="$1"
    [[ -f "$PROJECT_DIR/.env" ]] || return 0

    awk -F= -v wanted="$key" '
        /^[[:space:]]*#/ || !index($0, "=") { next }
        {
            name = $1
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
            if (name != wanted) { next }
            value = substr($0, index($0, "=") + 1)
            sub(/^[[:space:]]+/, "", value)
            sub(/[[:space:]]+#.*$/, "", value)
            gsub(/["\047]/, "", value)
            print value
            exit
        }
    ' "$PROJECT_DIR/.env"
}

BACKEND_PORT_ENV_OVERRIDE="${BACKEND_PORT:-}"
WEBUI_PORT_ENV_OVERRIDE="${WEBUI_PORT:-}"
ENV_WEBUI_PORT="$(read_env_value WEBUI_PORT)"
CONFIGURED_BACKEND_PORT="${BACKEND_PORT_ENV_OVERRIDE:-${WEBUI_PORT_ENV_OVERRIDE:-${ENV_WEBUI_PORT:-$DEFAULT_BACKEND_PORT}}}"
BACKEND_PORT="$CONFIGURED_BACKEND_PORT"
BACKEND_MANAGED=1

if [[ -n "${NVM_BIN:-}" ]]; then
    export PATH="$NVM_BIN:$PATH"
fi

log() { echo "[$(date '+%H:%M:%S')] $*"; }

port_is_valid() {
    [[ "$1" =~ ^[0-9]+$ ]] && ((10#$1 >= 1 && 10#$1 <= 65535))
}

save_backend_state() {
    mkdir -p "$PROJECT_DIR/logs"
    printf '%s\n' "$BACKEND_PORT" > "$BACKEND_PORT_FILE"
    printf '%s\n' "$BACKEND_MANAGED" > "$BACKEND_MANAGED_FILE"
}

load_backend_state() {
    local state_port state_managed discovered

    if [[ -z "$BACKEND_PORT_ENV_OVERRIDE" && -z "$WEBUI_PORT_ENV_OVERRIDE" && -z "$ENV_WEBUI_PORT" && -s "$BACKEND_PORT_FILE" ]]; then
        state_port=$(tr -d '[:space:]' < "$BACKEND_PORT_FILE")
        if port_is_valid "$state_port"; then
            BACKEND_PORT="$state_port"
        fi
    fi

    if [[ -s "$BACKEND_MANAGED_FILE" ]]; then
        state_managed=$(tr -d '[:space:]' < "$BACKEND_MANAGED_FILE")
        if [[ "$state_managed" == "0" || "$state_managed" == "1" ]]; then
            BACKEND_MANAGED="$state_managed"
        fi
    fi

    if [[ ! -s "$BACKEND_PORT_FILE" && -z "$BACKEND_PORT_ENV_OVERRIDE" && -z "$WEBUI_PORT_ENV_OVERRIDE" && -z "$ENV_WEBUI_PORT" ]]; then
        discovered=$(find_project_backend_ports | head -n 1 || true)
        if [[ -n "$discovered" ]]; then
            BACKEND_PORT="$discovered"
            BACKEND_MANAGED=0
        fi
    fi
}

clear_backend_state() {
    rm -f "$BACKEND_PORT_FILE" "$BACKEND_MANAGED_FILE"
}

start_detached() {
    local workdir="$1"
    local logfile="$2"
    shift 2

    (
        cd "$workdir"
        nohup python3 -c '
import os
import sys

os.setsid()
os.execvp(sys.argv[1], sys.argv[1:])
' "$@" </dev/null >"$logfile" 2>&1 &
    )
}

get_pids() {
    lsof -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null || true
}

find_project_backend_ports() {
    local listener_pids pid cmd cwd

    listener_pids=$(lsof -tiTCP -sTCP:LISTEN 2>/dev/null | sort -u || true)
    while IFS= read -r pid; do
        [[ -n "$pid" ]] || continue
        cmd=$(ps -p "$pid" -o command= 2>/dev/null || true)
        if [[ "$cmd" != *"uvicorn"* || "$cmd" != *"server:app"* ]] && \
            [[ "$cmd" != *"main.py --serve"* && "$cmd" != *"main.py --webui"* ]]; then
            continue
        fi

        cwd=$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1 || true)
        [[ "$cwd" == "$PROJECT_DIR" || "$cwd" == "$PROJECT_DIR/"* ]] || continue

        lsof -a -p "$pid" -iTCP -sTCP:LISTEN -Fn 2>/dev/null | \
            sed -n 's/^n.*:\([0-9][0-9]*\)$/\1/p'
    done <<< "$listener_pids" | sort -nu
}

backend_http_ready() {
    local port="$1"
    local body

    body=$(curl --max-time 2 --silent --fail "http://127.0.0.1:$port/api/health" 2>/dev/null) || return 1
    [[ "$body" == *'"status":"ok"'* || "$body" == *'"status": "ok"'* ]]
}

find_free_backend_port() {
    local port="$1"

    while ((port <= 65535)); do
        if [[ -z "$(get_pids "$port")" ]]; then
            printf '%s\n' "$port"
            return 0
        fi
        port=$((port + 1))
    done
    return 1
}

resolve_backend_port() {
    local discovered preferred
    BACKEND_ALREADY_RUNNING=0

    if ! port_is_valid "$BACKEND_PORT"; then
        log "错误: 后端端口无效: $BACKEND_PORT"
        return 1
    fi

    # 未显式指定端口时，优先复用当前工作区已经启动的 FastAPI 后端。
    if [[ -z "$BACKEND_PORT_ENV_OVERRIDE" && -z "$WEBUI_PORT_ENV_OVERRIDE" && -z "$ENV_WEBUI_PORT" ]]; then
        discovered=$(find_project_backend_ports | head -n 1 || true)
        if [[ -n "$discovered" ]]; then
            BACKEND_PORT="$discovered"
            BACKEND_ALREADY_RUNNING=1
            log "检测到本项目后端已在 port $BACKEND_PORT 运行，将复用它"
            return 0
        fi
    fi

    # 允许通过 WEBUI_PORT 或 BACKEND_PORT 指向一个已启动且健康的后端。
    if backend_http_ready "$BACKEND_PORT"; then
        BACKEND_ALREADY_RUNNING=1
        log "检测到后端健康接口已在 port $BACKEND_PORT 就绪，将复用它"
        return 0
    fi

    # 默认端口被其他程序占用时，自动向后寻找空闲端口，并把该端口传给 Vite。
    if [[ -n "$(get_pids "$BACKEND_PORT")" ]]; then
        preferred="$BACKEND_PORT"
        BACKEND_PORT=$(find_free_backend_port "$preferred" || true)
        if [[ -z "$BACKEND_PORT" ]]; then
            log "错误: 从 port $preferred 开始没有可用的后端端口"
            return 1
        fi
        log "后端端口 $preferred 已被其他进程占用，自动改用 port $BACKEND_PORT"
    fi
}

frontend_tunnel_running() {
    local tunnel_pid
    if [[ -f "$TUNNEL_PID_FILE" ]]; then
        tunnel_pid=$(tr -d '[:space:]' < "$TUNNEL_PID_FILE")
        [[ -n "$tunnel_pid" ]] && kill -0 "$tunnel_pid" 2>/dev/null && return 0
    fi

    command -v screen >/dev/null 2>&1 || return 1
    screen -ls 2>/dev/null | grep -q "[.]$TUNNEL_SCREEN_NAME[[:space:]]"
}

extract_tunnel_urls() {
    [[ -f "$TUNNEL_LOG" ]] || return 0
    grep -Eao 'https://[[:alnum:]-]+(\.free\.pinggy\.net|\.run\.pinggy-free\.link)' "$TUNNEL_LOG" 2>/dev/null | sort -u || true
}

tunnel_http_ready() {
    local url="$1"
    local status
    status=$(curl --max-time 8 --silent --output /dev/null --write-out '%{http_code}' \
        "$url" 2>/dev/null || true)
    [[ "$status" =~ ^[1-4][0-9][0-9]$ ]]
}

wait_for_frontend_tunnel() {
    local timeout="${1:-45}"
    local waited=0
    local urls url

    while [[ "$waited" -lt "$timeout" ]]; do
        urls=$(extract_tunnel_urls)
        if [[ -n "$urls" ]]; then
            : > "$TUNNEL_URL_FILE"
            while IFS= read -r url; do
                [[ -n "$url" ]] && echo "$url" >> "$TUNNEL_URL_FILE"
            done <<< "$urls"

            while IFS= read -r url; do
                if tunnel_http_ready "$url"; then
                    log "前端公网隧道已启动: $url"
                    log "全部公网地址:"
                    sed 's/^/  /' "$TUNNEL_URL_FILE"
                    return 0
                fi
            done <<< "$urls"
        fi
        sleep 1
        waited=$((waited + 1))
    done

    log "警告: 前端公网隧道未在 ${timeout}s 内就绪，请检查 $TUNNEL_LOG"
    return 1
}

stop_frontend_tunnel() {
    local tunnel_pids screen_sessions screen_session

    screen_sessions=""
    if command -v screen >/dev/null 2>&1; then
        screen_sessions=$(screen -ls 2>/dev/null | awk -v name="$TUNNEL_SCREEN_NAME" '$1 ~ ("\\." name "$") { print $1 }' || true)
    fi
    if [[ -n "$screen_sessions" ]]; then
        log "停止前端公网隧道..."
        while IFS= read -r screen_session; do
            [[ -n "$screen_session" ]] && screen -S "$screen_session" -X quit >/dev/null 2>&1 || true
        done <<< "$screen_sessions"
    fi

    if [[ -f "$TUNNEL_PID_FILE" ]]; then
        tunnel_pids=$(tr -d '[:space:]' < "$TUNNEL_PID_FILE")
        [[ -n "$tunnel_pids" ]] && kill "$tunnel_pids" 2>/dev/null || true
    fi

    tunnel_pids=$(pgrep -f "[s]sh .*${PINGGY_HOST}.*127[.]0[.]0[.]1:${FRONTEND_PORT}" 2>/dev/null || true)
    [[ -n "$tunnel_pids" ]] && kill $tunnel_pids 2>/dev/null || true
    rm -f "$TUNNEL_URL_FILE" "$TUNNEL_PID_FILE"
    [[ -n "$screen_sessions$tunnel_pids" ]] && sleep 1
    return 0
}

start_frontend_tunnel() {
    if [[ "$DEV_TUNNEL" == "0" ]]; then
        log "已跳过前端公网隧道 (DEV_TUNNEL=0)"
        return 0
    fi

    if ! command -v ssh >/dev/null 2>&1; then
        log "警告: 未找到 ssh，无法启动前端公网隧道"
        return 1
    fi

    if ! command -v screen >/dev/null 2>&1; then
        log "警告: 未找到 screen，无法稳定保持 Pinggy 隧道"
        return 1
    fi

    stop_frontend_tunnel
    : > "$TUNNEL_LOG"

    log "启动前端公网隧道 (Pinggy -> 127.0.0.1:$FRONTEND_PORT)..."
    TUNNEL_LOG="$TUNNEL_LOG" TUNNEL_PID_FILE="$TUNNEL_PID_FILE" FRONTEND_PORT="$FRONTEND_PORT" PINGGY_HOST="$PINGGY_HOST" \
        screen -dmS "$TUNNEL_SCREEN_NAME" bash -lc \
        'echo "$$" > "$TUNNEL_PID_FILE"; exec ssh -tt -p 443 -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -R0:127.0.0.1:"$FRONTEND_PORT" "$PINGGY_HOST" >> "$TUNNEL_LOG" 2>&1' || true

    wait_for_frontend_tunnel 45
}

firecrawl_http_ready() {
    local status
    status=$(curl --max-time 2 --silent --output /dev/null --write-out '%{http_code}' \
        "http://127.0.0.1:$FIRECRAWL_PORT/" 2>/dev/null || true)
    [[ "$status" =~ ^[1-4][0-9][0-9]$ ]]
}

wait_for_firecrawl() {
    local timeout="${1:-180}"
    local waited=0
    while [[ "$waited" -lt "$timeout" ]]; do
        if firecrawl_http_ready; then
            log "Firecrawl 已启动 (port $FIRECRAWL_PORT)"
            return 0
        fi
        sleep 2
        waited=$((waited + 2))
    done
    log "警告: Firecrawl 未在 ${timeout}s 内就绪，请检查 logs/Firecrawl.log"
    return 1
}

searxng_http_ready() {
    local status
    status=$(curl --max-time 2 --silent --output /dev/null --write-out '%{http_code}' \
        "http://127.0.0.1:$SEARXNG_PORT/config" 2>/dev/null || true)
    [[ "$status" == "200" ]]
}

wait_for_searxng() {
    local timeout="${1:-60}"
    local waited=0
    while [[ "$waited" -lt "$timeout" ]]; do
        if searxng_http_ready; then
            log "SearXNG 已启动 (port $SEARXNG_PORT)"
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done
    log "错误: SearXNG 未在 ${timeout}s 内就绪，请检查 logs/SearXNG.log"
    return 1
}

start_searxng() {
    log "启动 SearXNG (port $SEARXNG_PORT)..."
    log "检查 SearXNG 源码与运行时..."
    (cd "$SEARXNG_DIR" && npm run install:searxng)
    start_detached "$SEARXNG_DIR" "$PROJECT_DIR/logs/SearXNG.log" \
        bash ./scripts/start-searxng.sh
    wait_for_searxng 60
}

start_firecrawl() {
    log "启动 Firecrawl (port $FIRECRAWL_PORT)..."
    log "检查 Firecrawl 源码与构建..."
    (cd "$FIRECRAWL_DIR" && npm run install:firecrawl)
    start_detached "$FIRECRAWL_DIR" "$PROJECT_DIR/logs/Firecrawl.log" \
        bash ./scripts/start-firecrawl.sh
}

ensure_webfetch_runtime() {
    log "检查 Scrapling / Patchright 抓取运行时..."
    (cd "$WEBFETCH_DIR" && bash ./scripts/install-runtime.sh)
}

stop_firecrawl() {
    local pids supervisor_pid
    pids=$(get_pids "$FIRECRAWL_PORT"; get_pids "$FIRECRAWL_PLAYWRIGHT_PORT"; get_pids "$FIRECRAWL_REDIS_PORT")
    supervisor_pid=""
    [[ -f "$PROJECT_DIR/logs/firecrawl-supervisor.pid" ]] && supervisor_pid=$(tr -d '[:space:]' < "$PROJECT_DIR/logs/firecrawl-supervisor.pid")
    if [[ -n "$pids" || -n "$supervisor_pid" ]]; then
        log "停止 Firecrawl (port $FIRECRAWL_PORT)..."
        [[ -n "$pids" ]] && kill $pids 2>/dev/null || true
        [[ -n "$supervisor_pid" ]] && kill "$supervisor_pid" 2>/dev/null || true
        rm -f "$PROJECT_DIR/logs/firecrawl-supervisor.pid"
    fi
}

stop_searxng() {
    local pids supervisor_pid
    pids=$(get_pids "$SEARXNG_PORT")
    supervisor_pid=""
    [[ -f "$PROJECT_DIR/logs/searxng-supervisor.pid" ]] && supervisor_pid=$(tr -d '[:space:]' < "$PROJECT_DIR/logs/searxng-supervisor.pid")
    if [[ -n "$pids" || -n "$supervisor_pid" ]]; then
        log "停止 SearXNG (port $SEARXNG_PORT)..."
        [[ -n "$supervisor_pid" ]] && kill "$supervisor_pid" 2>/dev/null || true
        [[ -n "$pids" ]] && kill $pids 2>/dev/null || true
        rm -f "$PROJECT_DIR/logs/searxng-supervisor.pid"
    fi
}

wait_for_port() {
    local port="$1"
    local name="$2"
    local timeout="${3:-30}"
    local waited=0
    while [[ "$waited" -lt "$timeout" ]]; do
        if [[ -n "$(get_pids "$port")" ]]; then
            log "$name 已启动 (port $port)"
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done
    log "警告: $name 未在 ${timeout}s 内启动，请检查 logs/${name}.log"
    return 1
}

wait_for_backend() {
    local timeout="${1:-30}"
    local waited=0

    while [[ "$waited" -lt "$timeout" ]]; do
        if backend_http_ready "$BACKEND_PORT"; then
            log "backend 已启动 (port $BACKEND_PORT)"
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done

    log "警告: backend 未在 ${timeout}s 内通过健康检查，请检查 logs/backend.log"
    return 1
}

stop_services() {
    local backend_pids frontend_pids rsshub_pids firecrawl_pids searxng_pids tunnel_pids rag_pid_files
    load_backend_state
    backend_pids=$(get_pids "$BACKEND_PORT")
    frontend_pids=$(get_pids "$FRONTEND_PORT")
    rsshub_pids=$(get_pids "$RSSHUB_PORT")
    firecrawl_pids=$(get_pids "$FIRECRAWL_PORT"; get_pids "$FIRECRAWL_PLAYWRIGHT_PORT"; get_pids "$FIRECRAWL_REDIS_PORT")
    searxng_pids=$(get_pids "$SEARXNG_PORT")
    tunnel_pids=$(pgrep -f "[s]sh .*${PINGGY_HOST}.*127[.]0[.]0[.]1:${FRONTEND_PORT}" 2>/dev/null || true)
    rag_pid_files=$(find "$RAG_PID_DIR" -maxdepth 1 -type f -name '*.pid' -print 2>/dev/null || true)

    if [[ -z "$backend_pids" && -z "$frontend_pids" && -z "$rsshub_pids" && -z "$firecrawl_pids" && -z "$searxng_pids" && -z "$tunnel_pids" && -z "$rag_pid_files" ]] && ! frontend_tunnel_running; then
        log "没有运行中的服务"
        return 0
    fi

    stop_frontend_tunnel
    if [[ -n "$rag_pid_files" ]]; then
        bash "$RAG_LOCAL_SCRIPT" stop || log "警告: 本机 RAG 服务未能全部优雅停止"
    fi
    stop_firecrawl
    stop_searxng
    [[ -n "$rsshub_pids" ]] && log "保留独立数据来源 RSSHub (port $RSSHUB_PORT)，业务停止不影响资讯采集"
    if [[ -n "$backend_pids" ]]; then
        if [[ "$BACKEND_MANAGED" == "1" ]]; then
            log "停止后端 (port $BACKEND_PORT)..."
            kill $backend_pids 2>/dev/null || true
        else
            log "保留已存在的后端 (port $BACKEND_PORT)，该进程不是由本次脚本启动"
        fi
    fi
    [[ -n "$frontend_pids" ]] && { log "停止前端 (port $FRONTEND_PORT)..."; kill $frontend_pids 2>/dev/null || true; }

    sleep 1

    # 强制清理残留进程
    local remaining
    if [[ "$BACKEND_MANAGED" == "1" ]]; then
        remaining=$(get_pids "$FIRECRAWL_PORT"; get_pids "$FIRECRAWL_PLAYWRIGHT_PORT"; get_pids "$FIRECRAWL_REDIS_PORT"; get_pids "$SEARXNG_PORT"; get_pids "$BACKEND_PORT"; get_pids "$FRONTEND_PORT")
    else
        remaining=$(get_pids "$FIRECRAWL_PORT"; get_pids "$FIRECRAWL_PLAYWRIGHT_PORT"; get_pids "$FIRECRAWL_REDIS_PORT"; get_pids "$SEARXNG_PORT"; get_pids "$FRONTEND_PORT")
    fi
    if [[ -n "$remaining" ]]; then
        log "强制清理残留进程..."
        kill -9 $remaining 2>/dev/null || true
        sleep 1
    fi

    log "服务已停止"
    clear_backend_state
    BACKEND_PORT="$CONFIGURED_BACKEND_PORT"
    BACKEND_MANAGED=1
}

start_services() {
    resolve_backend_port || return 1
    if [[ "${DEV_MARKET_DATA:-1}" == "1" ]]; then
        log "确认独立数据服务及自动采集进程..."
        bash "$PROJECT_DIR/market_data_service/manage.sh" start || return 1
    fi

    if [[ -n "$(get_pids "$FIRECRAWL_PORT")" || -n "$(get_pids "$FIRECRAWL_PLAYWRIGHT_PORT")" || -n "$(get_pids "$FIRECRAWL_REDIS_PORT")" || -n "$(get_pids "$SEARXNG_PORT")" ]] || [[ -n "$(get_pids "$FRONTEND_PORT")" ]]; then
        log "端口已被占用，请先运行: $0 restart"
        return 1
    fi

    mkdir -p "$PROJECT_DIR/logs"

    log "启动本机原生 PDF RAG 服务（不使用 Docker）..."
    bash "$RAG_LOCAL_SCRIPT" start || return 1

    ensure_webfetch_runtime
    start_searxng
    start_firecrawl

    if [[ -z "$(get_pids "$RSSHUB_PORT")" ]]; then
    log "启动 RSSHub (port $RSSHUB_PORT)..."
    if [[ ! -d "$RSSHUB_DIR/app/.git" ]]; then
        log "初始化 RSSHub 源码与依赖..."
        if [[ -s "$HOME/.nvm/nvm.sh" ]]; then
            (
                cd "$RSSHUB_DIR"
                bash -lc 'source "$HOME/.nvm/nvm.sh"; nvm use 24 >/dev/null; npm run install:rsshub'
            )
        else
            (cd "$RSSHUB_DIR" && npm run install:rsshub)
        fi
    fi
    if [[ -s "$HOME/.nvm/nvm.sh" ]]; then
        start_detached "$RSSHUB_DIR" "$PROJECT_DIR/logs/RSSHub.log" \
            env PORT="$RSSHUB_PORT" bash -lc 'source "$HOME/.nvm/nvm.sh"; nvm use 24 >/dev/null; exec npm start'
    else
        start_detached "$RSSHUB_DIR" "$PROJECT_DIR/logs/RSSHub.log" env PORT="$RSSHUB_PORT" npm start
    fi
    else
        log "复用已运行的独立数据来源 RSSHub"
    fi

    if [[ "$BACKEND_ALREADY_RUNNING" == "1" ]]; then
        BACKEND_MANAGED=0
    else
        BACKEND_MANAGED=1
        log "启动后端 FastAPI (port $BACKEND_PORT)..."
        start_detached "$PROJECT_DIR" "$PROJECT_DIR/logs/backend.log" \
            uvicorn server:app \
                --reload \
                --reload-dir "$PROJECT_DIR/api" \
                --reload-dir "$PROJECT_DIR/src" \
                --timeout-graceful-shutdown 3 \
                --host 0.0.0.0 \
                --port "$BACKEND_PORT"
    fi
    save_backend_state

    log "启动前端 Vite Dev (port $FRONTEND_PORT)..."
    local api_proxy_target="${VITE_API_PROXY_TARGET:-http://127.0.0.1:$BACKEND_PORT}"
    if [[ "$DEV_TUNNEL" == "0" ]]; then
        if [[ -s "$HOME/.nvm/nvm.sh" ]]; then
            start_detached "$FRONTEND_DIR" "$PROJECT_DIR/logs/frontend.log" \
                env VITE_API_PROXY_TARGET="$api_proxy_target" bash -lc 'source "$HOME/.nvm/nvm.sh"; nvm use 24 >/dev/null; npm run dev -- --host 0.0.0.0'
        else
            start_detached "$FRONTEND_DIR" "$PROJECT_DIR/logs/frontend.log" \
                env VITE_API_PROXY_TARGET="$api_proxy_target" npm run dev -- --host 0.0.0.0
        fi
    else
        if [[ -s "$HOME/.nvm/nvm.sh" ]]; then
            start_detached "$FRONTEND_DIR" "$PROJECT_DIR/logs/frontend.log" \
                env VITE_API_PROXY_TARGET="$api_proxy_target" bash -lc 'source "$HOME/.nvm/nvm.sh"; nvm use 24 >/dev/null; npm run dev:tunnel'
        else
            start_detached "$FRONTEND_DIR" "$PROJECT_DIR/logs/frontend.log" \
                env VITE_API_PROXY_TARGET="$api_proxy_target" npm run dev:tunnel
        fi
    fi

    wait_for_firecrawl 180 || true
    wait_for_port "$RSSHUB_PORT" "RSSHub" 90 || true
    wait_for_backend 30 || true
    wait_for_port "$FRONTEND_PORT" "frontend" 30 || true
    if [[ -n "$(get_pids "$FRONTEND_PORT")" ]]; then
        start_frontend_tunnel || true
    fi

    if searxng_http_ready && firecrawl_http_ready && backend_http_ready "$BACKEND_PORT" && [[ -n "$(get_pids "$RSSHUB_PORT")" && -n "$(get_pids "$FRONTEND_PORT")" ]]; then
        log "服务启动成功!"
        log "  SearXNG: http://localhost:$SEARXNG_PORT"
        log "  Firecrawl: http://localhost:$FIRECRAWL_PORT"
        log "  RSSHub: http://localhost:$RSSHUB_PORT"
        log "  后端: http://localhost:$BACKEND_PORT"
        log "  前端: http://localhost:$FRONTEND_PORT"
        if [[ -f "$TUNNEL_URL_FILE" ]]; then
            log "  前端公网:"
            sed 's/^/    /' "$TUNNEL_URL_FILE"
        fi
        log "  API文档: http://localhost:$BACKEND_PORT/docs"
    else
        log "警告: 部分服务可能未启动成功，请检查端口"
    fi
}

status() {
    local rp bp fp
    load_backend_state
    if [[ -x "$PROJECT_DIR/.venv-data/bin/supervisorctl" ]]; then
        "$PROJECT_DIR/.venv-data/bin/supervisorctl" -c "$PROJECT_DIR/market_data_service/supervisord.conf" status || true
    fi
    bash "$RAG_LOCAL_SCRIPT" status || true
    rp=$(get_pids "$RSSHUB_PORT")
    bp=$(get_pids "$BACKEND_PORT")
    fp=$(get_pids "$FRONTEND_PORT")

    if searxng_http_ready; then
        log "SearXNG 运行中 (port $SEARXNG_PORT)"
    else
        log "SearXNG 未运行"
    fi

    if firecrawl_http_ready; then
        log "Firecrawl 运行中 (port $FIRECRAWL_PORT)"
    else
        log "Firecrawl 未运行"
    fi

    if [[ -n "$rp" ]]; then
        log "RSSHub 运行中 (port $RSSHUB_PORT, PID: $(echo $rp | tr '\n' ' '))"
    else
        log "RSSHub 未运行"
    fi

    if backend_http_ready "$BACKEND_PORT"; then
        log "后端运行中 (port $BACKEND_PORT, PID: $(echo $bp | tr '\n' ' '))"
    elif [[ -n "$bp" ]]; then
        log "后端端口被占用但健康检查未通过 (port $BACKEND_PORT, PID: $(echo $bp | tr '\n' ' '))"
    else
        log "后端未运行 (默认/配置端口 $BACKEND_PORT)"
    fi

    if [[ -n "$fp" ]]; then
        log "前端运行中 (port $FRONTEND_PORT, PID: $(echo $fp | tr '\n' ' '))"
    else
        log "前端未运行"
    fi

    if frontend_tunnel_running; then
        log "前端公网隧道运行中"
        if [[ -f "$TUNNEL_URL_FILE" ]]; then
            sed 's/^/  /' "$TUNNEL_URL_FILE"
        else
            local urls
            urls=$(extract_tunnel_urls)
            if [[ -n "$urls" ]]; then
                printf '%s\n' "$urls" | sed 's/^/  /'
            else
                log "  地址尚未解析，请检查 $TUNNEL_LOG"
            fi
        fi
    else
        log "前端公网隧道未运行"
    fi
}

case "${1:-}" in
    start)
        start_services
        ;;
    stop)
        stop_services
        ;;
    restart)
        stop_services
        start_services
        ;;
    status)
        status
        ;;
    tunnel)
        mkdir -p "$PROJECT_DIR/logs"
        if [[ -z "$(get_pids "$FRONTEND_PORT")" ]]; then
            log "错误: 前端未运行，请先运行: $0 start"
            exit 1
        fi
        start_frontend_tunnel
        ;;
    *)
        echo "用法: $0 {start|stop|restart|status|tunnel}"
        echo ""
        echo "  start    启动 SearXNG、Firecrawl、RSSHub、后端、前端服务，并默认创建前端公网隧道"
        echo "  stop     停止业务服务和前端公网隧道（独立数据服务继续维护）"
        echo "  restart  重启所有服务和前端公网隧道"
        echo "  status   查看服务和前端公网隧道状态"
        echo "  tunnel   在前端已运行时重建并打印前端公网隧道"
        echo ""
        echo "环境变量:"
        echo "  DEV_MARKET_DATA=0 使用远端/外部部署的数据服务，不启动本地采集"
        echo "  DEV_TUNNEL=0     跳过前端公网隧道"
        echo "  PINGGY_HOST=...  覆盖 Pinggy SSH 入口，默认 free.pinggy.io"
        echo "  BACKEND_PORT=... 指定后端端口，未指定时读取 WEBUI_PORT/.env 或自动选择"
        echo "  WEBUI_PORT=...   指定后端端口（与 main.py 配置保持一致）"
        echo "  VITE_API_PROXY_TARGET=... 覆盖前端开发代理目标"
        exit 1
        ;;
esac
