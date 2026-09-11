# -*- coding: utf-8 -*-
"""Stocks list endpoint tests — pagination, search and market filter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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
    auth._auth_enabled = None
    with (
        patch("api.middlewares.auth.is_auth_enabled", return_value=False),
        patch("src.auth.is_auth_enabled", return_value=False),
    ):
        yield
    auth._auth_enabled = None


def test_list_stocks_returns_paginated_items(client):
    service = MagicMock()
    service.securities.return_value = {
        "items": [{"code": "000001", "name": "X"}],
        "total": 1,
        "freshness": {"status": "fresh"},
    }
    with patch(
        "api.v1.endpoints.stocks.list.get_market_data_client",
        return_value=service,
    ):
        response = client.get(
            "/api/v1/stocks", params={"page": 1, "page_size": 10}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["code"] == "000001"
    assert body["total"] == 1
    assert body["total_pages"] == 1
    service.securities.assert_called_once_with(
        page=1, page_size=10, search="", market="all"
    )


def test_list_stocks_rejects_invalid_pagination(client):
    response = client.get("/api/v1/stocks", params={"page": 0})

    assert response.status_code == 422
