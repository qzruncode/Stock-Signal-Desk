# -*- coding: utf-8 -*-
"""Stocks sync endpoint tests for split list/K-line flows."""

from __future__ import annotations

from datetime import date
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
    with patch("api.middlewares.auth.is_auth_enabled", return_value=False), \
         patch("src.auth.is_auth_enabled", return_value=False):
        yield
    auth._auth_enabled = None


def _reset_state(state: dict) -> None:
    state.update({
        "status": "idle",
        "progress": 0,
        "total": 0,
        "kline_progress": 0,
        "kline_total": 0,
        "started_at": None,
        "finished_at": None,
        "message": "",
        "error": None,
    })


@pytest.fixture(autouse=True)
def reset_sync_state():
    with sync_mod._list_sync_lock:
        _reset_state(sync_mod._list_sync_state)
    with sync_mod._kline_sync_lock:
        _reset_state(sync_mod._kline_sync_state)
    with sync_mod._missing_kline_sync_lock:
        _reset_state(sync_mod._missing_kline_sync_state)
    yield
    with sync_mod._list_sync_lock:
        _reset_state(sync_mod._list_sync_state)
    with sync_mod._kline_sync_lock:
        _reset_state(sync_mod._kline_sync_state)
    with sync_mod._missing_kline_sync_lock:
        _reset_state(sync_mod._missing_kline_sync_state)


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
    with patch("api.v1.endpoints.stocks.sync._run_list_sync"):
        resp = client.post("/api/v1/stocks/sync/list")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["status"] == "running"


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


def test_get_latest_trading_day_skips_weekend():
    result = sync_mod._get_latest_trading_day(reference=date(2026, 6, 6))
    assert result.weekday() < 5
