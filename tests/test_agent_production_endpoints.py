from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from api.app import create_app
from api.deps import get_database_manager
from api.v1.endpoints.agent import tool_registry_meta
import src.auth as auth


def _client() -> TestClient:
    auth._auth_enabled = None
    return TestClient(create_app())


def test_agent_readiness_reports_langgraph_checkpointer_and_atomic_catalog() -> None:
    with (
        patch("api.middlewares.auth.is_auth_enabled", return_value=False),
        patch("src.auth.is_auth_enabled", return_value=False),
        patch(
            "src.llm.anthropic_gateway.resolve_anthropic_gateway_config",
            return_value={"model": "verified-model"},
        ),
        patch("api.v1.endpoints.agent.health.agent_production_issues", return_value=[]),
    ):
        with _client() as client:
            response = client.get("/api/v1/agent/readiness")

    auth._auth_enabled = None
    assert response.status_code == 200
    payload = response.json()
    assert payload["checks"]["runtime"]["engine"] == "langgraph_agent_loop"
    assert payload["checks"]["runtime"]["graph_initialized"] is True
    assert payload["checks"]["tools"]["registered"] > 0
    assert payload["checks"]["tools"]["operation_directory"] == payload["checks"]["tools"]["registered"]


def test_agent_readiness_fails_closed_when_model_is_unavailable() -> None:
    with (
        patch("api.middlewares.auth.is_auth_enabled", return_value=False),
        patch("src.auth.is_auth_enabled", return_value=False),
        patch(
            "src.llm.anthropic_gateway.resolve_anthropic_gateway_config",
            side_effect=RuntimeError("model unavailable"),
        ),
        patch("api.v1.endpoints.agent.health.agent_production_issues", return_value=[]),
    ):
        with _client() as client:
            response = client.get("/api/v1/agent/readiness")

    auth._auth_enabled = None
    assert response.status_code == 503
    assert response.json()["checks"]["model"]["ok"] is False


def test_deep_readiness_probe_is_explicit() -> None:
    with (
        patch("api.middlewares.auth.is_auth_enabled", return_value=False),
        patch("src.auth.is_auth_enabled", return_value=False),
        patch(
            "src.llm.anthropic_gateway.resolve_anthropic_gateway_config",
            return_value={"model": "verified-model"},
        ),
        patch("api.v1.endpoints.agent.health.agent_production_issues", return_value=[]),
        patch(
            "api.v1.endpoints.agent.health._live_dependency_probe",
            return_value={"ok": True, "cached": False, "checks": {}, "probed_at": "2026-08-06T00:00:00+08:00"},
        ) as live_probe,
    ):
        with _client() as client:
            shallow = client.get("/api/v1/agent/readiness")
            deep = client.get("/api/v1/agent/readiness?deep=true")

    auth._auth_enabled = None
    assert "dependencies" not in shallow.json()["checks"]
    assert deep.json()["checks"]["dependencies"]["ok"] is True
    live_probe.assert_awaited_once_with()


def test_tool_registry_is_read_only_and_exposes_execution_policy_metadata() -> None:
    with (
        patch("api.middlewares.auth.is_auth_enabled", return_value=False),
        patch("src.auth.is_auth_enabled", return_value=False),
    ):
        with _client() as client:
            response = client.get("/api/v1/agent/tool-registry")
            forbidden = client.post(
                "/api/v1/agent/tool-registry/execute",
                json={"tool_name": "read_market_indices_sina", "arguments": {}},
            )

    auth._auth_enabled = None
    assert response.status_code == 200
    assert forbidden.status_code in {404, 405}
    payload = response.json()
    tools = {item["name"]: item for item in payload["tools"]}
    assert "read_market_indices_sina" in tools
    assert tools["read_company_news_akshare"]["category"] == "news_source"
    assert payload["categories"]["news_source"] == 1
    assert tools["read_market_indices_sina"]["effect"] == "read"
    assert tools["read_market_indices_sina"]["effect_mode"] == "fixed"
    assert tools["read_market_indices_sina"]["timeout_seconds"] > 0
    assert tools["read_market_indices_sina"]["typed"] is False
    assert tools["read_market_indices_sina"]["result_schema"]["additionalProperties"] is True
    assert tools["read_market_indices_sina"]["max_attempts"] >= 1
    assert "confirmed" not in tools["add_watchlist_items"]["args_schema"]["properties"]
    assert tools["add_watchlist_items"]["approval_policy"] == "required_for_side_effect"
    assert tools["add_watchlist_items"]["effect"] == "side_effect"
    assert tools["add_watchlist_items"]["effect_mode"] == "fixed"


