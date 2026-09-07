#!/usr/bin/env bash
# Native development lifecycle. Production deployments use compose.yaml.
set -euo pipefail
DATA_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DATA_ROOT"
DATA_PY="$DATA_ROOT/.venv-data/bin/python"
DATA_CONF="$DATA_ROOT/market_data_service/supervisord.conf"

local_infra() {
    # Only revive the cluster explicitly initialized for this project. Never
    # touch a system/default cluster or change an existing database.
    if [[ -f "$DATA_ROOT/.market-data/postgres/PG_VERSION" ]]; then
        DATA_PG_BIN="$(command -v pg_ctl || true)"
        if [[ -z "$DATA_PG_BIN" ]] && command -v brew >/dev/null; then
            DATA_PG_BIN="$(brew --prefix postgresql@17)/bin/pg_ctl"
        fi
        if [[ -x "$DATA_PG_BIN" ]] && ! "$DATA_PG_BIN" -D "$DATA_ROOT/.market-data/postgres" status >/dev/null 2>&1; then
            "$DATA_PG_BIN" -D "$DATA_ROOT/.market-data/postgres" -l "$DATA_ROOT/.market-data/logs/postgres.log" \
                -o "-h 127.0.0.1 -p 5433 -k $DATA_ROOT/.market-data/run" -w start
        fi
        if ! redis-cli -h 127.0.0.1 -p 6381 ping >/dev/null 2>&1; then
            redis-server --bind 127.0.0.1 --port 6381 --appendonly yes --daemonize yes \
                --dir "$DATA_ROOT/.market-data" --pidfile "$DATA_ROOT/.market-data/run/redis.pid" \
                --logfile "$DATA_ROOT/.market-data/logs/redis.log"
        fi
    fi
}

case "${1:-status}" in
    install)
        [[ -x "$DATA_PY" ]] || python3 -m venv "$DATA_ROOT/.venv-data"
        "$DATA_PY" -m pip install -r market_data_service/requirements.lock
        ;;
    start)
        [[ -x "$DATA_PY" ]] || { echo 'Run: bash market_data_service/manage.sh install'; exit 1; }
        mkdir -p .market-data/run .market-data/logs
        local_infra
        "$DATA_PY" -m market_data_service.cli init
        if .venv-data/bin/supervisorctl -c "$DATA_CONF" pid >/dev/null 2>&1; then
            .venv-data/bin/supervisorctl -c "$DATA_CONF" reread
            .venv-data/bin/supervisorctl -c "$DATA_CONF" update
            .venv-data/bin/supervisorctl -c "$DATA_CONF" start all
        else
            .venv-data/bin/supervisord -c "$DATA_CONF"
        fi
        ;;
    stop)
        .venv-data/bin/supervisorctl -c "$DATA_CONF" shutdown
        echo 'Data API/workers/scheduler stopped. PostgreSQL, Redis and all data are retained.'
        ;;
    restart)
        "$DATA_PY" -m market_data_service.cli init
        .venv-data/bin/supervisorctl -c "$DATA_CONF" reread
        .venv-data/bin/supervisorctl -c "$DATA_CONF" update
        .venv-data/bin/supervisorctl -c "$DATA_CONF" restart all
        ;;
    status)
        .venv-data/bin/supervisorctl -c "$DATA_CONF" status
        "$DATA_PY" -m market_data_service.cli health
        ;;
    *) echo 'Usage: bash market_data_service/manage.sh {install|start|stop|restart|status}'; exit 2 ;;
esac
