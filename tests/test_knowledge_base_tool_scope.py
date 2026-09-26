from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.agent.langgraph_runtime.runtime import LangGraphRuntimeManager
from src.agent.tool_dispatch import ToolDispatchRequest, ToolDispatcher
from src.tools.base import ToolSpec, current_tool_execution_context, object_schema
from src.tools.registry import ToolRegistry


def _scope_tool(query: str):
    return {
        "success": True,
        "query": query,
        "results": [current_tool_execution_context()],
        "result_items": [],
    }


def test_knowledge_search_receives_only_server_injected_scope():
    tool = ToolSpec(
        name="search_knowledge_base",
        description="Search attached knowledge bases.",
        parameters=object_schema({"query": {"type": "string"}}, required=("query",)),
        executor=_scope_tool,
        category="research",
        web_fallback=False,
    )
    registry = ToolRegistry.from_tools([tool])
    dispatcher = ToolDispatcher(
        registry,
        isolated_executor=lambda *_args, **_kwargs: None,
        compact_result=lambda _name, value: value,
        attach_fallback=lambda _name, _args, value: value,
    )
    outcome = dispatcher.execute(
        ToolDispatchRequest(
            tool_name="search_knowledge_base",
            arguments={"query": "annual report"},
            idempotency_key="stable-call",
            tenant_id="tenant-1",
            owner_id="owner-1",
            knowledge_base_ids=("kb-allowed",),
        ),
        cancel_event=__import__("threading").Event(),
        progress_observer=lambda _update: None,
    )

    scoped = outcome.canonical_result["results"][0]
    assert scoped == {
        "conversation_id": "",
        "run_id": "",
        "tenant_id": "tenant-1",
        "owner_id": "owner-1",
        "knowledge_base_ids": "kb-allowed",
    }
    schema = registry.get_all_schemas()[0]["function"]["parameters"]["properties"]
    assert set(schema) == {"query"}


def test_checkpoint_restore_keeps_only_bounded_server_selected_knowledge_bases():
    class CheckpointGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(
                values={
                    "knowledge_base_ids": [" kb-1 ", "kb-1", *[f"kb-{index}" for index in range(2, 12)]]
                }
            )

    async def scenario():
        manager = LangGraphRuntimeManager(registry=ToolRegistry.from_tools([]), response_format=None)
        graph = CheckpointGraph()
        ids = await manager._checkpoint_knowledge_base_ids(graph, "conversation")
        assert ids == [f"kb-{index}" for index in range(1, 9)]

    asyncio.run(scenario())
