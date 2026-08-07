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
    manager = DatabaseManager(db_url=f"sqlite:///{tmp_path / 'agent-quality.db'}")
    try:
        yield manager
    finally:
        DatabaseManager.reset_instance()


def _quality_projection(*, supported: bool = True) -> dict:
    return {
        "engine": "langgraph",
        "intent": {"objective": "核验 600519 的公开信息"},
        "actions": [
            {
                "action_id": "news",
                "objective": "查询公开信息",
                "tool_name": "search_news",
                "arguments": {"symbol": "600519", "days": 30},
                "depends_on": [],
                "expected_evidence": ["公开来源和发布时间"],
            }
        ],
        "tool_results": [
            {
                "action_id": "news",
                "tool_name": "search_news",
                "success": True,
                "errors": [],
            }
        ],
        "evidence": [
            {
                "evidence_id": "ev_news",
                "action_id": "news",
                "tool_name": "search_news",
                "success": True,
                "source_refs": ["https://example.test/news/1"],
                "data_time": "2026-08-06T10:00:00+08:00",
            }
        ],
        "verification": {
            "accepted": supported,
            "claims": [
                {
                    "claim": "公开信息已核验",
                    "material": True,
                    "evidence_ids": ["ev_news"],
                    "supported": supported,
                    "issue": None if supported else "证据不支持该表述",
                }
            ],
        },
        "completed_action_ids": ["news"],
        "budgets": {
            "plan_round": 1,
            "max_plan_rounds": 6,
            "search_expansions": 0,
            "max_search_expansions": 2,
            "verification_round": 0,
            "max_verification_rounds": 2,
        },
    }


def _terminal_run(
    database: DatabaseManager,
    *,
    run_id: str,
    conversation_id: str,
    supported: bool = True,
) -> None:
    database.create_chat_conversation(
        conversation_id,
        tenant_id="tenant-a",
        owner_id="owner-a",
    )
    claimed = database.claim_agent_run(
        run_id=run_id,
        conversation_id=conversation_id,
        request_payload={"engine": "langgraph", "messages": [{"role": "user", "content": "核验 600519"}]},
        worker_id="worker-a",
        tenant_id="tenant-a",
        owner_id="owner-a",
    )
    assert claimed["claimed"] is True
    projection = _quality_projection(supported=supported)
    database.upsert_agent_run_trace(
        run_id=run_id,
        conversation_id=conversation_id,
        orchestrator_mode="langgraph",
        status="running",
        quality_projection=projection,
    )
    assert database.commit_agent_run_terminal(
        run_id=run_id,
        conversation_id=conversation_id,
        status="completed",
        final_text="结论包含风险提示 [ev_news]",
        messages=[
            {"role": "user", "content": "核验 600519"},
            {"role": "assistant", "content": "结论包含风险提示 [ev_news]"},
        ],
        agent_context={},
        worker_id="worker-a",
        trace={
            "engine": "langgraph",
            "status": "completed",
            "quality_projection": projection,
        },
    )


def test_quality_evaluation_case_passes_and_feedback_is_owned(database) -> None:
    _terminal_run(database, run_id="run-source", conversation_id="conversation-source")
    case = database.create_agent_evaluation_case(
        tenant_id="tenant-a",
        owner_id="owner-a",
        source_run_id="run-source",
        suite="release",
        name="dynamic-evidence-query",
        description=None,
        expectations={
            "required_tools": ["search_news"],
            "minimum_evidence_items": 1,
            "require_claim_evidence_verified": True,
            "required_answer_terms": ["风险提示"],
            "minimum_score": 0.9,
        },
        tags=["dynamic-plan", "evidence"],
    )
    result = database.evaluate_agent_run_against_case(
        tenant_id="tenant-a",
        owner_id="owner-a",
        case_id=case["id"],
        candidate_run_id="run-source",
    )
    assert result["passed"] is True
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
    assert database.get_agent_run_quality_snapshot(
        "run-source",
        tenant_id="tenant-a",
        owner_id="someone-else",
    ) is None

    summary = database.agent_quality_summary(tenant_id="tenant-a", owner_id="owner-a")
    assert summary["terminal_runs"] == 1
    assert summary["feedback"]["negative"] == 1
    assert summary["release_gate"]["passed"] == 1
    explorer = database.list_agent_runs_for_owner(
        tenant_id="tenant-a",
        owner_id="owner-a",
        tool="search_news",
    )
    assert explorer["total"] == 1
    assert explorer["items"][0]["tools"] == ["search_news"]
    snapshot = database.get_agent_run_quality_snapshot(
        "run-source",
        tenant_id="tenant-a",
        owner_id="owner-a",
    )
    assert snapshot is not None
    assert snapshot["quality_projection"]["actions"][0]["action_id"] == "news"


