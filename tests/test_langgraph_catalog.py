from __future__ import annotations

import json
from collections import OrderedDict
from datetime import datetime

from src.agent.langgraph_runtime.agent_tools import (
    NATIVE_TOOL_RESULT_MARKER,
    _native_tool_result_envelope,
    build_langchain_tools,
)
from src.agent.langgraph_runtime.catalog import ToolCatalog
from src.tools.base import ToolSpec, object_schema
from src.tools.registry import ToolRegistry


def _registry() -> ToolRegistry:
    registry = object.__new__(ToolRegistry)
    operation = ToolSpec(
        name="search_source",
        description="从一个来源检索公开材料",
        parameters=object_schema(
            {
                "source_id": {"type": "string", "enum": ["primary"]},
                "query": {"type": "string"},
            },
            required=("source_id", "query"),
        ),
        executor=lambda **_kwargs: {"success": True},
        source_catalog=(
            {"id": "primary", "name": "主来源", "purpose": "测试来源"},
        ),
    )
    registry._tools = OrderedDict([(operation.name, operation)])
    registry._owners = {operation.name: "test"}
    return registry


def test_model_context_keeps_sources_but_does_not_duplicate_bound_schema() -> None:
    catalog = ToolCatalog(_registry())

    entry = json.loads(catalog.model_context())[0]

    assert entry == {
        "operation": "search_source",
        "effect": "read",
        "sources": [
            {"id": "primary", "name": "主来源", "purpose": "测试来源"},
        ],
    }
    assert "description" not in entry
    assert "fields" not in entry


def test_prompt_context_does_not_remove_any_registered_operation() -> None:
    registry = _registry()
    catalog = ToolCatalog(registry)

    assert {item["operation"] for item in json.loads(catalog.model_context())} == set(
        registry.get_tool_names()
    )
    assert {tool.name for tool in build_langchain_tools(registry)} == set(
        registry.get_tool_names()
    )


def test_native_tool_result_envelope_is_json_even_with_date_like_provider_values() -> None:
    encoded = _native_tool_result_envelope(
        {"success": True, "result": {"retrieved_at": datetime(2026, 9, 12, 8, 30)}},
        {"evidence_id": "ev-test"},
    )

    payload = json.loads(encoded)

    assert payload[NATIVE_TOOL_RESULT_MARKER] is True
    assert payload["record"]["result"]["retrieved_at"] == "2026-09-12 08:30:00"
