# -*- coding: utf-8 -*-
"""Stocks sync endpoint tests for split list/K-line flows."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import src.auth as auth
from api.app import create_app
from api.v1.endpoints.stocks import sync as sync_mod


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture(autouse=True)
def disable_auth():
    auth._auth_enabled = None
    with (
        patch("api.middlewares.auth.is_auth_enabled", return_value=False),
        patch("src.auth.is_auth_enabled", return_value=False),
    ):
        yield
    auth._auth_enabled = None


def _reset_state(state: dict) -> None:
    state.update(sync_mod._initial_state())


@pytest.fixture(autouse=True)
def reset_sync_state():
    with sync_mod._list_sync_lock:
        _reset_state(sync_mod._list_sync_state)
    with sync_mod._kline_sync_lock:
        _reset_state(sync_mod._kline_sync_state)
    with sync_mod._missing_kline_sync_lock:
        _reset_state(sync_mod._missing_kline_sync_state)
    with sync_mod._financial_sync_lock:
        _reset_state(sync_mod._financial_sync_state)
    yield
    with sync_mod._list_sync_lock:
        _reset_state(sync_mod._list_sync_state)
    with sync_mod._kline_sync_lock:
        _reset_state(sync_mod._kline_sync_state)
    with sync_mod._missing_kline_sync_lock:
        _reset_state(sync_mod._missing_kline_sync_state)
    with sync_mod._financial_sync_lock:
        _reset_state(sync_mod._financial_sync_state)


def test_mark_list_sync_started_returns_true_when_idle():
    assert sync_mod._mark_list_sync_started() is True
    state = sync_mod._get_list_state_copy()
    assert state["status"] == "running"
    assert state["started_at"] is not None


def test_mark_list_sync_started_returns_false_when_already_running():
    sync_mod._mark_list_sync_started()
    assert sync_mod._mark_list_sync_started() is False


def test_get_list_state_copy_returns_independent_copy():
    sync_mod._set_list_state(message="hello")
    copy = sync_mod._get_list_state_copy()
    copy["message"] = "mutated"
    assert sync_mod._get_list_state_copy()["message"] == "hello"


def test_sync_stock_list_rejects_when_already_running(client):
    sync_mod._mark_list_sync_started()
    resp = client.post("/api/v1/stocks/sync/list")
    assert resp.status_code == 409


def test_sync_stock_list_starts_when_idle(client):
    with (
        patch("api.v1.endpoints.stocks.sync._latest_stock_universe_status", return_value=None),
        patch("api.v1.endpoints.stocks.sync._claim_persisted_sync_job", return_value=("list-job-1", True)),
        patch("api.v1.endpoints.stocks.sync._launch_detached_worker") as launch_worker,
    ):
        resp = client.post("/api/v1/stocks/sync/list")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["status"] == "running"
    launch_worker.assert_called_once_with("src.services.stock_list_sync_worker", "list-job-1")


def test_sync_stock_list_rejects_when_persistent_job_is_running(client):
    with patch.object(sync_mod, "_latest_stock_universe_status", return_value={"status": "running"}):
        resp = client.post("/api/v1/stocks/sync/list")

    assert resp.status_code == 409


def test_run_list_sync_forwards_progress_and_marks_success():
    changes = {
        "total": 5000,
        "added": 10,
        "updated": 4980,
        "delisted": 10,
        "delisted_daily": 0,
    }

    def fake_ensure(**kwargs):
        kwargs["on_progress"](200, 5000, "股票列表写入中 200/5000")
        return {"maintenance_status": "success", "changes": changes}

    with patch.object(sync_mod, "ensure_stock_universe", side_effect=fake_ensure), patch.object(
        sync_mod, "_set_list_state"
    ) as set_state:
        sync_mod._run_list_sync()

    assert any(call.kwargs == {"progress": 200, "total": 5000, "message": "股票列表写入中 200/5000"} for call in set_state.call_args_list)
    assert any(call.kwargs.get("status") == "success" for call in set_state.call_args_list)


def test_run_list_sync_does_not_report_stale_fallback_as_success():
    with patch.object(
        sync_mod,
        "ensure_stock_universe",
        return_value={
            "maintenance_status": "stale_fallback",
            "warning": "股票基础库自动更新未完成，当前使用本地缓存",
            "total": 5000,
        },
    ), patch.object(sync_mod, "_set_list_state") as set_state:
        sync_mod._run_list_sync()

    failed_call = next(call for call in set_state.call_args_list if call.kwargs.get("status") == "failed")
    assert failed_call.kwargs["message"] == "股票列表同步失败"
    assert "本地缓存" in failed_call.kwargs["error"]


def test_old_sync_route_removed(client):
    resp = client.post("/api/v1/stocks/sync")
    assert resp.status_code in (404, 405)


def test_list_sync_status_returns_state_when_total_positive(client):
    sync_mod._set_list_state(total=100, status="running", progress=10)
    resp = client.get("/api/v1/stocks/sync/list/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 100
    assert body["status"] == "running"


def test_list_sync_status_falls_back_to_db_when_state_empty(client):
    db = MagicMock()
    session = MagicMock()
    query = MagicMock()
    filtered = MagicMock()
    filtered.count.return_value = 5000
    query.filter.return_value = filtered
    session.query.return_value = query
    db.get_session.return_value.__enter__.return_value = session
    with patch("api.v1.endpoints.stocks.sync.DatabaseManager") as db_cls:
        db_cls.get_instance.return_value = db
        resp = client.get("/api/v1/stocks/sync/list/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5000
    assert body["status"] == "success"


def test_list_sync_status_uses_persisted_running_job(client):
    now = datetime.now()
    job = sync_mod.DataMaintenanceJob(
        id="job-1",
        dataset="stock_universe",
        status="running",
        progress=200,
        total=5000,
        message="股票列表写入中 200/5000",
        started_at=now,
        updated_at=now,
        created_at=now,
    )
    db = MagicMock()
    session = MagicMock()
    query = MagicMock()
    query.filter.return_value.order_by.return_value.first.return_value = job
    session.query.return_value = query
    db.get_session.return_value.__enter__.return_value = session

    with patch.object(sync_mod, "DatabaseManager") as db_cls:
        db_cls.get_instance.return_value = db
        resp = client.get("/api/v1/stocks/sync/list/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["progress"] == 200
    assert body["message"] == "股票列表写入中 200/5000"


def test_list_sync_status_prefers_terminal_state_for_same_job_id(client):
    sync_mod._set_list_state(
        status="running",
        job_id="job-1",
        started_at="2026-08-12T10:00:00+00:00",
        total=5000,
        progress=200,
    )
    persisted = {
        "job_id": "job-1",
        "status": "success",
        "started_at": "2026-08-12T09:59:59+00:00",
        "finished_at": "2026-08-12T10:02:00+00:00",
        "progress": 5000,
        "total": 5000,
        "message": "股票基础库已自动更新",
        "error": None,
    }
    with patch.object(sync_mod, "_latest_stock_universe_status", return_value=persisted):
        resp = client.get("/api/v1/stocks/sync/list/status")

    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    assert resp.json()["progress"] == 5000


def test_claim_persisted_kline_job_rejects_fresh_running_job():
    now = datetime.now()
    job = sync_mod.DataMaintenanceJob(
        id="kline-job-1",
        dataset="kline",
        scope_key="all",
        target_data_time="2026-08-12",
        status="running",
        started_at=now,
        updated_at=now,
        created_at=now,
    )
    db = MagicMock()
    session = MagicMock()
    query = MagicMock()
    query.filter.return_value.one_or_none.return_value = job
    session.query.return_value = query
    db.get_session.return_value.__enter__.return_value = session

    with patch.object(sync_mod, "DatabaseManager") as db_cls:
        db_cls.get_instance.return_value = db
        job_id, claimed = sync_mod._claim_persisted_sync_job(
            dataset="kline",
            scope_key="all",
            target_data_time="2026-08-12",
            total=10,
            trigger="test",
        )

    assert job_id == "kline-job-1"
    assert claimed is False
    session.commit.assert_not_called()


def test_claim_persisted_sync_job_rotates_stale_job_id_within_schema_limit():
    now = datetime.now()
    job = sync_mod.DataMaintenanceJob(
        id="old-job-id",
        dataset="kline",
        scope_key="all",
        target_data_time="2026-08-12",
        status="success",
        started_at=now,
        updated_at=now,
        created_at=now,
    )
    db = MagicMock()
    session = MagicMock()
    query = MagicMock()
    query.filter.return_value = query
    query.one_or_none.return_value = job
    query.update.return_value = 1
    session.query.return_value = query
    db.get_session.return_value.__enter__.return_value = session

    with patch.object(sync_mod, "DatabaseManager") as db_cls:
        db_cls.get_instance.return_value = db
        job_id, claimed = sync_mod._claim_persisted_sync_job(
            dataset="kline",
            scope_key="all",
            target_data_time="2026-08-12",
            total=10,
            trigger="test",
        )

    assert claimed is True
    assert job_id != job.id
    assert len(job_id) == 36
    update_values = query.update.call_args.args[0]
    assert update_values["id"] == job_id


def test_kline_status_restores_persisted_terminal_job(client):
    now = datetime.now()
    job = sync_mod.DataMaintenanceJob(
        id="kline-job-2",
        dataset="kline",
        scope_key="all",
        target_data_time="2026-08-12",
        status="success",
        progress=2,
        total=2,
        message="K线同步完成: 更新 2, 跳过 0, 失败 0",
        started_at=now,
        finished_at=now,
        created_at=now,
    )
    db = MagicMock()
    session = MagicMock()
    query = MagicMock()
    query.filter.return_value.order_by.return_value.first.return_value = job
    session.query.return_value = query
    db.get_session.return_value.__enter__.return_value = session

    with patch.object(sync_mod, "DatabaseManager") as db_cls:
        db_cls.get_instance.return_value = db
        response = client.get("/api/v1/stocks/sync/kline/status")

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == "kline-job-2"
    assert body["status"] == "success"
    assert body["kline_progress"] == 2
    assert body["kline_total"] == 2


def test_sync_stock_financial_launches_detached_worker(client):
    with (
        patch.object(sync_mod, "_get_active_stock_codes", return_value=["000001", "000002"]),
        patch.object(sync_mod, "_latest_persisted_sync_state", return_value=None),
        patch.object(sync_mod._financials_sync, "latest_report_period", return_value="20260630"),
        patch.object(sync_mod, "_claim_persisted_sync_job", return_value=("financial-job-1", True)),
        patch.object(sync_mod, "_mark_financial_sync_started", return_value=True),
        patch.object(sync_mod, "_set_financial_state"),
        patch.object(sync_mod, "_launch_detached_worker") as launch_worker,
    ):
        response = client.post("/api/v1/stocks/sync/financial")

    assert response.status_code == 200
    assert response.json()["status"] == "running"
    launch_worker.assert_called_once_with(
        "src.services.financial_sync_worker",
        "financial-job-1",
        "20260630",
        "000001,000002",
    )


def test_financial_status_restores_persisted_terminal_counts(client):
    now = datetime.now()
    job = sync_mod.DataMaintenanceJob(
        id="financial-job-1",
        dataset="financial_reports",
        scope_key="all",
        target_data_time="2026-06-30",
        status="partial",
        progress=5539,
        total=5539,
        message=(
            "最新财报同步完成：已更新 5530 / 5539 只；无可用财报 0 只；"
            "失败 0 只；9 只股票没有拿到完整核心字段，本轮未覆盖"
        ),
        started_at=now,
        finished_at=now,
        created_at=now,
    )
    db = MagicMock()
    session = MagicMock()
    query = MagicMock()
    query.filter.return_value.order_by.return_value.first.return_value = job
    session.query.return_value = query
    db.get_session.return_value.__enter__.return_value = session

    with patch.object(sync_mod, "DatabaseManager") as db_cls:
        db_cls.get_instance.return_value = db
        resp = client.get("/api/v1/stocks/sync/financial/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "partial"
    assert body["updated_count"] == 5530
    assert body["total"] == 5539
    assert body["incomplete_count"] == 9


def test_get_latest_trading_day_skips_weekend():
    result = sync_mod._get_latest_trading_day(reference=date(2026, 6, 6))
    assert result.weekday() < 5
