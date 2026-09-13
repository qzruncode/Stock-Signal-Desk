"""Focused data-service contract tests, isolated from the business database."""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_DATA_DATABASE_URL", f"sqlite:///{tmp_path}/service.db")
    monkeypatch.setenv("MARKET_DATA_BROKER_URL", "redis://127.0.0.1:6381/14")
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "fixture")
    monkeypatch.setenv("MARKET_DATA_API_TOKEN", "test-service-token")
    monkeypatch.setenv("MARKET_DATA_FIXTURE_PATH", str(tmp_path / "fixture.json"))
    from market_data_service.settings import get_settings
    from market_data_service.database import get_database
    from market_data_service.sources import registry
    from market_data_service.calendar import _calendar_bucket

    get_settings.cache_clear()
    get_database.cache_clear()
    registry.cache_clear()
    _calendar_bucket.cache_clear()
    today = date.today()
    days = [today - timedelta(days=730) + timedelta(days=i) for i in range(1460)]
    calendar = [day.isoformat() for day in days if day.weekday() < 5]
    last = max(day for day in calendar if day < today.isoformat())
    codes = ["000001", "000002", "600000"]
    data = {
        "calendar": calendar,
        "securities": [
            {
                "code": code,
                "name": "隔离测试" + code,
                "market": "sh" if code.startswith("6") else "sz",
            }
            for code in codes
        ],
        "kline": {
            code: {
                "success": True,
                "data": [
                    {
                        "date": last,
                        "open": 10,
                        "close": 11,
                        "high": 12,
                        "low": 9,
                        "volume": 10000,
                    }
                ],
                "source": "fixture",
                "data_time": last,
                "is_stale": False,
            }
            for code in codes
        },
        "financials": {
            code: {
                "success": True,
                "data_time": "2026-06-30",
                "source": "fixture",
                "data": {
                    "revenue_latest": 100,
                    "net_profit_latest": 10,
                    "operating_cf_latest": 8,
                    "revenue_ttm": 200,
                    "parent_net_profit_ttm": 20,
                    "deducted_net_profit_ttm": 18,
                    "debt_ratio": 30,
                    "report_date": "2026-06-30",
                },
            }
            for code in codes
        },
        "news": {
            code: {"success": True, "items": [], "source": "fixture"} for code in codes
        },
    }
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(data))
    from market_data_service.api import app
    from market_data_service.worker import source_task

    monkeypatch.setattr(source_task, "apply_async", lambda *args, **kwargs: None)
    with TestClient(
        app, headers={"Authorization": "Bearer test-service-token"}
    ) as client:
        yield client, get_database(), data, fixture
    get_database().engine.dispose()
    get_database.cache_clear()
    get_settings.cache_clear()
    registry.cache_clear()
    _calendar_bucket.cache_clear()


def seed(service, *datasets):
    client, _, _, _ = service
    from market_data_service.jobs import run_job

    for dataset in datasets:
        job = client.post("/v1/jobs", json={"dataset": dataset, "mode": "all"}).json()
        run_job(job["id"])


def test_auth_and_closed_contract(service):
    client, _, _, _ = service
    assert (
        client.get("/v1/health", headers={"Authorization": "Bearer wrong"}).status_code
        == 401
    )
    assert client.post("/v1/jobs", json={"dataset": "unknown"}).status_code == 422
    assert (
        client.post(
            "/v1/jobs", json={"dataset": "kline", "symbols": ["not-a-stock"]}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/observations",
            json={"operation": "os.system", "arguments": {"command": "anything"}},
        ).status_code
        == 422
    )


def test_duplicate_jobs_cancel_and_retry(service):
    client, db, _, _ = service
    from market_data_service.control_models import SyncJob

    a = client.post("/v1/jobs", json={"dataset": "securities"}).json()
    b = client.post("/v1/jobs", json={"dataset": "securities"}).json()
    assert a["id"] == b["id"] and not b["created"]
    assert client.post(f"/v1/jobs/{a['id']}/cancel").json()["status"] == "cancelled"
    retried = client.post(f"/v1/jobs/{a['id']}/retry").json()
    assert retried["id"] != a["id"] and retried["retry_of"] == a["id"]
    with db.get_session() as session:
        assert session.get(SyncJob, a["id"]).active_key is None


def test_scheduled_reference_subscription_resolves_company_name_to_code(service):
    _, database, _, _ = service
    seed(service, "securities")
    from market_data_service.control_models import SyncJob
    from market_data_service.jobs import target_symbols
    from market_data_service.sources import register_request

    register_request("news", {"symbol": "隔离测试000001"})
    job = SyncJob(id="name-subscription", dataset="news", mode="all")
    with database.get_session() as session:
        assert target_symbols(session, job) == ["000001"]


def test_atomic_financial_snapshot_versions_and_strict_reads(service):
    client, db, _, _ = service
    seed(service, "calendar", "securities", "financials")
    response = client.post(
        "/v1/snapshots",
        json={"symbols": ["000001", "000002"], "datasets": ["financials"]},
    )
    assert response.status_code == 200, response.text
    value = response.json()["items"]["000001"]
    assert value["financials"]["revenue_ttm"] == 200
    version = value["versions"]["financials"]
    assert client.get(f"/v1/observations/{version}").json()["historical"]
    from market_data_service.control_models import DataState, utcnow

    with db.session_scope() as session:
        row = session.scalar(
            select(DataState).where(
                DataState.dataset == "financials", DataState.symbol == "000001"
            )
        )
        row.last_success_at = utcnow() - timedelta(days=10)
    pending = client.post(
        "/v1/snapshots",
        json={"symbols": ["000001", "000002"], "datasets": ["financials"]},
    )
    assert pending.status_code == 202 and pending.json()["not_ready"] == {
        "000001": ["financials"]
    }
    historical = client.post(
        "/v1/snapshots",
        json={
            "symbols": ["000001"],
            "datasets": ["financials"],
            "freshness": "allow_stale",
        },
    )
    assert historical.json()["partial"] is True
    assert historical.json()["freshness"]["000001"]["financials"] == "stale"


