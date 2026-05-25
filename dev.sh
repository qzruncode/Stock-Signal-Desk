#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
FRONTEND_DIR="$PROJECT_DIR/apps/dsa-web"
BACKEND_PORT=8000
FRONTEND_PORT=5173

log() { echo "[$(date '+%H:%M:%S')] $*"; }

get_pids() {
    lsof -i ":$1" -t 2>/dev/null || true
}

stop_services() {
    local backend_pids frontend_pids
    backend_pids=$(get_pids "$BACKEND_PORT")
    frontend_pids=$(get_pids "$FRONTEND_PORT")

    if [[ -z "$backend_pids" && -z "$frontend_pids" ]]; then
        log "没有运行中的服务"
        return 0
    fi

    [[ -n "$backend_pids" ]] && { log "停止后端 (port $BACKEND_PORT)..."; kill $backend_pids 2>/dev/null || true; }
    [[ -n "$frontend_pids" ]] && { log "停止前端 (port $FRONTEND_PORT)..."; kill $frontend_pids 2>/dev/null || true; }

    sleep 1

    # 强制清理残留进程
    local remaining
    remaining=$(get_pids "$BACKEND_PORT"; get_pids "$FRONTEND_PORT")
    if [[ -n "$remaining" ]]; then
        log "强制清理残留进程..."
        kill -9 $remaining 2>/dev/null || true
        sleep 1
    fi

    log "服务已停止"
}

start_services() {
    if [[ -n "$(get_pids "$BACKEND_PORT")" || -n "$(get_pids "$FRONTEND_PORT")" ]]; then
        log "端口已被占用，请先运行: $0 restart"
        return 1
    fi

    log "启动后端 FastAPI (port $BACKEND_PORT)..."
    (cd "$PROJECT_DIR" && uvicorn server:app --reload --host 0.0.0.0 --port "$BACKEND_PORT" &)

    log "启动前端 Vite Dev (port $FRONTEND_PORT)..."
    (cd "$FRONTEND_DIR" && npm run dev &)

    sleep 2

    if [[ -n "$(get_pids "$BACKEND_PORT")" && -n "$(get_pids "$FRONTEND_PORT")" ]]; then
        log "服务启动成功!"
        log "  后端: http://localhost:$BACKEND_PORT"
        log "  前端: http://localhost:$FRONTEND_PORT"
        log "  API文档: http://localhost:$BACKEND_PORT/docs"
    else
        log "警告: 部分服务可能未启动成功，请检查端口"
    fi
}

status() {
    local bp fp
    bp=$(get_pids "$BACKEND_PORT")
    fp=$(get_pids "$FRONTEND_PORT")

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
        echo "  start    启动前后端服务"
        echo "  stop     停止所有服务"
        echo "  restart  重启所有服务"
        echo "  status   查看服务运行状态"
        exit 1
        ;;
esac
