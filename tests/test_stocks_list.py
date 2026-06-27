# -*- coding: utf-8 -*-
"""Stocks list endpoint tests — pagination, search, market filter, count, kline-status.

Covers api.v1.endpoints.stocks.list route-level behavior.
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

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
    with patch("api.middlewares.auth.is_auth_enabled", return_value=False), \
         patch("src.auth.is_auth_enabled", return_value=False):
        yield
    auth._auth_enabled = None


def _mock_session_query(items, total):
    """Build a MagicMock session whose query chain returns items and total."""
    session = MagicMock()
    query = MagicMock()
    filtered = MagicMock()
    query.filter.return_value = filtered
    filtered.count.return_value = total
    ordered = MagicMock()
    filtered.order_by.return_value = ordered
    paged = MagicMock()
    ordered.offset.return_value = paged
    paged.limit.return_value = paged
    paged.all.return_value = items
    session.query.return_value = query
    return session


def test_list_stocks_returns_paginated_items(client):
    item = MagicMock()
    item.to_dict.return_value = {"code": "000001", "name": "X"}
    db = MagicMock()
    db.get_session.return_value.__enter__.return_value = _mock_session_query([item], 1)
    with patch("api.v1.endpoints.stocks.list.DatabaseManager") as db_cls:
        db_cls.get_instance.return_value = db
        resp = client.get("/api/v1/stocks", params={"page": 1, "page_size": 10})
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["code"] == "000001"
    assert body["total"] == 1
    assert body["total_pages"] == 1


def test_list_stocks_rejects_invalid_pagination(client):
    resp = client.get("/api/v1/stocks", params={"page": 0})
    assert resp.status_code == 422


def test_list_stocks_count_endpoint(client):
    db = MagicMock()
    session = MagicMock()
    query = MagicMock()
    filtered = MagicMock()
    filtered.count.return_value = 42
    query.filter.return_value = filtered
    session.query.return_value = query
    db.get_session.return_value.__enter__.return_value = session
    with patch("api.v1.endpoints.stocks.list.DatabaseManager") as db_cls:
        db_cls.get_instance.return_value = db
        resp = client.get("/api/v1/stocks/count")
    assert resp.status_code == 200
    assert resp.json()["total"] == 42


def test_kline_status_returns_counts(client):
    db = MagicMock()
    session = MagicMock()
    query = MagicMock()
    filtered = MagicMock()
    filtered.scalar.return_value = 100
    query.filter.return_value = filtered
    # stocks_with_kline: session.query(...).scalar() with no filter
    query_no_filter = MagicMock()
    query_no_filter.scalar.return_value = 80
    session.query.side_effect = [query, query_no_filter]
    session.execute.return_value.scalar.return_value = "2026-06-01"
    db.get_session.return_value.__enter__.return_value = session
    with patch("api.v1.endpoints.stocks.list.DatabaseManager") as db_cls:
        db_cls.get_instance.return_value = db
        resp = client.get("/api/v1/stocks/kline-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_stocks"] == 100
    assert body["stocks_with_kline"] == 80
    assert body["missing"] == 20
    assert body["latest_trading_day"] == "2026-06-01"


def test_kline_batch_returns_empty_for_no_codes(client):
    resp = client.post("/api/v1/stocks/kline/batch", json={"codes": []})
    assert resp.status_code == 200
    assert resp.json()["results"] == {}


def test_kline_batch_returns_data_for_known_codes(client):
    db = MagicMock()
    session = MagicMock()
    session.execute.return_value.all.return_value = [
        ("000001", "2026-06-01", 10.0, 11.0, 9.5, 10.5),
    ]
    db.get_session.return_value.__enter__.return_value = session
    with patch("api.v1.endpoints.stocks.list.DatabaseManager") as db_cls:
        db_cls.get_instance.return_value = db
        resp = client.post(
            "/api/v1/stocks/kline/batch",
            json={"codes": ["000001"], "count": 10},
        )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert "000001" in results
    assert results["000001"][0][0] == "2026-06-01"
