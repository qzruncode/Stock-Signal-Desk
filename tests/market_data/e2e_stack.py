"""Isolated real PostgreSQL/Celery/HTTP stack for API and browser QA."""

from __future__ import annotations
import argparse
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".market-data/e2e"
TOKEN = "isolated-e2e-data-token-not-a-production-secret"


def fixture_data():
    today = date.today()
    days = [today - timedelta(days=2000) + timedelta(days=i) for i in range(2400)]
    calendar = [day.isoformat() for day in days if day.weekday() < 5]
    end = (
        today.isoformat()
        if (datetime.now().hour, datetime.now().minute) >= (15, 15)
        else (today - timedelta(days=1)).isoformat()
    )
    last = max(day for day in calendar if day <= end)
    codes = [str(index).zfill(6) for index in range(1, 31)]
    from market_data_service.providers.financial_data import _expected_min_report_date

    period = _expected_min_report_date(today).isoformat()
    bars = [
        {
            "date": day,
            "open": 10 + index / 100,
            "close": 10.2 + index / 100,
            "high": 10.5 + index / 100,
            "low": 9.8 + index / 100,
            "volume": 100000 + index * 10,
            "amount": 1020000,
            "pct_chg": 0.5,
            "volume_unit": "股",
        }
        for index, day in enumerate([day for day in calendar if day <= last][-500:])
    ]
    return {
        "calendar": calendar,
        "securities": [
            {"code": code, "name": "测试证券" + code, "market": "sz"} for code in codes
        ],
        "kline": {
            code: {
                "success": True,
                "data": bars,
                "requested_count": 500,
                "count": 500,
                "source": "fixture",
                "data_time": last,
                "is_stale": False,
                "bar_complete": True,
            }
            for code in codes
        },
        "financials": {
            code: {
                "success": True,
                "data_time": period,
                "source": "fixture",
                "data": {
                    "revenue_latest": 100,
                    "net_profit_latest": 10,
                    "operating_cf_latest": 8,
                    "revenue_ttm": 200,
                    "parent_net_profit_ttm": 20,
                    "deducted_net_profit_ttm": 18,
                    "debt_ratio": 30,
                    "report_date": period,
                },
            }
            for code in codes
        },
        "quotes": {
            code: {
                "success": True,
                "items": [
                    {
                        "code": code,
                        "name": "测试证券" + code,
                        "price": 10.5,
                        "trade_time": datetime.now().isoformat(),
                    }
                ],
                "source": "fixture",
                "data_time": datetime.now().isoformat(),
            }
            for code in codes
        },
        "news": {
            code: {"success": True, "items": [], "source": "fixture"} for code in codes
        },
        "announcements": {
            code: {"success": True, "items": [], "source": "fixture"} for code in codes
        },
        "operations": {
            "rss.ui.get_rss_feeds_by_spec": {
                "items": [
                    {
                        "id": "isolated-item",
                        "title": "隔离资讯正文",
                        "link": "https://example.com/e2e",
                        "content_html": "<p>正文必须保留</p>",
                        "summary": "正文必须保留",
                    }
                ],
                "feed_title": "端到端测试订阅",
                "errors": [],
                "source": "fixture",
            },
        },
    }


