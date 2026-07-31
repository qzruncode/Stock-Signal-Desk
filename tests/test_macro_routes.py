# -*- coding: utf-8 -*-

from datetime import date, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
import src.auth as auth


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture(autouse=True)
def disable_auth():
    """Keep macro route tests independent from local auth env state."""
    auth._auth_enabled = None
    with (
        patch("api.middlewares.auth.is_auth_enabled", return_value=False),
        patch("src.auth.is_auth_enabled", return_value=False),
    ):
        yield
    auth._auth_enabled = None


def test_macro_index_route_is_a_thin_tool_adapter(client, monkeypatch):
    """The HTTP route must delegate business logic to the registered tool."""
    today = datetime.now().strftime("%Y-%m-%d")
    payload = {
        "index_code": "000001",
        "days": 5,
        "data_time": today,
        "is_stale": False,
        "success": True,
        "history": [],
    }
    called = {}

    def fake_tool(index_code, days):
        called.update(index_code=index_code, days=days)
        return payload

    monkeypatch.setattr("src.tools.get_index_data.get_index_data", fake_tool)

    response = client.get("/api/v1/macro/index", params={"index_code": "000001", "days": 5})

    assert response.status_code == 200
    payload = response.json()
    assert response.json() == payload
    assert called == {"index_code": "000001", "days": 5}


def test_macro_series_helpers_sort_chinese_periods_and_detect_staleness():
    from api.v1.endpoints.macro._helpers import (
        is_series_stale,
        latest_series_date,
        parse_series_date,
        sort_series_records,
    )

    records = [
        {"period": "2025年07月份", "value": 49.0},
        {"period": "2026年06月份", "value": 49.7},
        {"period": "2026年01月份", "value": 49.1},
    ]
    ordered = sort_series_records(records, "period")

    assert parse_series_date("2026年06月份") == date(2026, 6, 1)
    assert parse_series_date("2026年第二季度") == date(2026, 6, 1)
    assert ordered[-1]["period"] == "2026年06月份"
    assert latest_series_date(records, "period") == "2026年06月份"
    assert is_series_stale(records, "period", max_days=120) is False


def test_bond_storage_accepts_akshare_string_dates():
    from src.storage import DatabaseManager

    DatabaseManager.reset_instance()
    try:
        db = DatabaseManager(db_url="sqlite:///:memory:")
        saved = db.save_bond_yield_daily(
            "cn",
            "10y",
            [{"date": "2026-07-15", "value": 1.82}],
        )
        rows = db.get_bond_yield_daily("cn", "10y", limit=5)
        assert saved == 1
        assert rows == [{"date": "2026-07-15", "value": 1.82}]
    finally:
        DatabaseManager.reset_instance()
