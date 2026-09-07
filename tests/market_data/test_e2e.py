"""Opt-in HTTP E2E against the real isolated stack (no queue/provider mocks)."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
from datetime import timedelta

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("MARKET_DATA_E2E") != "1",
    reason="Requires explicitly started isolated E2E stack",
)
ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".market-data/e2e"


def eventually(fn, timeout=45):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = fn()
            if last:
                return last
        except (httpx.RequestError, AssertionError):
            pass
        time.sleep(0.5)
    raise AssertionError(f"Condition not reached in {timeout}s: {last}")


def control(action, name):
    subprocess.run(
        [
            str(ROOT / ".venv-data/bin/supervisorctl"),
            "-c",
            str(RUNTIME / "supervisord.conf"),
            action,
            name,
        ],
        check=True,
        capture_output=True,
        timeout=25,
    )


def business_python(code):
    result = subprocess.run(
        [
            os.getenv(
                "MARKET_DATA_E2E_BUSINESS_PYTHON",
                "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3",
            ),
            "-c",
            code,
        ],
        cwd=ROOT,
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        timeout=40,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture(scope="module")
def stack():
    state = json.loads((RUNTIME / "state.json").read_text())
    env = state["environment"]
    assert env["MARKET_DATA_PROVIDER"] == "fixture" and state["database"].startswith(
        "market_data_e2e_"
    )
    os.environ.update(env)
    from market_data_service.settings import get_settings
    from market_data_service.database import get_database
    from market_data_service.sources import registry

    get_settings.cache_clear()
    get_database.cache_clear()
    registry.cache_clear()
    with (
        httpx.Client(
            base_url="http://127.0.0.1:8011",
            headers={"Authorization": "Bearer " + env["MARKET_DATA_API_TOKEN"]},
            timeout=35,
        ) as data,
        httpx.Client(base_url="http://127.0.0.1:8001", timeout=35) as business,
    ):
        assert data.get("/v1/health").json()["provider"] == "fixture"
        yield data, business, get_database(), env


def terminal(data, job_id):
    def poll():
        value = data.get(f"/v1/jobs/{job_id}").json()
        return value if value["status"] not in {"queued", "running"} else None

    return eventually(poll)


def test_01_automatic_bootstrap_and_database_isolation(stack):
    data, business, _, env = stack
    for dataset in ["calendar", "securities", "financials", "kline"]:
        assert eventually(
            lambda: any(
                job["status"] == "success" and job["trigger"] == "scheduled"
                for job in data.get("/v1/jobs", params={"dataset": dataset}).json()[
                    "items"
                ]
            )
        )
    result = data.post(
        "/v1/snapshots",
        json={"symbols": ["000001"], "datasets": ["financials", "kline"], "count": 120},
    )
    assert result.status_code == 200, result.text
    item = result.json()["items"]["000001"]
    assert len(item["kline"]) == 120 and item["financials"]["revenue_ttm"] == 200
    assert data.get("/v1/observations/" + item["versions"]["kline"]).json()[
        "historical"
    ]
    with sqlite3.connect(
        f"file:{env['DATABASE_PATH']}?mode=ro", uri=True
    ) as connection:
        assert connection.execute("SELECT count(*) FROM stock_meta").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM stock_daily").fetchone()[0] == 0
    assert (
        business.get("/api/v1/data-service/datasets").json()["service"]["database"]
        == "postgresql"
    )


def test_02_business_http_contracts_and_rich_rss(stack):
    data, business, _, _ = stack
    response = business.post(
        "/api/v1/stocks/fundamental-filter", json={"codes": ["000001"]}
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["000001"]["revenue_ttm"] == 200
    response = business.post(
        "/api/v1/rss/feeds",
        json={"route_path": "/wallstreetcn/news/global", "limit": 10},
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["content_html"] == "<p>正文必须保留</p>"
    page = business.get(
        "/api/v1/data-service/datasets/financials/coverage",
        params={"page": 2, "page_size": 20},
    ).json()
    assert page["total"] == 30 and len(page["items"]) == 10
    csv = business.get("/api/v1/data-service/datasets/financials/coverage.csv")
    assert (
        csv.status_code == 200
        and "000030" in csv.text
        and "attachment" in csv.headers["content-disposition"]
    )
    business_python(
        'from src.tools._kline import get_kline; result = get_kline("000001", count=120); assert len(result["data"]) == 120 and result["data_service"]["status"] == "fresh"'
    )


def test_03_cold_read_to_durable_observation(stack):
    data, _, _, _ = stack
    arguments = {"symbol": "000019"}
    request = {"operation": "news", "arguments": arguments}
    response = data.post("/v1/observations", json=request)
    assert response.status_code in {200, 202}
    ready = eventually(
        lambda: (
            r.json()
            if (r := data.post("/v1/observations", json=request)).status_code == 200
            else None
        )
    )
    assert ready["items"] == [] and ready["data_service"]["version"]
    assert data.get("/v1/datasets/news/coverage").json()["total"] >= 1


def test_04_failure_stale_guard_and_only_failed_retry(stack):
    data, _, database, env = stack
    from sqlalchemy import select
    from market_data_service.control_models import DataState, utcnow

    path = Path(env["MARKET_DATA_FIXTURE_PATH"])
    original = json.loads(path.read_text())
    changed = json.loads(path.read_text())
    changed["financials"]["000002"] = {"error": "E2E injected upstream outage"}
    path.write_text(json.dumps(changed))
    try:
        with database.session_scope() as session:
            state = session.scalar(
                select(DataState).where(
                    DataState.dataset == "financials", DataState.symbol == "000002"
                )
            )
            state.last_success_at = utcnow() - timedelta(days=10)
        body = {"symbols": ["000001", "000002"], "datasets": ["financials"]}
        response = data.post("/v1/snapshots", json=body)
        assert response.status_code == 202 and response.json()["not_ready"] == {
            "000002": ["financials"]
        }
        historical = data.post(
            "/v1/snapshots", json={**body, "freshness": "allow_stale"}
        ).json()
        assert (
            historical["partial"]
            and historical["freshness"]["000002"]["financials"] == "stale"
        )
        job = data.post(
            "/v1/jobs",
            json={
                "dataset": "financials",
                "symbols": ["000001", "000002"],
                "mode": "all",
            },
        ).json()
        done = terminal(data, job["id"])
        assert (
            done["status"] == "partial"
            and done["failed"] == 1
            and done["succeeded"] == 1
        )
    finally:
        path.write_text(json.dumps(original))
    retry = data.post(f"/v1/jobs/{job['id']}/retry").json()
    assert retry["symbols"] == ["000002"]
    assert terminal(data, retry["id"])["status"] == "success"


def test_05_queued_and_running_cancellation_and_worker_restart(stack):
    data, _, _, env = stack
    path = Path(env["MARKET_DATA_FIXTURE_PATH"])
    original = json.loads(path.read_text())
    changed = json.loads(path.read_text())
    for item in changed["financials"].values():
        item["delay_seconds"] = 4
    path.write_text(json.dumps(changed))
    control("stop", "e2e-sync")
    try:
        request = {
            "dataset": "financials",
            "symbols": [
                "000010",
                "000011",
                "000012",
                "000013",
                "000014",
                "000015",
                "000016",
                "000017",
            ],
            "mode": "all",
        }
        job = data.post("/v1/jobs", json=request).json()
        assert data.post("/v1/jobs", json=request).json()["id"] == job["id"]
        assert data.post(f"/v1/jobs/{job['id']}/cancel").json()["status"] == "cancelled"
        job = data.post(f"/v1/jobs/{job['id']}/retry").json()
        assert job["status"] == "queued"
        control("start", "e2e-sync")
        eventually(
            lambda: data.get(f"/v1/jobs/{job['id']}").json()["status"] == "running"
        )
        assert data.post(f"/v1/jobs/{job['id']}/cancel").json()["cancel_requested"]
        assert terminal(data, job["id"])["status"] == "cancelled"
    finally:
        path.write_text(json.dumps(original))
        subprocess.run(
            [
                str(ROOT / ".venv-data/bin/supervisorctl"),
                "-c",
                str(RUNTIME / "supervisord.conf"),
                "start",
                "e2e-sync",
            ],
            capture_output=True,
        )
    retried = data.post(f"/v1/jobs/{job['id']}/retry").json()
    assert terminal(data, retried["id"])["status"] == "success"


def test_06_policy_survives_api_restart_and_scheduler_works_without_business(stack):
    data, business, database, env = stack
    from sqlalchemy import select
    from market_data_service.control_models import DataState, utcnow

    previous = next(
        item["policy"]
        for item in data.get("/v1/datasets").json()["items"]
        if item["id"] == "financials"
    )
    body = {"enabled": True, "interval_seconds": 30, "max_age_seconds": 30}
    assert data.put("/v1/datasets/financials/policy", json=body).status_code == 200
    control("restart", "e2e-api")
    eventually(lambda: data.get("/v1/health").status_code == 200)
    assert (
        next(
            item
            for item in data.get("/v1/datasets").json()["items"]
            if item["id"] == "financials"
        )["policy"]["interval_seconds"]
        == 30
    )
    control("stop", "e2e-business")
    try:
        with database.session_scope() as session:
            state = session.scalar(
                select(DataState).where(
                    DataState.dataset == "financials", DataState.symbol == "000030"
                )
            )
            state.last_success_at = utcnow() - timedelta(days=10)

        def refreshed():
            with database.get_session() as session:
                row = session.scalar(
                    select(DataState).where(
                        DataState.dataset == "financials", DataState.symbol == "000030"
                    )
                )
                return row.last_success_at > utcnow() - timedelta(seconds=25)

        eventually(refreshed)
        latest = data.get("/v1/jobs", params={"dataset": "financials"}).json()["items"][
            0
        ]
        assert latest["trigger"] == "scheduled"
    finally:
        data.put(
            "/v1/datasets/financials/policy", json={key: previous[key] for key in body}
        )
        control("start", "e2e-business")
    eventually(lambda: business.get("/api/health").status_code == 200)


def test_07_unavailable_service_fails_closed_in_business_tools(stack):
    data, _, _, _ = stack
    control("stop", "e2e-api")
    try:
        business_python(
            'from src.services.market_data_client import MarketDataError\nfrom src.tools._kline import get_kline\ntry:\n    get_kline("000001", count=30)\nexcept MarketDataError as exc:\n    assert "无法连接独立数据服务" in str(exc)\nelse:\n    raise AssertionError("Service unavailable but tool returned data")'
        )
    finally:
        control("start", "e2e-api")
    eventually(lambda: data.get("/v1/health").status_code == 200)
