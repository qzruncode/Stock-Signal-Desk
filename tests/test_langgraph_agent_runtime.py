"""Focused checks for the generic LangChain/LangGraph Agent loop."""

from __future__ import annotations

import asyncio
from collections import OrderedDict, defaultdict
from typing import Any, Mapping

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict, Field

from src.agent.langgraph_runtime.runtime import LangGraphRuntimeManager
from src.tools.base import ToolSpec, object_schema
from src.tools.registry import ToolRegistry


class ScriptedChatModel(BaseChatModel):
    """Small native tool-calling model double; no legacy structured contracts."""

    responses: list[AIMessage] = Field(default_factory=list)
    calls: list[list[BaseMessage]] = Field(default_factory=list, exclude=True)
    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def _llm_type(self) -> str:
        return "scripted_agent_loop"

    def bind_tools(self, tools: list[Any], *, tool_choice: Any | None = None, **kwargs: Any) -> Any:
        return self.bind(tools=tools, tool_choice=tool_choice, **kwargs)

    def _generate(self, *_args: Any, **_kwargs: Any) -> ChatResult:
        raise NotImplementedError("test model is async-only")

    async def _agenerate(self, messages: list[BaseMessage], **_kwargs: Any) -> ChatResult:
        self.calls.append(list(messages))
        if not self.responses:
            raise AssertionError("the generic Agent loop asked for an unscripted model response")
        return ChatResult(generations=[ChatGeneration(message=self.responses.pop(0))])


class FakeAtomicExecutor:
    """Preserves the executor return contract while recording actual calls."""

    def __init__(
        self,
        outcomes: Mapping[str, list[Mapping[str, Any]]] | None = None,
        *,
        delay_seconds: float = 0.0,
    ) -> None:
        self.outcomes: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for name, values in (outcomes or {}).items():
            self.outcomes[name].extend(dict(value) for value in values)
        self.delay_seconds = delay_seconds
        self.calls: list[dict[str, Any]] = []
        self.active = 0
        self.max_active = 0

    async def execute(
        self,
        action: Mapping[str, Any],
        *,
        approved: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        call = dict(action)
        self.calls.append({**call, "approved": approved})
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
            queued = self.outcomes.get(str(call["tool_name"])) or []
            outcome = queued.pop(0) if queued else {"success": True}
        finally:
            self.active -= 1

        action_id = str(call["action_id"])
        success = outcome.get("success") is not False
        data_time = outcome.get("data_time", "2026-08-08")
        source_refs = list(outcome.get("source_refs") or ["https://source.example/test"])
        result = dict(outcome.get("result") or {"source_id": call["arguments"].get("source_id")})
        record = {
            "id": action_id,
            "action_id": action_id,
            "tool_name": str(call["tool_name"]),
            "effect": "side_effect" if approved else "read",
            "arguments": dict(call["arguments"]),
            "success": success,
            "partial": bool(outcome.get("partial")),
            "errors": list(outcome.get("errors") or []),
            "error_code": outcome.get("error_code"),
            "source_refs": source_refs,
            "data_time": data_time,
            "data_time_provenance": "source" if data_time else "unavailable",
            "result": result,
        }
        if not success:
            return record, None
        evidence_id = f"ev_{action_id}"
        return record, {
            "id": evidence_id,
            "evidence_id": evidence_id,
            "action_id": action_id,
            "tool_name": str(call["tool_name"]),
            "effect": record["effect"],
            "success": True,
            "partial": bool(outcome.get("partial")),
            "entities": dict(call["arguments"]),
            "data_time": data_time,
            "data_time_provenance": record["data_time_provenance"],
            "source_refs": source_refs,
            "result": result,
        }


def _registry(*tools: ToolSpec) -> ToolRegistry:
    registry = object.__new__(ToolRegistry)
    registry._tools = OrderedDict((tool.name, tool) for tool in tools)
    registry._owners = {tool.name: "test" for tool in tools}
    return registry


def _search_operation() -> ToolSpec:
    return ToolSpec(
        name="search_source",
        description="从一个明确来源检索公开材料",
        parameters=object_schema(
            {
                "source_id": {"type": "string", "enum": ["primary", "secondary"]},
                "query": {"type": "string"},
            },
            required=("source_id", "query"),
        ),
        executor=lambda **_kwargs: {"success": True},
        source_catalog=(
            {"id": "primary", "name": "主来源", "purpose": "测试来源"},
            {"id": "secondary", "name": "备用来源", "purpose": "测试来源"},
        ),
        max_attempts=1,
    )


def _tool_call(call_id: str, source_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "search_source",
                "args": {"source_id": source_id, "query": "测试问题"},
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


async def _run(
    *,
    model: ScriptedChatModel,
    registry: ToolRegistry | None = None,
    executor: FakeAtomicExecutor | None = None,
    conversation_id: str,
) -> tuple[Any, FakeAtomicExecutor]:
    atomic_executor = executor or FakeAtomicExecutor()
    manager = LangGraphRuntimeManager(registry=registry or _registry(_search_operation()))
    await manager.start(testing=True)
    try:
        result = await manager.run_new(
            messages=[{"role": "user", "content": "测试问题"}],
            user_text="测试问题",
            system_prompt="",
            llm_config={},
            database=None,
            controller=None,
            run_id=f"run-{conversation_id}",
            conversation_id=conversation_id,
            run_attempt=1,
            tenant_id="tenant",
            owner_id="owner",
            model=model,
            executor=atomic_executor,
        )
        return result, atomic_executor
    finally:
        await manager.close()


def test_plain_answer_uses_the_standard_model_completion_path() -> None:
    async def scenario() -> None:
        result, executor = await _run(
            model=ScriptedChatModel(responses=[AIMessage(content="这是无需外部取证的解释。")]),
            registry=_registry(),
            conversation_id="plain-answer",
        )
        assert result.status == "completed"
        assert result.final_text == "这是无需外部取证的解释。"
        assert executor.calls == []
        assert [item["stage"] for item in result.stage_history or []] == ["model", "model", "publish"]

    asyncio.run(scenario())


def test_failed_source_is_an_observation_and_model_can_choose_another_source() -> None:
    async def scenario() -> None:
        result, executor = await _run(
            model=ScriptedChatModel(
                responses=[
                    _tool_call("first", "primary"),
                    _tool_call("second", "secondary"),
                    AIMessage(content="备用来源已返回可用材料【证据 ev_second】"),
                ]
            ),
            executor=FakeAtomicExecutor(
                {
                    "search_source": [
                        {"success": False, "errors": ["primary unavailable"], "error_code": "provider_unavailable"},
                        {"success": True, "result": {"headline": "secondary result"}},
                    ]
                }
            ),
            conversation_id="alternate-source",
        )
        assert result.status == "completed"
        assert [call["arguments"]["source_id"] for call in executor.calls] == ["primary", "secondary"]
        assert result.state["tool_results"][0]["success"] is False
        assert result.state["tool_results"][1]["success"] is True
        assert "ev_second" in result.final_text

    asyncio.run(scenario())


def test_independent_read_operations_run_in_parallel() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_source",
                            "args": {"source_id": "primary", "query": "A"},
                            "id": "parallel-a",
                            "type": "tool_call",
                        },
                        {
                            "name": "search_source",
                            "args": {"source_id": "secondary", "query": "B"},
                            "id": "parallel-b",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(content="两项来源均已返回【证据 ev_parallel-a】【证据 ev_parallel-b】"),
            ]
        )
        result, executor = await _run(
            model=model,
            executor=FakeAtomicExecutor(delay_seconds=0.02),
            conversation_id="parallel-reads",
        )
        assert result.status == "completed"
        assert len(executor.calls) == 2
        assert executor.max_active == 2
        assert result.state["tool_call_count"] == 2

    asyncio.run(scenario())