def start():
    import psycopg
    from psycopg import sql

    RUNTIME.mkdir(parents=True, exist_ok=True)
    if (RUNTIME / "supervisor.sock").exists():
        raise SystemExit("E2E stack already running; stop it explicitly first")
    database = "market_data_e2e_" + uuid.uuid4().hex[:10]
    admin = os.getenv(
        "MARKET_DATA_E2E_PG_ADMIN", "postgresql://xiejiawei@127.0.0.1:5433/postgres"
    )
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database))
        )
    from sqlalchemy.engine import make_url

    database_url = (
        make_url(admin)
        .set(drivername="postgresql+psycopg", database=database)
        .render_as_string(hide_password=False)
    )
    fixture = RUNTIME / "fixture.json"
    fixture.write_text(json.dumps(fixture_data(), ensure_ascii=False))
    environment = {
        "MARKET_DATA_DATABASE_URL": database_url,
        "MARKET_DATA_BROKER_URL": "redis://127.0.0.1:6381/12",
        "MARKET_DATA_PROVIDER": "fixture",
        "MARKET_DATA_ENVIRONMENT": "development",
        "MARKET_DATA_API_TOKEN": TOKEN,
        "MARKET_DATA_FIXTURE_PATH": str(fixture),
        "MARKET_DATA_SCHEDULER_SECONDS": "5",
        "MARKET_DATA_SERVICE_URL": "http://127.0.0.1:8011",
        "MARKET_DATA_SERVICE_TOKEN": TOKEN,
        "DATABASE_URL": "sqlite:///" + str(RUNTIME / (database + "_business.db")),
        "DATABASE_PATH": str(RUNTIME / (database + "_business.db")),
        "APP_ENV": "development",
    }
    (RUNTIME / "state.json").write_text(
        json.dumps({"environment": environment, "database": database}, indent=2)
    )
    py = ROOT / ".venv-data/bin"
    commands = {
        "e2e-events": f"{py}/python -m market_data_service.event_relay",
        "e2e-api": f"{py}/uvicorn market_data_service.api:app --host 127.0.0.1 --port 8011",
        "e2e-worker": f"{py}/celery -A market_data_service.worker:celery_app worker -n e2e-sources@%%h -Q market-data --concurrency=2 --loglevel=INFO --without-mingle --without-gossip",
        "e2e-sync": f"{py}/celery -A market_data_service.worker:celery_app worker -n e2e-sync@%%h -Q market-data-sync --concurrency=2 --loglevel=INFO --without-mingle --without-gossip",
        "e2e-beat": f"{py}/celery -A market_data_service.worker:celery_app beat --schedule {RUNTIME}/beat --loglevel=INFO",
        "e2e-business": f"{os.getenv('MARKET_DATA_E2E_BUSINESS_PYTHON', '/Library/Frameworks/Python.framework/Versions/3.13/bin/python3')} -m uvicorn api.app:create_app --factory --host 127.0.0.1 --port 8001",
    }
    env = ",".join(f'{key}="{value}"' for key, value in environment.items())
    config = f"""[unix_http_server]
file={RUNTIME}/supervisor.sock
chmod=0700
[supervisord]
logfile={RUNTIME}/supervisor.log
pidfile={RUNTIME}/supervisor.pid
[rpcinterface:supervisor]
supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface
[supervisorctl]
serverurl=unix://{RUNTIME}/supervisor.sock
"""
    for name, command in commands.items():
        config += f"""\n[program:{name}]
directory={ROOT}
command={command}
environment={env}
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
stopwaitsecs=10
stdout_logfile={RUNTIME}/{name}.log
redirect_stderr=true
"""
    path = RUNTIME / "supervisord.conf"
    path.write_text(config)
    subprocess.run([str(py / "supervisord"), "-c", str(path)], check=True)
    print(f"Isolated stack started: data :8011, business :8001, PostgreSQL {database}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=["start", "stop", "status", "demo-failure", "reset-fixture"]
    )
    action = parser.parse_args().action
    if action == "start":
        start()
    elif action in {"demo-failure", "reset-fixture"}:
        state = json.loads((RUNTIME / "state.json").read_text())
        assert state["environment"]["MARKET_DATA_PROVIDER"] == "fixture"
        data = fixture_data()
        if action == "demo-failure":
            for item in data["financials"].values():
                item["delay_seconds"] = 10
            data["financials"]["000002"] = {
                "delay_seconds": 10,
                "error": "测试来源暂时不可用；恢复后可重试",
            }
        (RUNTIME / "fixture.json").write_text(json.dumps(data, ensure_ascii=False))
        print("Only isolated upstream fixture changed: " + action)
    else:
        subprocess.run(
            [
                str(ROOT / ".venv-data/bin/supervisorctl"),
                "-c",
                str(RUNTIME / "supervisord.conf"),
                "shutdown" if action == "stop" else "status",
            ],
            check=True,
        )
