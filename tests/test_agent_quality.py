# -*- coding: utf-8 -*-
"""Durable Agent quality evaluation and feedback contracts."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.deps import get_database_manager
import src.auth as auth
from src.agent.evaluation import score_agent_run_snapshot
from src.storage import DatabaseManager


@pytest.fixture
def database(tmp_path: Path):
    DatabaseManager.reset_instance()
    manager = DatabaseManager(
        db_url=f"sqlite:///{tmp_path / 'agent-quality.db'}"
    )
    try:
        yield manager
    finally:
        DatabaseManager.reset_instance()


def _terminal_run(
    database: DatabaseManager,
    *,
    run_id: str,
    conversation_id: str,
    coverage_complete: bool = True,
) -> None:
    database.create_chat_conversation(
        conversation_id,
        tenant_id="tenant-a",
        owner_id="owner-a",
    )
    claimed = database.claim_agent_run(
        run_id=run_id,
        conversation_id=conversation_id,
        request_payload={
            "messages": [
                {"role": "user", "content": "分析 600519"}
            ]
        },
        worker_id="worker-a",
        tenant_id="tenant-a",
        owner_id="owner-a",
    )
    assert claimed["claimed"] is True
    database.upsert_agent_run_trace(
        run_id=run_id,
        conversation_id=conversation_id,
        orchestrator_mode="unified",
        status="compiled",
        quality_projection={
            "tasks": [
                {
                    "task_id": "task-1",
                    "capability": "stock_profile",
                    "depends_on": [],
                    "effect": "read",
                }
            ],
            "plan_revision": 0,
        },
    )
    assert database.commit_agent_run_terminal(
        run_id=run_id,
        conversation_id=conversation_id,
        status="completed",
        final_text="结论包含风险提示",
        messages=[
            {"role": "user", "content": "分析 600519"},
            {"role": "assistant", "content": "结论包含风险提示"},
        ],
        agent_context={},
        worker_id="worker-a",
        trace={
            "status": "completed",
            "quality_projection": {
                "outcomes": [
                    {
                        "task_id": "task-1",
                        "status": "completed",
                        "coverage": {
                            "requested": ["600519"],
                            "covered": (
                                ["600519"]
                                if coverage_complete
                                else []
                            ),
                            "missing": (
                                []
                                if coverage_complete
                                else ["600519"]
                            ),
                            "complete": coverage_complete,
                        },
                        "evidence_count": 2,
                        "warning_count": 0,
                        "error_count": 0,
                    }
                ],
                "artifact_count": 0,
            },
        },
    )


def test_quality_evaluation_case_passes_and_feedback_is_owned(database):
    _terminal_run(
        database,
        run_id="run-source",
        conversation_id="conversation-source",
    )
    case = database.create_agent_evaluation_case(
        tenant_id="tenant-a",
        owner_id="owner-a",
        source_run_id="run-source",
        suite="release",
        name="single-stock-profile",
        description=None,
        expectations={
            "required_capabilities": ["stock_profile"],
            "minimum_evidence_items": 2,
            "required_answer_terms": ["风险提示"],
            "minimum_score": 0.9,
        },
        tags=["stock", "profile"],
    )
    result = database.evaluate_agent_run_against_case(
        tenant_id="tenant-a",
        owner_id="owner-a",
        case_id=case["id"],
        candidate_run_id="run-source",
    )
    assert result["passed"] is True
    assert result["status"] == "passed"
    assert result["total_score"] == 1.0

    feedback = database.upsert_agent_run_feedback(
        tenant_id="tenant-a",
        owner_id="owner-a",
        run_id="run-source",
        rating=-1,
        category="evidence",
        comment="需要更明确的数据出处",
    )
    assert feedback["rating"] == -1
    assert (
        database.get_agent_run_quality_snapshot(
            "run-source",
            tenant_id="tenant-a",
            owner_id="someone-else",
        )
        is None
    )

    summary = database.agent_quality_summary(
        tenant_id="tenant-a",
        owner_id="owner-a",
    )
    assert summary["terminal_runs"] == 1
    assert summary["feedback"]["negative"] == 1
    assert summary["release_gate"]["passed"] == 1
    explorer = database.list_agent_runs_for_owner(
        tenant_id="tenant-a",
        owner_id="owner-a",
        capability="stock_profile",
    )
    assert explorer["total"] == 1
    assert explorer["items"][0]["run_id"] == "run-source"
    assert explorer["items"][0]["quality_score"] == 1.0
    snapshot = database.get_agent_run_quality_snapshot(
        "run-source",
        tenant_id="tenant-a",
        owner_id="owner-a",
    )
    assert snapshot is not None
    assert snapshot["quality_projection"]["tasks"] == [
        {
            "task_id": "task-1",
            "capability": "stock_profile",
            "depends_on": [],
            "effect": "read",
        }
    ]


def test_quality_evaluator_rejects_incomplete_coverage(database):
    _terminal_run(
        database,
        run_id="run-incomplete",
        conversation_id="conversation-incomplete",
        coverage_complete=False,
    )
    snapshot = database.get_agent_run_quality_snapshot(
        "run-incomplete",
        tenant_id="tenant-a",
        owner_id="owner-a",
    )
    assert snapshot is not None
    result = score_agent_run_snapshot(snapshot)
    assert result["passed"] is False
    assert any(
        item["code"] == "incomplete_coverage"
        for item in result["violations"]
    )


def test_completed_run_without_typed_outcome_fails_coverage():
    result = score_agent_run_snapshot(
        {
            "run": {
                "status": "completed",
                "final_text": "legacy answer",
            },
            "quality_projection": {
                "tasks": [],
                "outcomes": [],
            },
            "steps": [],
        }
    )
    assert result["passed"] is False
    assert result["dimensions"]["coverage"]["score"] == 0.0
    assert any(
        item["code"] == "missing_typed_outcomes"
        for item in result["violations"]
    )


@pytest.fixture
def api_client():
    auth._auth_enabled = None
    app = create_app()
    fake_db = MagicMock()
    app.dependency_overrides[get_database_manager] = lambda: fake_db
    with (
        patch(
            "api.middlewares.auth.is_auth_enabled",
            return_value=False,
        ),
        patch("src.auth.is_auth_enabled", return_value=False),
        TestClient(app) as client,
    ):
        yield client, fake_db
    auth._auth_enabled = None


def test_feedback_endpoint_uses_current_owner_scope(api_client):
    client, fake_db = api_client
    fake_db.upsert_agent_run_feedback.return_value = {
        "run_id": "run-1",
        "rating": 1,
        "category": "correctness",
    }
    response = client.put(
        "/api/v1/agent/runs/run-1/feedback",
        json={"rating": 1, "category": "correctness"},
    )
    assert response.status_code == 200
    fake_db.upsert_agent_run_feedback.assert_called_once_with(
        tenant_id="local",
        owner_id="admin",
        run_id="run-1",
        rating=1,
        category="correctness",
        comment=None,
    )


def test_evaluation_contract_rejects_unknown_fields(api_client):
    client, fake_db = api_client
    response = client.post(
        "/api/v1/agent/evaluation-cases",
        json={
            "source_run_id": "run-1",
            "name": "case",
            "expectations": {"unknown_check": True},
        },
    )
    assert response.status_code == 422
    fake_db.create_agent_evaluation_case.assert_not_called()


def test_run_explorer_list_uses_owner_scope(api_client):
    client, fake_db = api_client
    fake_db.list_agent_runs_for_owner.return_value = {
        "items": [],
        "total": 0,
        "page": 1,
        "limit": 30,
    }
    response = client.get(
        "/api/v1/agent/runs",
        params={"status": "completed", "page": 1},
    )
    assert response.status_code == 200
    fake_db.list_agent_runs_for_owner.assert_called_once_with(
        tenant_id="local",
        owner_id="admin",
        status="completed",
        capability=None,
        page=1,
        limit=30,
    )
