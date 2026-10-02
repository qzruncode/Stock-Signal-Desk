"""Regression coverage for model arguments before LangChain tool validation."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.runtime import Runtime

from src.agent.langgraph_runtime.agent_tools import (
    NATIVE_TOOL_RESULT_MARKER,
    build_langchain_tools,
)
from src.agent.langgraph_runtime.middleware import ToolExecutionMiddleware
from src.tools.registry import ToolRegistry


@pytest.fixture(scope="module")
def tool_runtime() -> tuple[ToolRegistry, dict[str, Any]]:
    registry = ToolRegistry()
    return registry, {tool.name: tool for tool in build_langchain_tools(registry)}


@pytest.mark.parametrize(
    ("tool_name", "authored_arguments", "expected_arguments"),
    [
        (
            "read_core_financial_indicators_ths",
            {"stock_code": "300850"},
            {"symbol": "300850"},
        ),
        (
            "read_local_financial_snapshot",
            {"stock_code": "300850"},
            {"symbols": "300850"},
        ),
        (
            "read_realtime_quote",
            {"source": "auto", "symbol": "300850"},
            {"source_id": "auto", "symbol": "300850"},
        ),
        (
            "read_recent_kline",
            {"source": "eastmoney", "symbol": "300850"},
            {"source_id": "eastmoney", "symbol": "300850"},
        ),
        (
            "read_kline_range",
            {
                "source_id": "eastmoney",
                "symbol": "300850",
                "start_date": "2026-08-01",
                "end_date": "2026-09-24",
            },
            {
                "source_id": "eastmoney",
                "symbol": "300850",
                "start_date": "20260801",
                "end_date": "20260924",
            },
        ),
        (
            "calculate_technical_indicator",
            {"source_id": "eastmoney", "symbol": "300850", "indicator": "MA"},
            {
                "source_id": "eastmoney",
                "symbol": "300850",
                "indicator": "moving_average",
            },
        ),
        (
            "calculate_technical_indicator",
            {"source_id": "eastmoney", "symbol": "300850", "indicator": "MACD"},
            {
                "source_id": "eastmoney",
                "symbol": "300850",
                "indicator": "macd",
            },
        ),
        (
            "calculate_technical_indicator",
            {"source_id": "eastmoney", "symbol": "300850", "indicator": "RSI"},
            {
                "source_id": "eastmoney",
                "symbol": "300850",
                "indicator": "rsi",
            },
        ),
        (
            "read_company_news_akshare",
            {"stock": "300850"},
            {"symbol": "300850"},
        ),
        (
            "read_company_announcements_akshare",
            {"stock": "300850"},
            {"symbol": "300850"},
        ),
    ],
)
def test_tool_call_aliases_are_normalized_before_langchain_schema_validation(
    tool_runtime: tuple[ToolRegistry, dict[str, Any]],
    tool_name: str,
    authored_arguments: dict[str, Any],
    expected_arguments: dict[str, Any],
) -> None:
    registry, tools = tool_runtime
    tool = tools[tool_name]
    context = SimpleNamespace(
        registry=registry,
        executor=object(),
        events=SimpleNamespace(),
        run_id="normalization-test",
        conversation_id="normalization-test",
        tenant_id="tenant",
        owner_id="owner",
        knowledge_base_ids=(),
    )
    tool_call_id = "call-normalization-test"
    request = ToolCallRequest(
        tool_call={
            "id": tool_call_id,
            "name": tool_name,
            "args": authored_arguments,
            "type": "tool_call",
        },
        tool=tool,
        state={"tool_results": [], "tool_call_count": 0},
        runtime=Runtime(context=context),
    )
    received_arguments: dict[str, Any] = {}

    async def handler(normalized_request: ToolCallRequest) -> ToolMessage:
        # This is the LangChain StructuredTool boundary that rejected these
        # model calls before the registry's executor-side normalization ran.
        parsed = normalized_request.tool.args_schema.model_validate(
            normalized_request.tool_call["args"]
        )
        received_arguments.update(parsed.model_dump())
        payload = {
            NATIVE_TOOL_RESULT_MARKER: True,
            "record": {
                "success": True,
                "tool_name": tool_name,
                "effect": "read",
                "arguments": received_arguments,
                "result": {"items": [{"symbol": "300850"}]},
            },
            "evidence": None,
        }
        return ToolMessage(
            content=json.dumps(payload),
            name=tool_name,
            tool_call_id=tool_call_id,
            status="success",
        )

    result = asyncio.run(ToolExecutionMiddleware().awrap_tool_call(request, handler))

    assert result.update["tool_results"][0]["success"] is True
    for key, value in expected_arguments.items():
        assert received_arguments[key] == value
