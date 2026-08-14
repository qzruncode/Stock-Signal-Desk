from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

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


def _quality_projection(*, linked: bool = True) -> dict:
    return {
        "engine": "langgraph_agent_loop",
        "tool_results": [
            {
                "action_id": "news",
                "tool_call_id": "call-news",
                "tool_name": "read_company_news_akshare",
                "effect": "read",
                "success": True,
                "errors": [],
            }
        ],
        "evidence": [
            {
                "evidence_id": "ev_news",
                "action_id": "news",
                "tool_call_id": "call-news",
                "tool_name": "read_company_news_akshare",
                "effect": "read",
                "success": True,
                "source_refs": ["https://example.test/news/1"] if linked else [],
                "data_time": "2026-08-06T10:00:00+08:00",
            }
        ],
        "claim_evidence": [
            {
                "claim_id": "claim_news",
                "text": "结论包含风险提示【证据 ev_news】",
                "kind": "fact",
                "evidence_ids": ["ev_news"],
                "entity_fields": ["query"],
                "time_references": [],
                "checks": {
                    "tool_success": True,
                    "source": linked,
                    "entity_scope": True,
                    "time": True,
                },
            }
        ],
        "completed_tool_call_ids": ["call-news"],
        "loop": {
            "model_turn_count": 2,
            "tool_call_count": 1,
            "tool_call_limit": 8,
            "evidence_repair_count": 0,
            "evidence_repair_limit": 1,
            "work_budget_exhausted": False,
        },
    }


