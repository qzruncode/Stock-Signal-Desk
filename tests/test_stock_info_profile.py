# -*- coding: utf-8 -*-
"""Stock_info profile endpoint tests — value coercion, normalization, caching, fetch.

Covers api.v1.endpoints.stock_info.profile:
- _safe_float / _safe_int / _normalize_symbol (normal / boundary / failure)
- get_stock_info: cache hit, cache miss with force, multi-source merge
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.v1.endpoints.stock_info import profile as profile_mod
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
# _safe_float
# ---------------------------------------------------------------------------

def test_safe_float_returns_none_for_empty_or_markers():
    for v in [None, "", "nan", "None", "--", "-"]:
        assert profile_mod._safe_float(v) is None


def test_safe_float_parses_plain_number():
    assert profile_mod._safe_float("3.14") == 3.14


def test_safe_float_parses_yi_suffix():
    assert profile_mod._safe_float("1.5亿") == 1.5e8


def test_safe_float_parses_wan_suffix():
    assert profile_mod._safe_float("200万") == 2e6


def test_safe_float_returns_none_for_nan_float():
    assert profile_mod._safe_float(float("nan")) is None
    assert profile_mod._safe_float(float("inf")) is None


# ---------------------------------------------------------------------------
# _safe_int
# ---------------------------------------------------------------------------

def test_safe_int_returns_none_for_empty():
    assert profile_mod._safe_int(None) is None
    assert profile_mod._safe_int("") is None


def test_safe_int_parses_plain_int():
    assert profile_mod._safe_int("42") == 42


def test_safe_int_parses_yi_suffix():
    assert profile_mod._safe_int("1亿") == int(1e8)


def test_safe_int_returns_none_for_invalid():
    assert profile_mod._safe_int("abc") is None


# ---------------------------------------------------------------------------
# _normalize_symbol
# ---------------------------------------------------------------------------

def test_normalize_symbol_strips_exchange_prefix():
    assert profile_mod._normalize_symbol("sh600519") == "600519"
    assert profile_mod._normalize_symbol("SZ000001") == "000001"


def test_normalize_symbol_keeps_bare_code():
    assert profile_mod._normalize_symbol("600519") == "600519"


def test_normalize_symbol_trims_whitespace():
    assert profile_mod._normalize_symbol("  600519  ") == "600519"


# ---------------------------------------------------------------------------
# get_stock_info route
# ---------------------------------------------------------------------------

def test_get_stock_info_returns_cached_without_force(client):
    cached = {"symbol": "000001", "cached": True}
    with patch("api.v1.endpoints.stock_info.profile._cache_get", return_value=cached):
        resp = client.get("/api/v1/stocks/info", params={"symbol": "000001"})
    assert resp.status_code == 200
    assert resp.json()["cached"] is True


def test_get_stock_info_force_skips_cache_and_refetches(client):
    with patch("api.v1.endpoints.stock_info.profile._cache_get", return_value={"old": True}), \
         patch("api.v1.endpoints.stock_info.profile._fetch_all", return_value={"symbol": "000001", "fresh": True}), \
         patch("api.v1.endpoints.stock_info.profile._cache_put") as put:
        resp = client.get(
            "/api/v1/stocks/info", params={"symbol": "sh000001", "force": True}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["fresh"] is True
    assert body["symbol"] == "000001"  # normalized
    put.assert_called_once()


def test_get_stock_info_merges_all_sources(client):
    fetched = {
        "_sources": ["cninfo", "eastmoney", "ths"],
        "cninfo": {"x": 1},
        "eastmoney": {"y": 2},
        "ths_business": {"z": 3},
        "symbol": "000001",
    }
    with patch("api.v1.endpoints.stock_info.profile._cache_get", return_value=None), \
         patch("api.v1.endpoints.stock_info.profile._fetch_all", return_value=fetched), \
         patch("api.v1.endpoints.stock_info.profile._cache_put"):
        resp = client.get("/api/v1/stocks/info", params={"symbol": "000001"})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["_sources"]) == {"cninfo", "eastmoney", "ths"}
    assert body["symbol"] == "000001"


def test_get_stock_info_missing_symbol_returns_422(client):
    resp = client.get("/api/v1/stocks/info")
    assert resp.status_code == 422
