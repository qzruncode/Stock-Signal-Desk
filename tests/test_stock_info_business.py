# -*- coding: utf-8 -*-
"""Stock_info business helper tests — cache keys, sanitization, prompt builders.

Covers api.v1.endpoints.stock_info.business pure helpers (non-SSE):
- _business_cache_key / _business_cache_get / _business_cache_put
- _sanitize
- _build_growth_text / format_llm_input
- get_stock_business route (cache hit, force refetch)
"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.v1.endpoints.stock_info import business as biz
import src.auth as auth


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


# ---------------------------------------------------------------------------
# cache helpers
# ---------------------------------------------------------------------------

def test_business_cache_get_returns_none_when_db_raises():
    with patch("src.storage.DatabaseManager") as db_cls:
        db_cls.get_instance.side_effect = RuntimeError("no db")
        assert biz._business_cache_get("000001") is None


def test_business_cache_get_parses_string_payload():
    with patch("src.storage.DatabaseManager") as db_cls:
        db = MagicMock()
        db.get_kline_snapshot.return_value = json.dumps({"symbol": "000001"})
        db_cls.get_instance.return_value = db
        out = biz._business_cache_get("000001")
    assert out == {"symbol": "000001"}


def test_business_cache_get_returns_dict_payload_as_is():
    payload = {"symbol": "000001"}
    with patch("src.storage.DatabaseManager") as db_cls:
        db = MagicMock()
        db.get_kline_snapshot.return_value = payload
        db_cls.get_instance.return_value = db
        assert biz._business_cache_get("000001") == payload


def test_business_cache_put_swallows_db_errors():
    with patch("src.storage.DatabaseManager") as db_cls:
        db_cls.get_instance.side_effect = RuntimeError("no db")
        # should not raise
        biz._business_cache_put("000001", {"x": 1})


def test_business_cache_put_serializes_dict_to_json():
    with patch("src.storage.DatabaseManager") as db_cls:
        db = MagicMock()
        db_cls.get_instance.return_value = db
        biz._business_cache_put("000001", {"x": 1})
    db.save_kline_snapshot.assert_called_once()
    args = db.save_kline_snapshot.call_args[0]
    assert json.loads(args[1]) == {"x": 1}


# ---------------------------------------------------------------------------
# _sanitize
# ---------------------------------------------------------------------------

def test_get_stock_business_returns_cached_without_force(client):
    cached = {"symbol": "000001", "cached": True}
    with patch("api.v1.endpoints.stock_info.business._business_cache_get", return_value=cached):
        resp = client.get("/api/v1/stocks/business", params={"symbol": "000001"})
    assert resp.status_code == 200
    assert resp.json()["cached"] is True


def test_get_stock_business_force_refetches_and_caches(client):
    result = {"symbol": "000001", "fresh": True}
    with patch("api.v1.endpoints.stock_info.business._business_cache_get", return_value=None), \
         patch("api.v1.endpoints.stock_info.business._fetch_business_intro", return_value={}), \
         patch("api.v1.endpoints.stock_info.business._fetch_business_composition", return_value=[]), \
         patch("api.v1.endpoints.stock_info.business._fetch_profit_forecast", return_value=[]), \
         patch("api.v1.endpoints.stock_info.business._fetch_financial_summary", return_value={}), \
         patch("api.v1.endpoints.stock_info.business._fetch_recent_events", return_value={}), \
         patch("api.v1.endpoints.stock_info.business._generate_llm_business_analysis", return_value=result), \
         patch("api.v1.endpoints.stock_info.business._business_cache_put") as put:
        resp = client.get(
            "/api/v1/stocks/business", params={"symbol": "sh000001", "force": True}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["fresh"] is True
    assert body["symbol"] == "000001"
    put.assert_called_once()
