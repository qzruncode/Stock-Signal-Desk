# -*- coding: utf-8 -*-
"""HTTP/runtime tests for Agent production diagnostics and tool probes."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from api.app import create_app
from api.v1.endpoints.agent import tool_registry_meta
from api.v1.schemas.tools_meta import ToolExecuteRequest
from src.agent.task_workflows import WORKFLOW_REGISTRY
import src.auth as auth


def test_agent_readiness_reports_model_database_runtime_and_tools():
    auth._auth_enabled = None
    app = create_app()
    with patch("api.middlewares.auth.is_auth_enabled", return_value=False), \
         patch("src.auth.is_auth_enabled", return_value=False), \
         patch(
             "src.llm.anthropic_gateway.resolve_anthropic_gateway_config",
             return_value={"model": "verified-model"},
         ), \
         patch("api.v1.endpoints.agent.health.agent_production_issues", return_value=[]):
        response = TestClient(app).get("/api/v1/agent/readiness")

    auth._auth_enabled = None
    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is True
    assert payload["checks"]["database"]["ok"] is True
    assert payload["checks"]["model"] == {"ok": True, "model": "verified-model"}
    assert payload["checks"]["runtime"]["ok"] is True
    assert payload["checks"]["tools"]["registered"] > 0


def test_agent_readiness_fails_closed_when_model_is_unavailable():
    auth._auth_enabled = None
    app = create_app()
    with patch("api.middlewares.auth.is_auth_enabled", return_value=False), \
         patch("src.auth.is_auth_enabled", return_value=False), \
         patch(
             "src.llm.anthropic_gateway.resolve_anthropic_gateway_config",
             side_effect=RuntimeError("model unavailable"),
         ), \
         patch("api.v1.endpoints.agent.health.agent_production_issues", return_value=[]):
        response = TestClient(app).get("/api/v1/agent/readiness")

    auth._auth_enabled = None
    assert response.status_code == 503
    assert response.json()["checks"]["model"]["ok"] is False


def test_tool_probe_preserves_normalized_arguments_and_payload_failure():
    registry = MagicMock()
    registry.normalize_arguments.return_value = {"days": 7}
    registry.execute.return_value = {
        "success": False,
        "errors": ["upstream unavailable"],
        "items": [],
    }
    request = ToolExecuteRequest(tool_name="search_financial_news", arguments={"days": "7"})

    async def run():
        with patch.object(tool_registry_meta, "_registry", registry), \
             patch.object(
                 tool_registry_meta,
                 "execute_tool_isolated",
                 side_effect=lambda name, arguments, **_kwargs: registry.execute(name, arguments),
             ), \
             patch.object(tool_registry_meta, "_compact_tool_result", side_effect=lambda _name, value: value), \
             patch.object(tool_registry_meta, "_maybe_attach_search_fallback", side_effect=lambda _name, _args, value: value):
            return await tool_registry_meta.execute_tool(request)

    response = asyncio.run(run())
    assert response.arguments == {"days": 7}
    assert response.success is False
    assert response.error == "upstream unavailable"


def test_tool_probe_does_not_install_an_application_deadline():
    registry = SimpleNamespace(
        normalize_arguments=lambda _name, args: args,
        execute=lambda _name, _args: {"success": True},
    )
    request = ToolExecuteRequest(tool_name="slow_tool", arguments={})

    async def run():
        with patch.object(tool_registry_meta, "_registry", registry), \
             patch.object(
                 tool_registry_meta,
                 "execute_tool_isolated",
                 side_effect=lambda name, arguments, **_kwargs: registry.execute(name, arguments),
             ), \
             patch.object(
                 tool_registry_meta.asyncio,
                 "wait_for",
                 side_effect=AssertionError("tool probe must not use wait_for"),
             ):
            return await tool_registry_meta.execute_tool(request)

    response = asyncio.run(run())
    assert response.success is True
    assert response.error is None


def test_workflow_contract_has_no_timeout_or_retry_fields():
    for workflow in WORKFLOW_REGISTRY.values():
        assert not hasattr(workflow, "timeout_seconds")
        assert not hasattr(workflow, "max_attempts")


def test_tool_registry_keeps_specialized_categories():
    tools = {tool.name: tool_registry_meta._build_tool_meta(tool) for tool in tool_registry_meta._registry._tools.values()}
    assert tools["get_domain_stock_candidates"].category == "research"
    assert "get_theme_stock_candidates" not in tools
    assert tools["get_regulatory_updates"].category == "regulatory"
    assert tools["get_announcements"].category == "events"
    assert tools["get_risk_events"].category == "risk"