def test_registered_catalog_contains_no_removed_composite_sop_tools() -> None:
    registered = set(tool_registry_meta._registry.get_tool_names())
    assert {
        "run_stock_analysis",
        "run_batch_analysis",
        "filter_watchlist_by_theme",
        "evaluate_multi_stock_buy_criteria",
        "get_domain_stock_candidates",
    }.isdisjoint(registered)


def test_approval_endpoint_accepts_once_then_returns_conflict() -> None:
    conversation_id = "conversation-approval"
    run_id = "run-approval"
    interrupt_id = "interrupt-approval"
    fingerprint = "f" * 64
    pending = {
        "interrupt_id": interrupt_id,
        "run_id": run_id,
        "fingerprint": fingerprint,
    }

    class FakeDatabase:
        def __init__(self) -> None:
            self.status = "interrupted"
            self.resume_count = 0

        def get_agent_run(self, *, run_id: str):
            assert run_id == "run-approval"
            return {
                "run_id": run_id,
                "conversation_id": conversation_id,
                "status": self.status,
                "request": {
                    "messages": [{"role": "user", "content": "发送通知"}],
                    "body": {},
                },
                "event_cursor": 3,
                "attempt": 1,
            }

        def resume_interrupted_agent_run(self, run_id: str, *, worker_id: str):
            assert run_id == "run-approval"
            assert worker_id == "worker-test"
            if self.status != "interrupted":
                return None
            self.status = "running"
            self.resume_count += 1
            return self.get_agent_run(run_id=run_id)

        def release_agent_run_lease(self, *_args, **_kwargs):
            return True

    class FakeRun:
        def __init__(self) -> None:
            self.started = False

        async def start(self, _factory) -> None:
            self.started = True

    class FakeRegistry:
        worker_id = "worker-test"

        def __init__(self) -> None:
            self.run = FakeRun()

        def configure(self, _database) -> None:
            return None

        def get(self, _conversation_id: str):
            return None

        async def adopt_recovered(self, **_kwargs):
            return self.run

    database = FakeDatabase()
    registry = FakeRegistry()
    service = MagicMock()
    service.get_conversation.return_value = {"id": conversation_id}
    app = create_app()
    app.dependency_overrides[get_database_manager] = lambda: database

    with (
        patch("api.middlewares.auth.is_auth_enabled", return_value=False),
        patch("src.auth.is_auth_enabled", return_value=False),
        patch(
            "api.v1.endpoints.agent.approvals.ChatSessionService",
            return_value=service,
        ),
        patch(
            "api.v1.endpoints.agent.approvals.agent_graph_runtime.pending_interrupt",
            new=AsyncMock(side_effect=[pending, pending, None]),
        ),
        patch(
            "api.v1.endpoints.agent.approvals.resolve_anthropic_gateway_config",
            return_value={"model": "test-model"},
        ),
        patch(
            "api.v1.endpoints.agent.approvals.active_run_registry",
            new=registry,
        ),
    ):
        with TestClient(app) as client:
            path = (
                f"/api/v1/agent/conversations/{conversation_id}/interrupts/"
                f"{interrupt_id}/decision"
            )
            body = {
                "run_id": run_id,
                "fingerprint": fingerprint,
                "decision": "approve",
            }
            mismatch = client.post(
                path,
                json={**body, "fingerprint": "b" * 64},
            )
            accepted = client.post(path, json=body)
            duplicate = client.post(path, json=body)

    auth._auth_enabled = None
    assert mismatch.status_code == 409
    assert accepted.status_code == 202
    assert accepted.json()["accepted"] is True
    assert duplicate.status_code == 409
    assert database.resume_count == 1
    assert registry.run.started is True