def test_missing_evidence_link_reenters_model_without_a_fixed_verify_workflow() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(
            responses=[
                _tool_call("evidence", "primary"),
                AIMessage(content="这里使用了刚才的外部材料，但没有引用。"),
                AIMessage(content="修订后仅保留可追溯结论【证据 ev_evidence】"),
            ]
        )
        result, _executor = await _run(model=model, conversation_id="evidence-repair")
        assert result.status == "completed"
        assert result.state["evidence_repair_count"] == 1
        assert len(model.calls) == 3
        assert any(
            item["stage"] == "evidence" and item["status"] == "started"
            for item in result.stage_history or []
        )
        assert result.final_text.endswith("【证据 ev_evidence】")

    asyncio.run(scenario())


def test_final_answer_persists_generic_claim_to_evidence_mapping() -> None:
    async def scenario() -> None:
        result, _executor = await _run(
            model=ScriptedChatModel(
                responses=[
                    _tool_call("claim-evidence", "primary"),
                    AIMessage(content="截至2026-08-08，主来源已返回可用材料【证据 ev_claim-evidence】"),
                ]
            ),
            conversation_id="claim-evidence",
        )
        assert result.status == "completed"
        claims = result.state["claim_evidence"]
        assert len(claims) == 1
        assert claims[0]["evidence_ids"] == ["ev_claim-evidence"]
        assert all(claims[0]["checks"].values())
        completed = [
            item
            for item in result.stage_history or []
            if item["stage"] == "evidence" and item["status"] == "completed"
        ]
        assert completed[-1]["details"]["claim_count"] == 1

    asyncio.run(scenario())


def test_uncited_freshness_claim_reenters_model_even_with_a_dated_evidence_elsewhere() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(
            responses=[
                _tool_call("time-scope", "primary"),
                AIMessage(
                    content=(
                        "基于最新公开资料，以下为概括。\n\n"
                        "主来源已返回可用材料【证据 ev_time-scope】"
                    )
                ),
                AIMessage(content="主来源已返回可用材料【证据 ev_time-scope】"),
            ]
        )
        result, _executor = await _run(
            model=model,
            conversation_id="freshness-claim-scope",
        )

        assert result.status == "completed"
        assert result.state["evidence_repair_count"] == 1
        assert len(model.calls) == 3
        assert "最新" not in result.final_text
        repair = next(
            item
            for item in result.stage_history or []
            if item["stage"] == "evidence" and item["status"] == "started"
        )
        assert any("紧邻的 evidence_id" in issue for issue in repair["details"]["issues"])

    asyncio.run(scenario())


def test_tool_budget_stops_with_a_partial_answer_and_explicit_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_MAX_TOOL_CALLS", "1")

    async def scenario() -> None:
        result, executor = await _run(
            model=ScriptedChatModel(
                responses=[
                    _tool_call("within-budget", "primary"),
                    _tool_call("over-budget", "secondary"),
                    AIMessage(content="仅基于第一项来源的已知内容【证据 ev_within-budget】"),
                ]
            ),
            conversation_id="tool-budget",
        )
        assert result.status == "partial"
        assert result.error_code == "tool_call_budget_exceeded"
        assert [call["action_id"] for call in executor.calls] == ["within-budget"]
        assert result.state["work_budget_exhausted"] is True
        assert "未完成全部取证" in result.final_text

    asyncio.run(scenario())