def test_financial_period_is_replaced_as_unit_and_failed_items_retry(service):
    client, db, data, fixture = service
    seed(service, "calendar", "securities", "financials")
    data["financials"]["000001"]["data"]["deducted_net_profit_ttm"] = None
    data["financials"]["000002"] = {"error": "temporary upstream failure"}
    fixture.write_text(json.dumps(data))
    seed(service, "financials")
    job = client.get("/v1/jobs", params={"dataset": "financials"}).json()["items"][0]
    assert job["status"] == "partial" and job["failed"] == 2
    from market_data_service.models import StockMeta

    with db.get_session() as session:
        row = session.scalar(select(StockMeta).where(StockMeta.code == "000001"))
        assert row.deducted_net_profit_ttm is None  # no cross-period COALESCE
    retry = client.post(f"/v1/jobs/{job['id']}/retry").json()
    assert set(retry["symbols"]) == {"000001", "000002"}


def test_per_security_coverage_not_global_max(service):
    client, db, _, _ = service
    seed(service, "calendar", "securities")
    from market_data_service.control_models import DataState, utcnow
    from market_data_service.calendar import latest_completed_trade_day

    with db.session_scope() as session:
        for code, day in [
            ("000001", latest_completed_trade_day().isoformat()),
            ("000002", "2020-01-01"),
        ]:
            session.add(
                DataState(
                    dataset="kline",
                    symbol=code,
                    data_time=day,
                    last_success_at=utcnow(),
                    status="ready",
                )
            )
    from market_data_service.event_relay import drain

    drain(db, publisher=lambda event: None)
    result = next(
        item
        for item in client.get("/v1/datasets").json()["items"]
        if item["id"] == "kline"
    )
    assert (result["fresh"], result["stale"], result["missing"]) == (1, 1, 1)
    assert (
        client.get("/v1/datasets/kline/coverage", params={"status": "stale"}).json()[
            "items"
        ][0]["symbol"]
        == "000002"
    )
    exported = client.get("/v1/datasets/kline/coverage.csv")
    assert (
        exported.status_code == 200
        and "attachment" in exported.headers["content-disposition"]
    )


def test_policy_validation_and_update(service):
    client, db, _, _ = service
    assert (
        client.put(
            "/v1/datasets/news/policy",
            json={"enabled": True, "interval_seconds": 900, "max_age_seconds": 60},
        ).status_code
        == 422
    )
    response = client.put(
        "/v1/datasets/news/policy",
        json={"enabled": False, "interval_seconds": 120, "max_age_seconds": 240},
    )
    assert response.status_code == 200 and response.json()["enabled"] is False


def test_lease_recovery_and_duplicate_execution_fencing(service):
    client, db, _, _ = service
    from market_data_service.control_models import SyncJob, utcnow
    from market_data_service.jobs import _claim, run_job

    job = client.post("/v1/jobs", json={"dataset": "securities"}).json()
    first = _claim(job["id"])
    assert first and _claim(job["id"]) is None
    with db.session_scope() as session:
        row = session.get(SyncJob, job["id"])
        row.lease_until = utcnow() - timedelta(seconds=1)
    run_job(job["id"])
    result = client.get(f"/v1/jobs/{job['id']}").json()
    assert result["status"] == "success" and result["progress"] == 1
    run_job(job["id"])
    assert client.get(f"/v1/jobs/{job['id']}").json()["progress"] == 1


def test_late_financial_response_cannot_overwrite_newer_snapshot(service):
    client, db, data, _ = service
    seed(service, "calendar", "securities", "financials")
    from market_data_service.jobs import publish
    from market_data_service.control_models import utcnow

    payload = {
        **data["financials"]["000001"],
        "data": {**data["financials"]["000001"]["data"], "revenue_ttm": 1},
    }
    with db.session_scope() as session:
        publish(session, "financials", "000001", payload, utcnow() - timedelta(days=1))
    result = client.post(
        "/v1/snapshots", json={"symbols": ["000001"], "datasets": ["financials"]}
    ).json()
    assert result["items"]["000001"]["financials"]["revenue_ttm"] == 200


def test_job_history_pagination_filters_the_complete_history(service):
    client, _, _, _ = service
    seed(service, "securities", "calendar", "financials")
    first = client.get("/v1/jobs", params={"limit": 2}).json()
    second = client.get("/v1/jobs", params={"limit": 2, "page": 2}).json()
    assert first["total"] == second["total"] == 3
    assert len(first["items"]) == 2 and len(second["items"]) == 1
    assert not {row["id"] for row in first["items"]} & {
        row["id"] for row in second["items"]
    }
    filtered = client.get(
        "/v1/jobs", params={"dataset": "securities", "limit": 2}
    ).json()
    assert filtered["total"] == 1 and filtered["items"][0]["dataset"] == "securities"
