# -*- coding: utf-8 -*-
"""Batches schedule endpoint tests — get and update cron schedule.

Covers api.v1.endpoints.batches.schedule:
- get_batch_schedule: missing config returns default empty state
- get_batch_schedule: existing config returned as-is
- update_batch_schedule: rejects missing template_id, invalid time format,
  and persists valid input
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


def test_get_schedule_returns_default_when_missing():
    with patch("api.v1.endpoints.batches.schedule.DatabaseManager") as db_cls:
        db = MagicMock()
        db.get_batch_schedule.return_value = None
        db_cls.get_instance.return_value = db
        resp = TestClient(create_app()).get("/api/v1/batch/schedule")

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == 0
    assert body["enabled"] is False
    assert body["times"] == []


def test_get_schedule_returns_persisted_config():
    persisted = {
        "id": 7,
        "enabled": True,
        "times": ["09:30", "15:00"],
        "template_id": "tpl-1",
        "created_at": "2026-06-01T00:00:00",
        "updated_at": "2026-06-02T00:00:00",
    }
    with patch("api.v1.endpoints.batches.schedule.DatabaseManager") as db_cls:
        db = MagicMock()
        db.get_batch_schedule.return_value = persisted
        db_cls.get_instance.return_value = db
        resp = TestClient(create_app()).get("/api/v1/batch/schedule")

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == 7
    assert body["enabled"] is True
    assert body["times"] == ["09:30", "15:00"]
    assert body["template_id"] == "tpl-1"


def test_update_schedule_rejects_missing_template_id(client):
    resp = client.put(
        "/api/v1/batch/schedule",
        json={"enabled": True, "times": ["09:30"], "template_id": ""},
    )
    assert resp.status_code == 400


def test_update_schedule_rejects_invalid_time_format(client):
    resp = client.put(
        "/api/v1/batch/schedule",
        json={"enabled": True, "times": ["25:00"], "template_id": "tpl-1"},
    )
    assert resp.status_code == 400


def test_update_schedule_persists_valid_input(client):
    saved = {
        "id": 1,
        "enabled": True,
        "times": ["09:30"],
        "template_id": "tpl-1",
        "created_at": None,
        "updated_at": None,
    }
    with patch("api.v1.endpoints.batches.schedule.DatabaseManager") as db_cls:
        db = MagicMock()
        db.save_batch_schedule.return_value = saved
        db_cls.get_instance.return_value = db
        resp = client.put(
            "/api/v1/batch/schedule",
            json={"enabled": True, "times": ["09:30"], "template_id": "tpl-1"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["times"] == ["09:30"]
    db.save_batch_schedule.assert_called_once_with(
        enabled=True, times=["09:30"], template_id="tpl-1"
    )