def _terminal_run(
    database: DatabaseManager,
    *,
    run_id: str,
    conversation_id: str,
    linked: bool = True,
) -> None:
    database.create_chat_conversation(
        conversation_id,
        tenant_id="tenant-a",
        owner_id="owner-a",
    )
    claimed = database.claim_agent_run(
        run_id=run_id,
        conversation_id=conversation_id,
        request_payload={"engine": "langgraph_agent_loop", "messages": [{"role": "user", "content": "核验 600519"}]},
        worker_id="worker-a",
        tenant_id="tenant-a",
        owner_id="owner-a",
    )
    assert claimed["claimed"] is True
    projection = _quality_projection(linked=linked)
    database.upsert_agent_run_trace(
        run_id=run_id,
        conversation_id=conversation_id,
        orchestrator_mode="langgraph_agent_loop",
        status="running",
        quality_projection=projection,
    )
    assert database.commit_agent_run_terminal(
        run_id=run_id,
        conversation_id=conversation_id,
        status="completed",
        final_text="结论包含风险提示【证据 ev_news】",
        messages=[
            {"role": "user", "content": "核验 600519"},
            {"role": "assistant", "content": "结论包含风险提示【证据 ev_news】"},
        ],
        agent_context={},
        worker_id="worker-a",
        trace={
            "engine": "langgraph_agent_loop",
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
            "required_tools": ["read_company_news_akshare"],
            "minimum_evidence_items": 1,
            "require_evidence_citations": True,
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
        tool="read_company_news_akshare",
    )
    assert explorer["total"] == 1
    assert explorer["items"][0]["tools"] == ["read_company_news_akshare"]
    snapshot = database.get_agent_run_quality_snapshot(
        "run-source",
        tenant_id="tenant-a",
        owner_id="owner-a",
    )
    assert snapshot is not None
    assert snapshot["quality_projection"]["tool_results"][0]["action_id"] == "news"


def test_quality_evaluator_rejects_unlinked_evidence(database) -> None:
    _terminal_run(
        database,
        run_id="run-unsupported",
        conversation_id="conversation-unsupported",
        linked=False,
    )
    snapshot = database.get_agent_run_quality_snapshot(
        "run-unsupported",
        tenant_id="tenant-a",
        owner_id="owner-a",
    )
    assert snapshot is not None
    result = score_agent_run_snapshot(snapshot)
    assert result["passed"] is False
    assert result["dimensions"]["evidence_links"]["score"] < 1.0
    assert any(item["code"] == "evidence_link_contract_failed" for item in result["violations"])


def test_quality_snapshot_exposes_terminal_error_detail(database) -> None:
    run_id = "run-runtime-error"
    conversation_id = "conversation-runtime-error"
    database.create_chat_conversation(
        conversation_id,
        tenant_id="tenant-a",
        owner_id="owner-a",
    )
    claimed = database.claim_agent_run(
        run_id=run_id,
        conversation_id=conversation_id,
        request_payload={"engine": "langgraph_agent_loop", "messages": []},
        worker_id="worker-a",
        tenant_id="tenant-a",
        owner_id="owner-a",
    )
    assert claimed["claimed"] is True

    assert database.commit_agent_run_terminal(
        run_id=run_id,
        conversation_id=conversation_id,
        status="failed",
        final_text="",
        messages=[{"role": "user", "content": "核验 600519"}],
        agent_context={},
        error_code="agent_runtime_failed",
        error_detail="ConnectionError: upstream closed the connection",
        worker_id="worker-a",
        trace={"engine": "langgraph_agent_loop", "status": "failed"},
    )

    snapshot = database.get_agent_run_quality_snapshot(
        run_id,
        tenant_id="tenant-a",
        owner_id="owner-a",
    )
    assert snapshot is not None
    assert snapshot["run"]["error_code"] == "agent_runtime_failed"
    assert snapshot["run"]["error_detail"] == "ConnectionError: upstream closed the connection"


def test_completed_no_tool_general_answer_can_pass_generic_quality_contract() -> None:
    result = score_agent_run_snapshot(
        {
            "run": {"status": "completed", "final_text": "稳定的常识解释"},
            "quality_projection": {
                "engine": "langgraph_agent_loop",
                "tool_results": [],
                "evidence": [],
                "loop": {"tool_call_count": 0, "work_budget_exhausted": False},
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
            "quality_projection": {"tool_results": [], "evidence": []},
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
        params={"status": "completed", "tool": "read_company_news_akshare", "page": 1},
    )
    assert response.status_code == 200
    fake_db.list_agent_runs_for_owner.assert_called_once_with(
        tenant_id="local",
        owner_id="admin",
        status="completed",
        tool="read_company_news_akshare",
        page=1,
        limit=30,
    )


def test_run_explorer_detail_payloads_are_opt_in(api_client) -> None:
    client, fake_db = api_client
    def snapshot() -> dict:
        return {
            "run": {"status": "completed", "final_text": ""},
            "trace": {},
            "quality_projection": {
                "tool_results": [{
                    "action_id": "action-1",
                    "tool_name": "read_source",
                    "success": True,
                    "arguments": {"symbol": "000682", "secret": "should-not-be-in-summary"},
                    "display_arguments": {"symbol": "000682"},
                    "result": {"data": [{"close": 1}]},
                    "display_result": {"result_summary": "1 条"},
                }],
                "evidence": [{
                    "evidence_id": "ev-1",
                    "success": True,
                    "result": {"data": [{"close": 1}]},
                }],
            },
            "steps": [],
            "artifacts": [],
            "feedback": None,
        }

    fake_db.get_agent_run_quality_snapshot.side_effect = [snapshot(), snapshot()]

    summary_response = client.get("/api/v1/agent/runs/run-1")
    assert summary_response.status_code == 200
    summary_projection = summary_response.json()["snapshot"]["quality_projection"]
    assert summary_projection["tool_results"][0]["arguments"] == {"symbol": "000682"}
    assert "result" not in summary_projection["tool_results"][0]
    assert "result" not in summary_projection["evidence"][0]

    response = client.get(
        "/api/v1/agent/runs/run-1",
        params={"include_payloads": "true"},
    )

    assert response.status_code == 200
    full_projection = response.json()["snapshot"]["quality_projection"]
    assert full_projection["tool_results"][0]["arguments"]["secret"] == "should-not-be-in-summary"
    assert full_projection["tool_results"][0]["result"]["data"][0]["close"] == 1
    assert fake_db.get_agent_run_quality_snapshot.call_args_list == [
        call("run-1", tenant_id="local", owner_id="admin", include_evidence_payloads=False),
        call("run-1", tenant_id="local", owner_id="admin", include_evidence_payloads=True),
    ]
