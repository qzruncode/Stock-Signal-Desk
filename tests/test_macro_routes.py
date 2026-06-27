# -*- coding: utf-8 -*-

from datetime import datetime
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
    with patch("api.middlewares.auth.is_auth_enabled", return_value=False), \
         patch("src.auth.is_auth_enabled", return_value=False):
        yield
    auth._auth_enabled = None


def test_macro_index_route_returns_cached_db_payload(client, monkeypatch):
    """Regression test: response building should not crash on staleness metadata."""
    from api.v1.endpoints import macro

    today = datetime.now().strftime("%Y-%m-%d")

    class _FakeDb:
        def get_macro_index_daily(self, index_code, limit=20):
            assert index_code == "000001"
            assert limit == 5
            return [
                {
                    "date": today,
                    "open": 3300.0,
                    "high": 3320.0,
                    "low": 3290.0,
                    "close": 3310.0,
                    "volume": 123456789.0,
                }
            ]

    monkeypatch.setattr("api.v1.endpoints.macro._index.get_db", lambda: _FakeDb())

    response = client.get("/api/v1/macro/index", params={"index_code": "000001", "days": 5})

    assert response.status_code == 200
    payload = response.json()
    assert payload["index_code"] == "000001"
    assert payload["data_time"] == today
    assert payload["is_stale"] is False
    assert payload["_cached"] is True
