# -*- coding: utf-8 -*-
"""Contracts for the normalized stock-info tool and its HTTP adapter."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.v1.endpoints.stock_info import profile as profile_mod
import src.auth as auth


@pytest.fixture
def client():
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def disable_auth():
    auth._auth_enabled = None
    with patch("api.middlewares.auth.is_auth_enabled", return_value=False), \
         patch("src.auth.is_auth_enabled", return_value=False):
        yield
    auth._auth_enabled = None


def test_normalize_symbol_strips_all_exchange_prefixes():
    assert profile_mod._normalize_symbol("sh600519") == "600519"
    assert profile_mod._normalize_symbol("SZ000001") == "000001"
    assert profile_mod._normalize_symbol("bj920000") == "920000"
    assert profile_mod._normalize_symbol(" 600519 ") == "600519"


def test_stock_info_endpoint_delegates_to_tool_cache_by_default(client):
    payload = {"symbol": "000001", "success": True, "company_name": "平安银行股份有限公司"}
    with patch("api.v1.endpoints.stock_info.profile._tool_get_stock_info", return_value=payload) as tool:
        response = client.get("/api/v1/stocks/info", params={"symbol": "000001"})

    assert response.status_code == 200
    assert response.json()["company_name"] == "平安银行股份有限公司"
    tool.assert_called_once_with("000001", use_cache=True)


def test_stock_info_endpoint_force_bypasses_tool_cache(client):
    payload = {"symbol": "000001", "success": True}
    with patch("api.v1.endpoints.stock_info.profile._tool_get_stock_info", return_value=payload) as tool:
        response = client.get("/api/v1/stocks/info", params={"symbol": "sh000001", "force": True})

    assert response.status_code == 200
    tool.assert_called_once_with("sh000001", use_cache=False)


def test_stock_info_endpoint_reports_upstream_failure(client):
    payload = {"symbol": "000001", "success": False, "errors": ["profile down", "capital down"]}
    with patch("api.v1.endpoints.stock_info.profile._tool_get_stock_info", return_value=payload):
        response = client.get("/api/v1/stocks/info", params={"symbol": "000001"})

    assert response.status_code == 502
    assert "profile down" in response.json()["message"]


def test_get_stock_info_missing_symbol_returns_422(client):
    assert client.get("/api/v1/stocks/info").status_code == 422
