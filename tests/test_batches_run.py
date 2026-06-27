# -*- coding: utf-8 -*-
"""Batches run endpoint tests — trigger, current status, pause/resume/stop.

Covers api.v1.endpoints.batches.run route-level behavior:
- trigger_batch_run: empty stock list, buy_criteria mode, template mode, conflict
- get_current_batch_status: idle / running / finished states
- pause/resume/stop: 404 when no running control
- get_batch_run_detail: 404 when missing
- resume_batch_run: completed run rejected, missing run 404
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.v1.endpoints.batches import helpers as h
from api.v1.endpoints.batches import run as run_mod
from src.batch_runner import BatchRunControl
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


@pytest.fixture(autouse=True)
def reset_running_batch():
    """Isolate batch run state between tests."""
    h._running_batch = None
    run_mod._running_batch = None
    yield
    h._running_batch = None
    run_mod._running_batch = None


# ---------------------------------------------------------------------------
# trigger_batch_run
# ---------------------------------------------------------------------------

def test_trigger_run_rejects_empty_stock_list(client):
    with patch("api.v1.endpoints.batches.run.get_config") as gc:
        gc.return_value = type("C", (), {"stock_list": []})()
        resp = client.post("/api/v1/batch/run", json={"stock_codes": [], "template_id": ""})

    assert resp.status_code == 400


def test_trigger_run_buy_criteria_starts_without_template(client):
    captured = {}

    def _fake_start(run_factory, control=None):
        captured["started"] = True
        # mimic the state assignment the real thread would do
        state = run_factory(lambda s: None)
        h._running_batch = {"running": True, "state": state.to_dict() if state else None}

    with patch("api.v1.endpoints.batches.run._start_batch_thread", side_effect=_fake_start), \
         patch("api.v1.endpoints.batches.run.BatchRunner") as runner_cls, \
         patch("api.v1.endpoints.batches.run._is_batch_running", return_value=False):
        runner = MagicMock()
        state = MagicMock()
        state.to_dict.return_value = {"run_id": "r1", "completed": 0}
        runner.run.return_value = state
        runner_cls.return_value = runner

        resp = client.post(
            "/api/v1/batch/run",
            json={"stock_codes": ["000001"], "analysis_mode": "buy_criteria"},
        )

    assert resp.status_code == 202
    assert resp.json()["template_name"] == "买入判断筛选"
    assert captured["started"] is True


def test_trigger_run_template_mode_requires_existing_template(client):
    with patch("api.v1.endpoints.batches.run._is_batch_running", return_value=False), \
         patch("api.v1.endpoints.batches.run.get_prompt_template_store") as store_fn:
        store = MagicMock()
        store.get.return_value = None
        store_fn.return_value = store

        resp = client.post(
            "/api/v1/batch/run",
            json={"stock_codes": ["000001"], "template_id": "missing"},
        )

    assert resp.status_code == 404


def test_trigger_run_rejects_when_batch_already_running(client):
    with patch("api.v1.endpoints.batches.run._is_batch_running", return_value=True):
        resp = client.post(
            "/api/v1/batch/run",
            json={"stock_codes": ["000001"], "analysis_mode": "buy_criteria"},
        )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# get_current_batch_status
# ---------------------------------------------------------------------------

def test_current_status_idle_when_no_state(client):
    resp = client.get("/api/v1/batch/runs/current")
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is False
    assert body["state"] is None


def test_current_status_running_returns_state(client):
    run_mod._running_batch = {
        "running": True,
        "state": {"run_id": "r1", "completed": 5},
    }
    resp = client.get("/api/v1/batch/runs/current")
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is True
    assert body["state"]["run_id"] == "r1"


def test_current_status_finished_returns_terminal_state(client):
    run_mod._running_batch = {
        "running": False,
        "state": {"run_id": "r1", "completed": 10, "status": "completed"},
    }
    resp = client.get("/api/v1/batch/runs/current")
    body = resp.json()
    assert body["running"] is False
    assert body["state"]["status"] == "completed"


# ---------------------------------------------------------------------------
# pause / resume / stop
# ---------------------------------------------------------------------------

def test_pause_returns_404_when_no_running_batch(client):
    resp = client.post("/api/v1/batch/runs/current/pause")
    assert resp.status_code == 404


def test_resume_returns_404_when_no_running_batch(client):
    # /runs/current/resume matches /runs/{run_id}/resume with run_id="current",
    # so it needs the BatchRunResumeRequest body; control is None -> 404.
    resp = client.post(
        "/api/v1/batch/runs/current/resume", json={"stock_codes": []}
    )
    assert resp.status_code == 404


def test_stop_returns_404_when_no_running_batch(client):
    resp = client.post("/api/v1/batch/runs/current/stop")
    assert resp.status_code == 404


def test_pause_invokes_control_and_persists(client):
    control = MagicMock(spec=BatchRunControl)
    h._running_batch = {"running": True, "control": control, "state": {"run_id": "r1"}}
    with patch("api.v1.endpoints.batches.run._persist_current_status") as persist:
        resp = client.post("/api/v1/batch/runs/current/pause")
    assert resp.status_code == 200
    control.pause.assert_called_once()
    persist.assert_called_once_with("paused")


def test_stop_invokes_control_and_persists(client):
    control = MagicMock(spec=BatchRunControl)
    h._running_batch = {"running": True, "control": control, "state": {"run_id": "r1"}}
    with patch("api.v1.endpoints.batches.run._persist_current_status") as persist:
        resp = client.post("/api/v1/batch/runs/current/stop")
    assert resp.status_code == 200
    control.stop.assert_called_once()
    persist.assert_called_once_with("stopped")


# ---------------------------------------------------------------------------
# get_batch_run_detail
# ---------------------------------------------------------------------------

def test_get_run_detail_404_when_missing(client):
    with patch("api.v1.endpoints.batches.run.DatabaseManager") as db_cls:
        db = MagicMock()
        db.get_batch_run.return_value = None
        db_cls.get_instance.return_value = db
        resp = client.get("/api/v1/batch/runs/missing")
    assert resp.status_code == 404


def test_get_run_detail_returns_run_item(client):
    run = {
        "id": 1, "run_id": "r1", "triggered_by": "manual", "template_id": "t1",
        "template_name": "T", "stock_count": 2, "success_count": 1, "fail_count": 1,
        "started_at": "2026-06-01T00:00:00", "completed_at": "2026-06-01T01:00:00",
        "report_path": None, "results_json": "{}", "stock_codes_json": "[]",
        "status": "completed", "analysis_mode": "template",
    }
    with patch("api.v1.endpoints.batches.run.DatabaseManager") as db_cls:
        db = MagicMock()
        db.get_batch_run.return_value = run
        db_cls.get_instance.return_value = db
        resp = client.get("/api/v1/batch/runs/r1")
    assert resp.status_code == 200
    assert resp.json()["run_id"] == "r1"


# ---------------------------------------------------------------------------
# resume_batch_run
# ---------------------------------------------------------------------------

def test_resume_completed_run_rejected(client):
    with patch("api.v1.endpoints.batches.run._is_batch_running", return_value=False), \
         patch("api.v1.endpoints.batches.run.DatabaseManager") as db_cls:
        db = MagicMock()
        db.get_batch_run.return_value = {"completed_at": "2026-06-01T00:00:00"}
        db_cls.get_instance.return_value = db
        resp = client.post(
            "/api/v1/batch/runs/r1/resume", json={"stock_codes": ["000001"]}
        )
    assert resp.status_code == 400


def test_resume_missing_run_404(client):
    with patch("api.v1.endpoints.batches.run._is_batch_running", return_value=False), \
         patch("api.v1.endpoints.batches.run.DatabaseManager") as db_cls:
        db = MagicMock()
        db.get_batch_run.return_value = None
        db_cls.get_instance.return_value = db
        resp = client.post(
            "/api/v1/batch/runs/r1/resume", json={"stock_codes": ["000001"]}
        )
    assert resp.status_code == 404


def test_resume_rejects_when_batch_running(client):
    with patch("api.v1.endpoints.batches.run._is_batch_running", return_value=True):
        resp = client.post(
            "/api/v1/batch/runs/r1/resume", json={"stock_codes": ["000001"]}
        )
    assert resp.status_code == 409
