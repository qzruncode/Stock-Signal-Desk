#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
FRONTEND_DIR="$PROJECT_DIR/apps/dsa-web"
RSSHUB_DIR="$PROJECT_DIR/services/rsshub"
FIRECRAWL_DIR="$PROJECT_DIR/services/firecrawl"
SEARXNG_DIR="$PROJECT_DIR/services/searxng"
WEBFETCH_DIR="$PROJECT_DIR/services/webfetch"
BACKEND_PORT=8000
FRONTEND_PORT=5173
RSSHUB_PORT=1200
FIRECRAWL_PORT=3002
FIRECRAWL_PLAYWRIGHT_PORT=3003
FIRECRAWL_REDIS_PORT=6380
SEARXNG_PORT=8888

if [[ -n "${NVM_BIN:-}" ]]; then
    export PATH="$NVM_BIN:$PATH"
fi

log() { echo "[$(date '+%H:%M:%S')] $*"; }

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
    lsof -i ":$1" -t 2>/dev/null || true
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

stop_services() {
    local backend_pids frontend_pids rsshub_pids firecrawl_pids searxng_pids
    backend_pids=$(get_pids "$BACKEND_PORT")
    frontend_pids=$(get_pids "$FRONTEND_PORT")
    rsshub_pids=$(get_pids "$RSSHUB_PORT")
    firecrawl_pids=$(get_pids "$FIRECRAWL_PORT"; get_pids "$FIRECRAWL_PLAYWRIGHT_PORT"; get_pids "$FIRECRAWL_REDIS_PORT")
    searxng_pids=$(get_pids "$SEARXNG_PORT")

    if [[ -z "$backend_pids" && -z "$frontend_pids" && -z "$rsshub_pids" && -z "$firecrawl_pids" && -z "$searxng_pids" ]]; then
        log "没有运行中的服务"
        return 0
    fi

    stop_firecrawl
    stop_searxng
    [[ -n "$rsshub_pids" ]] && { log "停止 RSSHub (port $RSSHUB_PORT)..."; kill $rsshub_pids 2>/dev/null || true; }
    [[ -n "$backend_pids" ]] && { log "停止后端 (port $BACKEND_PORT)..."; kill $backend_pids 2>/dev/null || true; }
    [[ -n "$frontend_pids" ]] && { log "停止前端 (port $FRONTEND_PORT)..."; kill $frontend_pids 2>/dev/null || true; }

    sleep 1

    # 强制清理残留进程
    local remaining
    remaining=$(get_pids "$FIRECRAWL_PORT"; get_pids "$FIRECRAWL_PLAYWRIGHT_PORT"; get_pids "$FIRECRAWL_REDIS_PORT"; get_pids "$SEARXNG_PORT"; get_pids "$RSSHUB_PORT"; get_pids "$BACKEND_PORT"; get_pids "$FRONTEND_PORT")
    if [[ -n "$remaining" ]]; then
        log "强制清理残留进程..."
        kill -9 $remaining 2>/dev/null || true
        sleep 1
    fi

    log "服务已停止"
}

start_services() {
    if [[ -n "$(get_pids "$FIRECRAWL_PORT")" || -n "$(get_pids "$FIRECRAWL_PLAYWRIGHT_PORT")" || -n "$(get_pids "$FIRECRAWL_REDIS_PORT")" || -n "$(get_pids "$SEARXNG_PORT")" ]] || [[ -n "$(get_pids "$RSSHUB_PORT")" || -n "$(get_pids "$BACKEND_PORT")" || -n "$(get_pids "$FRONTEND_PORT")" ]]; then
        log "端口已被占用，请先运行: $0 restart"
        return 1
    fi

    mkdir -p "$PROJECT_DIR/logs"

    ensure_webfetch_runtime
    start_searxng
    start_firecrawl

    log "启动 RSSHub (port $RSSHUB_PORT)..."
    if [[ ! -d "$RSSHUB_DIR/app/.git" ]]; then
        log "初始化 RSSHub 源码与依赖..."
        (cd "$RSSHUB_DIR" && npm run install:rsshub)
    fi
    start_detached "$RSSHUB_DIR" "$PROJECT_DIR/logs/RSSHub.log" env PORT="$RSSHUB_PORT" npm start

    log "启动后端 FastAPI (port $BACKEND_PORT)..."
    start_detached "$PROJECT_DIR" "$PROJECT_DIR/logs/backend.log" \
        uvicorn server:app --reload --host 0.0.0.0 --port "$BACKEND_PORT"

    log "启动前端 Vite Dev (port $FRONTEND_PORT)..."
    start_detached "$FRONTEND_DIR" "$PROJECT_DIR/logs/frontend.log" \
        npm run dev -- --host 0.0.0.0

    wait_for_firecrawl 180 || true
    wait_for_port "$RSSHUB_PORT" "RSSHub" 90 || true
    wait_for_port "$BACKEND_PORT" "backend" 30 || true
    wait_for_port "$FRONTEND_PORT" "frontend" 30 || true

    if searxng_http_ready && firecrawl_http_ready && [[ -n "$(get_pids "$RSSHUB_PORT")" && -n "$(get_pids "$BACKEND_PORT")" && -n "$(get_pids "$FRONTEND_PORT")" ]]; then
        log "服务启动成功!"
        log "  SearXNG: http://localhost:$SEARXNG_PORT"
        log "  Firecrawl: http://localhost:$FIRECRAWL_PORT"
        log "  RSSHub: http://localhost:$RSSHUB_PORT"
        log "  后端: http://localhost:$BACKEND_PORT"
        log "  前端: http://localhost:$FRONTEND_PORT"
        log "  API文档: http://localhost:$BACKEND_PORT/docs"
    else
        log "警告: 部分服务可能未启动成功，请检查端口"
    fi
}

status() {
    local rp bp fp
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

    if [[ -n "$bp" ]]; then
        log "后端运行中 (port $BACKEND_PORT, PID: $(echo $bp | tr '\n' ' '))"
    else
        log "后端未运行"
    fi

    if [[ -n "$fp" ]]; then
        log "前端运行中 (port $FRONTEND_PORT, PID: $(echo $fp | tr '\n' ' '))"
    else
        log "前端未运行"
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
    *)
        echo "用法: $0 {start|stop|restart|status}"
        echo ""
        echo "  start    启动 SearXNG、Firecrawl、RSSHub、后端、前端服务"
        echo "  stop     停止所有服务"
        echo "  restart  重启所有服务"
        echo "  status   查看服务运行状态"
        exit 1
        ;;
esac