def test_quality_evaluator_rejects_unsupported_claim(database) -> None:
    _terminal_run(
        database,
        run_id="run-unsupported",
        conversation_id="conversation-unsupported",
        supported=False,
    )
    snapshot = database.get_agent_run_quality_snapshot(
        "run-unsupported",
        tenant_id="tenant-a",
        owner_id="owner-a",
    )
    assert snapshot is not None
    result = score_agent_run_snapshot(snapshot)
    assert result["passed"] is False
    assert result["dimensions"]["claim_evidence"]["score"] < 1.0
    assert any(item["code"] == "claim_evidence_contract_failed" for item in result["violations"])


def test_completed_no_tool_general_answer_can_pass_generic_quality_contract() -> None:
    result = score_agent_run_snapshot(
        {
            "run": {"status": "completed", "final_text": "稳定的常识解释"},
            "quality_projection": {
                "engine": "langgraph",
                "actions": [],
                "tool_results": [],
                "evidence": [],
                "verification": {"accepted": True, "claims": []},
                "budgets": {"plan_round": 0, "max_plan_rounds": 6},
            },
            "steps": [],
        }
    )
    assert result["passed"] is True
    assert result["total_score"] == 1.0


def test_legacy_capability_expectations_are_not_executable() -> None:
    result = score_agent_run_snapshot(
        {
            "run": {"status": "completed", "final_text": "answer"},
            "quality_projection": {"verification": {"accepted": True, "claims": []}},
            "steps": [],
        },
        {"required_capabilities": ["stock_profile"]},
    )
    assert result["passed"] is False
    assert any(item["code"] == "legacy_expectation_not_executable" for item in result["violations"])


@pytest.fixture
def api_client():
    auth._auth_enabled = None
    app = create_app()
    fake_db = MagicMock()
    app.dependency_overrides[get_database_manager] = lambda: fake_db
    with (
        patch("api.middlewares.auth.is_auth_enabled", return_value=False),
        patch("src.auth.is_auth_enabled", return_value=False),
        TestClient(app) as client,
    ):
        yield client, fake_db
    auth._auth_enabled = None


def test_feedback_endpoint_uses_current_owner_scope(api_client) -> None:
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


def test_evaluation_contract_rejects_unknown_fields(api_client) -> None:
    client, fake_db = api_client
    response = client.post(
        "/api/v1/agent/evaluation-cases",
        json={
            "source_run_id": "run-1",
            "name": "case",
            "expectations": {"required_capabilities": ["stock_profile"]},
        },
    )
    assert response.status_code == 422
    fake_db.create_agent_evaluation_case.assert_not_called()


def test_run_explorer_list_uses_tool_filter_and_owner_scope(api_client) -> None:
    client, fake_db = api_client
    fake_db.list_agent_runs_for_owner.return_value = {"items": [], "total": 0, "page": 1, "limit": 30}
    response = client.get(
        "/api/v1/agent/runs",
        params={"status": "completed", "tool": "search_news", "page": 1},
    )
    assert response.status_code == 200
    fake_db.list_agent_runs_for_owner.assert_called_once_with(
        tenant_id="local",
        owner_id="admin",
        status="completed",
        tool="search_news",
        page=1,
        limit=30,
    )
