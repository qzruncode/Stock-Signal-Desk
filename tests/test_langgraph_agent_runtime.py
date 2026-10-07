"""Focused checks for the generic LangChain/LangGraph Agent loop."""

from __future__ import annotations

import asyncio
from collections import OrderedDict, defaultdict
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, Mapping

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict, Field

from src.agent.langgraph_runtime.answer_contract import STRUCTURED_OUTPUT_TOOL_NAME
from src.agent.langgraph_runtime.catalog import ToolCatalog
from src.agent.langgraph_runtime.executor import action_fingerprint
from src.agent.langgraph_runtime.events import GraphEventBridge
from src.agent.langgraph_runtime.graph import DEFAULT_RESPONSE_FORMAT
from src.agent.langgraph_runtime.middleware import (
    OperationPolicyMiddleware,
    _failed_read_tool_call_keys,
    _source_fallback_reason,
    _tool_call_key,
    _user_requests_knowledge_base_only,
)
from src.agent.langgraph_runtime.reflection import ReflectionReview
from src.agent.run_registry import RunBroadcaster
from src.agent.langgraph_runtime.runtime import LangGraphRuntimeManager
from src.tools.base import ToolSpec, object_schema
from src.tools.registry import ToolRegistry
from src.tools.search_knowledge_base import TOOL as SEARCH_KNOWLEDGE_BASE_TOOL


class ScriptedChatModel(BaseChatModel):
    """Small native tool-calling model double; no legacy structured contracts."""

    responses: list[AIMessage] = Field(default_factory=list)
    calls: list[list[BaseMessage]] = Field(default_factory=list, exclude=True)
    call_options: list[dict[str, Any]] = Field(default_factory=list, exclude=True)
    structured_output_streams: list[bool | None] = Field(default_factory=list, exclude=True)
    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def _llm_type(self) -> str:
        return "scripted_agent_loop"

    def bind_tools(self, tools: list[Any], *, tool_choice: Any | None = None, **kwargs: Any) -> Any:
        return self.bind(tools=tools, tool_choice=tool_choice, **kwargs)

    def with_structured_output(
        self,
        schema: Any,
        *,
        stream: bool | None = None,
        **kwargs: Any,
    ) -> Any:
        self.structured_output_streams.append(stream)
        return super().with_structured_output(schema, **kwargs)

    def _generate(self, *_args: Any, **_kwargs: Any) -> ChatResult:
        raise NotImplementedError("test model is async-only")

    async def _agenerate(self, messages: list[BaseMessage], **_kwargs: Any) -> ChatResult:
        self.calls.append(list(messages))
        self.call_options.append(dict(_kwargs))
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
        result = dict(outcome.get("result") or {"value": "test-observation"})
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


class BlockingAtomicExecutor(FakeAtomicExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = True

    async def execute(
        self,
        action: Mapping[str, Any],
        *,
        approved: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        if self.block:
            self.started.set()
            await self.release.wait()
        return await super().execute(action, approved=approved)


def _registry(*tools: ToolSpec) -> ToolRegistry:
    registry = object.__new__(ToolRegistry)
    registry._tools = OrderedDict((tool.name, tool) for tool in tools)
    registry._owners = {tool.name: "test" for tool in tools}
    return registry


def _search_operation(*, category: str = "data") -> ToolSpec:
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
        category=category,
        max_attempts=1,
    )


def _web_search_operation() -> ToolSpec:
    return ToolSpec(
        name="search_web_source",
        description="通过网页搜索获取公开来源。",
        parameters=object_schema(
            {
                "source_id": {"type": "string", "enum": ["auto"]},
                "query": {"type": "string"},
            },
            required=("source_id", "query"),
        ),
        executor=lambda **_kwargs: {"success": True},
        category="source_search",
        source_catalog=({"id": "auto", "name": "自动网页搜索", "purpose": "测试"},),
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


def _named_tool_call(call_id: str, tool_name: str, arguments: Mapping[str, Any]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": tool_name,
                "args": dict(arguments),
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


def _structured_output_call(
    call_id: str,
    blocks: list[Mapping[str, Any]],
    *,
    title: str = "",
    profile: str | None = None,
) -> AIMessage:
    arguments: dict[str, Any] = {
        "title": title,
        "blocks": [dict(block) for block in blocks],
    }
    if profile is not None:
        arguments["profile"] = profile
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": STRUCTURED_OUTPUT_TOOL_NAME,
                "args": arguments,
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


def _reflection_output_call(
    call_id: str,
    *,
    verdict: str = "pass",
    summary: str = "候选回答与现有证据边界一致。",
    issues: list[Mapping[str, Any]] | None = None,
) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": ReflectionReview.__name__,
                "args": {
                    "verdict": verdict,
                    "summary": summary,
                    "issues": [dict(issue) for issue in (issues or [])],
                },
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


def _company_news_operation() -> ToolSpec:
    return ToolSpec(
        name="read_company_news_akshare",
        description="返回新闻来源链接，不包含正文。",
        parameters=object_schema(
            {"symbol": {"type": "string"}},
            required=("symbol",),
        ),
        executor=lambda **_kwargs: {"success": True},
    )


def _company_research_operation() -> ToolSpec:
    return ToolSpec(
        name="read_company_research_reports_akshare",
        description="返回研报来源链接，不包含正文。",
        parameters=object_schema(
            {"symbol": {"type": "string"}},
            required=("symbol",),
        ),
        executor=lambda **_kwargs: {"success": True},
    )


def _web_source_operation() -> ToolSpec:
    return ToolSpec(
        name="read_web_source",
        description="读取指定 URL 的正文。",
        parameters=object_schema(
            {
                "source_id": {"type": "string", "enum": ["http"]},
                "url": {"type": "string"},
            },
            required=("source_id", "url"),
        ),
        executor=lambda **_kwargs: {"success": True},
        source_catalog=({"id": "http", "name": "HTTP", "purpose": "测试"},),
    )


def _content_selection_operation() -> ToolSpec:
    return ToolSpec(
        name="select_content_sources",
        description="选择本轮正文候选。",
        parameters=object_schema(
            {
                "source_ids": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1},
                    "minItems": 1,
                    "maxItems": 4,
                }
            },
            required=("source_ids",),
        ),
        executor=lambda **_kwargs: {"success": True},
    )


async def _run(
    *,
    model: ScriptedChatModel,
    registry: ToolRegistry | None = None,
    executor: FakeAtomicExecutor | None = None,
    conversation_id: str,
    response_format: Any | None = None,
    knowledge_base_ids: tuple[str, ...] = (),
    user_text: str = "测试问题",
) -> tuple[Any, FakeAtomicExecutor]:
    atomic_executor = executor or FakeAtomicExecutor()
    manager = LangGraphRuntimeManager(
        registry=registry or _registry(_search_operation()),
        response_format=response_format,
    )
    await manager.start(testing=True)
    try:
        result = await manager.run_new(
            messages=[{"role": "user", "content": user_text}],
            user_text=user_text,
            system_prompt="",
            llm_config={},
            database=None,
            controller=None,
            run_id=f"run-{conversation_id}",
            conversation_id=conversation_id,
            run_attempt=1,
            tenant_id="tenant",
            owner_id="owner",
            knowledge_base_ids=knowledge_base_ids,
            model=model,
            executor=atomic_executor,
            agent_mode="direct",
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


def test_unstructured_handoff_never_enters_final_answer_format_repair() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(responses=[
            _structured_output_call("unbound-answer", [{"kind": "answer", "content": "错误的最终契约"}]),
            AIMessage(content="普通文本交接。"),
        ])
        result, _ = await _run(model=model, registry=_registry(_search_operation()), conversation_id="unstructured-handoff")
        assert result.status == "completed"
        assert result.final_text == "普通文本交接。"
        assert result.state["response_repair_count"] == 0
        assert all(options.get("tools") for options in model.call_options)
        for messages in model.calls:
            prompt = str(messages[0].content)
            assert "必须调用结构化输出工具 StructuredAgentAnswer" not in prompt
            assert "不要调用 StructuredAgentAnswer" in prompt
        assert any(isinstance(message, ToolMessage) and message.tool_call_id == "unbound-answer" for message in model.calls[-1])

    asyncio.run(scenario())


def test_failed_read_identity_ignores_model_generated_call_ids() -> None:
    state = {
        "tool_results": [
            {
                "id": "provider-call-1",
                "tool_name": "search_source",
                "arguments": {"source_id": "primary", "query": "测试问题"},
                "effect": "read",
                "success": False,
            }
        ]
    }

    assert _tool_call_key("search_source", {"query": "测试问题", "source_id": "primary"}) in _failed_read_tool_call_keys(state)


def test_native_general_answer_can_publish_without_external_evidence() -> None:
    async def scenario() -> None:
        result, _executor = await _run(
            model=ScriptedChatModel(
                responses=[
                    _structured_output_call(
                        "general-final",
                        [{"kind": "answer", "content": "这是一个通用概念解释。"}],
                        profile="general",
                    )
                ]
            ),
            registry=_registry(),
            conversation_id="general-structured-answer",
            response_format=DEFAULT_RESPONSE_FORMAT,
        )

        assert result.status == "completed"
        assert result.error_code is None
        assert result.final_text == "这是一个通用概念解释。"
        assert result.state["structured_answer"]["profile"] == "general"
        assert result.state["claim_evidence"][0]["requires_evidence"] is False
        assert result.state["reflection_status"] == "skipped"
        assert len(result.stage_history or []) == 5

    asyncio.run(scenario())


def test_direct_model_selects_a_contextual_pdf_query_without_entry_preretrieval() -> None:
    async def scenario() -> None:
        hit = {
            "evidence_id": "ev_kb_level_0_page_14",
            "filename": "Agentic_Design_Patterns_Complete.pdf",
            "page_start": 14,
            "page_end": 14,
            "snippet": "Level 0 has a complete lack of current-event awareness.",
            "url": "/api/v1/knowledge-bases/documents/doc-1/content#page=14",
        }
        search_outcome = {
            "success": True,
            "data_time": None,
            "source_refs": [hit["url"]],
            "result": {"success": True, "results": [hit], "result_items": []},
        }
        question = "根据所选 PDF，解释 Level 0 缺少什么能力。"
        model = ScriptedChatModel(responses=[
            _named_tool_call(
                "model-selected-pdf-search",
                "search_knowledge_base",
                {"query": "Level 0 current-event awareness limitation"},
            ),
            _structured_output_call(
                "pdf-grounded-answer",
                [{
                    "kind": "fact",
                    "content": "PDF指出，Level 0 缺乏对当前事件的感知。",
                    "source_ids": [1],
                }],
                profile="general",
            ),
            _reflection_output_call("pdf-grounded-reflection"),
        ])
        result, executor = await _run(
            model=model,
            registry=_registry(SEARCH_KNOWLEDGE_BASE_TOOL),
            executor=FakeAtomicExecutor({"search_knowledge_base": [search_outcome]}),
            conversation_id="model-led-pdf-search",
            response_format=DEFAULT_RESPONSE_FORMAT,
            knowledge_base_ids=("kb-test",),
            user_text=question,
        )

        assert result.status == "completed", (
            result.error_code, result.final_text, result.state.get("terminal_detail")
        )
        assert len(executor.calls) == 1
        assert executor.calls[0]["tool_name"] == "search_knowledge_base"
        assert executor.calls[0]["arguments"]["query"] != question
        assert executor.calls[0]["arguments"]["query"] == "Level 0 current-event awareness limitation"
        assert "ev_kb_level_0_page_14" in result.final_text

    asyncio.run(scenario())


@pytest.mark.parametrize("knowledge_base_ids", [(), ("kb-test",)])
def test_greeting_does_not_create_a_knowledge_search(knowledge_base_ids) -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(responses=[
            _structured_output_call("greeting", [{"kind": "answer", "content": "你好！"}], profile="general"),
        ])
        result, executor = await _run(
            model=model,
            registry=_registry(SEARCH_KNOWLEDGE_BASE_TOOL, _search_operation()),
            conversation_id="greeting-with-tool-priority",
            response_format=DEFAULT_RESPONSE_FORMAT,
            knowledge_base_ids=knowledge_base_ids,
            user_text="你好",
        )
        assert result.status == "completed"
        assert result.final_text == "你好！"
        assert executor.calls == []
        names = {tool.name for tool in model.call_options[0]["tools"]}
        assert ("search_knowledge_base" in names) is bool(knowledge_base_ids)
        assert "search_source" in names
        assert "首轮必须由你先作一次明确的来源决策" not in str(model.calls[0][0].content)

    asyncio.run(scenario())


def test_knowledge_selection_updates_model_source_decision_across_checkpoint_continuation() -> None:
    async def scenario() -> None:
        executor = FakeAtomicExecutor()
        manager = LangGraphRuntimeManager(
            registry=_registry(SEARCH_KNOWLEDGE_BASE_TOOL, _search_operation()), response_format=None,
        )
        await manager.start(testing=True)
        try:
            for turn, ids in enumerate([("kb-test",), (), ("kb-other",)], 1):
                question = "你好" if turn == 1 else "谢谢"
                model = ScriptedChatModel(responses=[AIMessage(content="你好！" if turn == 1 else "不客气。")])
                result = await manager.run_new(
                    messages=[{"role": "user", "content": question}], user_text=question,
                    system_prompt="", llm_config={}, database=None, controller=None,
                    run_id=f"kb-selection-{turn}", conversation_id="kb-selection-continuation",
                    run_attempt=1, tenant_id="tenant", owner_id="owner",
                    model=model, executor=executor, agent_mode="direct", history_mode="continue",
                    knowledge_base_ids=ids,
                )
                assert result.status == "completed"
                assert result.state["knowledge_base_ids"] == list(ids)
                assert "首轮必须由你先作一次明确的来源决策" not in str(model.calls[0][0].content)
                assert ("search_knowledge_base" in {t.name for t in model.call_options[0]["tools"]}) is bool(ids)
            assert executor.calls == []
        finally:
            await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("retrieval_success", [True, False])
def test_knowledge_observation_returns_to_native_loop_for_model_selected_supplement(retrieval_success) -> None:
    async def scenario() -> None:
        retrieval = {
            "success": retrieval_success,
            "error_code": None if retrieval_success else "retrieval_timeout",
            "errors": [] if retrieval_success else ["知识库检索超时"],
            "result": {
                "success": retrieval_success, "results": [], "no_evidence": True,
                "error_code": None if retrieval_success else "retrieval_timeout",
            },
        }
        model = ScriptedChatModel(responses=[
            _named_tool_call("kb-probe", "search_knowledge_base", {"query": "新强联 经营风险"}),
            _named_tool_call("external-probe", "search_source", {"source_id": "primary", "query": "新强联 风险披露"}),
            AIMessage(content="已取得外部补充资料。【证据 ev_external-probe】"),
        ])
        result, executor = await _run(
            model=model,
            registry=_registry(SEARCH_KNOWLEDGE_BASE_TOOL, _search_operation()),
            executor=FakeAtomicExecutor({"search_knowledge_base": [retrieval]}),
            conversation_id=f"kb-supplement-{retrieval_success}",
            knowledge_base_ids=("kb-test",),
            user_text="看下新强联的财报",
        )
        assert result.status == "completed", result.final_text
        assert [call["tool_name"] for call in executor.calls] == ["search_knowledge_base", "search_source"]
        assert result.state["tool_results"][0]["success"] is retrieval_success
        assert result.state["tool_results"][0]["error_code"] == retrieval["error_code"]
        assert {tool.name for tool in model.call_options[0]["tools"]} == {
            "search_knowledge_base", "search_source"
        }
        for options in model.call_options[1:]:
            assert {tool.name for tool in options["tools"]} == {"search_knowledge_base", "search_source"}
            assert options["tool_choice"] != "required"
        assert any(isinstance(message, ToolMessage) for message in model.calls[1])

    asyncio.run(scenario())


def test_unresolved_source_failure_cannot_end_without_a_bounded_web_fallback() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(
            responses=[
                _named_tool_call(
                    "source-failed",
                    "search_source",
                    {"source_id": "primary", "query": "测试问题"},
                ),
                AIMessage(content="主来源失败，但先给出一个没有网页核验的回答。"),
                AIMessage(content="仍然没有完成网页核验。"),
            ]
        )
        executor = FakeAtomicExecutor(
            {"search_source": [{"success": False, "error_code": "provider_unavailable"}]}
        )
        result, _executor = await _run(
            model=model,
            registry=_registry(_search_operation(category="source_read"), _web_search_operation()),
            executor=executor,
            conversation_id="source-fallback-required",
        )

        assert result.status == "partial"
        assert result.error_code == "source_fallback_incomplete"
        assert result.state["fallback_repair_count"] == 1
        assert [call["tool_name"] for call in executor.calls] == ["search_source"]
        assert any(
            item["stage"] == "source_fallback" and item["status"] == "started"
            for item in result.stage_history or []
        )
        assert any(
            item["stage"] == "source_fallback" and item["status"] == "failed"
            for item in result.stage_history or []
        )
        assert len(result.state["source_fallback_attempts"]) == 1
        assert result.state["source_fallback_attempts"][0]["status"] == "failed"

    asyncio.run(scenario())


def test_source_failure_can_recover_with_cited_web_evidence() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(
            responses=[
                _named_tool_call(
                    "source-failed-for-web",
                    "search_source",
                    {"source_id": "primary", "query": "测试问题"},
                ),
                _named_tool_call(
                    "web-fallback",
                    "search_web_source",
                    {"source_id": "auto", "query": "测试问题"},
                ),
                AIMessage(content="网页来源已补充该事实。【证据 ev_web-fallback】"),
            ]
        )
        executor = FakeAtomicExecutor(
            {
                "search_source": [{"success": False, "error_code": "provider_unavailable"}],
                "search_web_source": [{"result": {"items": [{"title": "网页事实"}]}}],
            }
        )
        result, _executor = await _run(
            model=model,
            registry=_registry(_search_operation(category="source_read"), _web_search_operation()),
            executor=executor,
            conversation_id="source-fallback-recovered",
        )

        assert result.status == "completed"
        assert result.error_code is None
        assert [call["tool_name"] for call in executor.calls] == [
            "search_source",
            "search_web_source",
        ]
        assert result.final_text.endswith("【证据 ev_web-fallback】")
        assert result.state["fallback_repair_count"] == 1
        assert len(model.calls) == 3

    asyncio.run(scenario())


@pytest.mark.parametrize("source_id", ["secondary", "primary"])
def test_pending_alternative_tools_execute_before_final_source_checks(source_id: str) -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(responses=[
            AIMessage(content="并行核验两个来源", tool_calls=[
                *_tool_call("failed-primary", "primary").tool_calls,
                *_tool_call("alternative", source_id).tool_calls,
            ]),
            _named_tool_call("web-recovery", "search_web_source", {"source_id": "auto", "query": "测试问题"}),
            _structured_output_call("alternative-answer", [{
                "kind": "fact", "content": "替代来源提供了核验数据。",
                "source_ids": [1],
            }]),
        ])
        executor = FakeAtomicExecutor({"search_source": [
            {"success": False, "error_code": "provider_unavailable"},
            {"success": True},
        ]})
        result, _ = await _run(
            model=model, executor=executor,
            registry=_registry(_search_operation(category="source_read"), _web_search_operation()),
            conversation_id=f"alternative-{source_id}", response_format=DEFAULT_RESPONSE_FORMAT,
        )
        assert result.status == "completed"
        assert {call["action_id"] for call in executor.calls[:2]} == {"failed-primary", "alternative"}
        assert executor.calls[-1]["action_id"] == "web-recovery"
        assert result.state["fallback_repair_count"] == 1
        # Every issued tool call must have a matching observation before the
        # next model request; redirecting after_model used to strand these.
        answered = {getattr(message, "tool_call_id", None) for message in model.calls[-1]}
        assert {"failed-primary", "alternative"} <= answered

    asyncio.run(scenario())


@pytest.mark.parametrize("web_success", [True, False])
@pytest.mark.parametrize("parallel", [False, True])
def test_unsupported_final_answer_requires_an_actual_recovery_tool_turn(web_success: bool, parallel: bool) -> None:
    async def scenario() -> None:
        responses = [
            _tool_call("failed-primary", "primary"),
            _named_tool_call("recovery-read", "search_web_source", {"source_id": "auto", "query": "测试问题"}),
        ]
        if parallel:
            responses[-1].tool_calls.append({
                "name": "search_web_source", "args": {"source_id": "auto", "query": "补充来源"},
                "id": "recovery-read-extra", "type": "tool_call",
            })
        if web_success:
            responses.append(_structured_output_call("recovered-answer", [{
                "kind": "fact", "content": "已从替代来源取得所需信息。", "source_ids": [1],
            }]))
        else:
            responses.append(_structured_output_call("failed-recovery-answer", [{
                "kind": "context", "content": "让我继续尝试其他途径。", "source_ids": [],
            }]))
        model = ScriptedChatModel(responses=responses)
        executor = FakeAtomicExecutor({
            "search_source": [{"success": False, "error_code": "provider_unavailable", "source_refs": ["数据接口"]}],
            "search_web_source": [{"success": web_success}] * (2 if parallel else 1),
        })
        result, _ = await _run(
            model=model, executor=executor,
            registry=_registry(_search_operation(category="source_read"), _web_search_operation(), _web_source_operation()),
            conversation_id=f"required-recovery-{web_success}-{parallel}", response_format=DEFAULT_RESPONSE_FORMAT,
        )
        assert len(executor.calls) == (3 if parallel else 2)
        assert result.state["fallback_repair_count"] == 1
        recovery_request = model.call_options[1]
        assert recovery_request["tool_choice"] == "required"
        assert {tool.name for tool in recovery_request["tools"]} == {"search_web_source"}
        assert len(model.call_options[2]["tools"]) == 4  # source, search, reader, answer
        assert result.state["fallback_feedback"] == ""
        if web_success:
            assert result.status == "completed"
            assert "ev_recovery-read" in result.final_text
        else:
            assert result.status == "partial"
            assert result.error_code == "source_fallback_incomplete"
            assert "本轮分析已结束" in result.final_text
            assert "让我" not in result.final_text

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid_schema", [False, True])
def test_response_format_repair_does_not_force_unnecessary_source_recovery(invalid_schema) -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(responses=[
            _tool_call("failed-primary", "primary"),
            _named_tool_call("usable-alternative", "read_web_source", {"source_id": "http", "url": "https://source.example/test"}),
            _structured_output_call("invalid-format", []) if invalid_schema else AIMessage(content="替代来源已取得数据。"),
            _structured_output_call("valid-answer", [{
                "kind": "fact", "content": "替代来源已取得数据。", "source_ids": [1],
            }]),
        ])
        result, executor = await _run(
            model=model,
            executor=FakeAtomicExecutor({"search_source": [{"success": False}, {"success": True}]}),
            registry=_registry(_search_operation(category="source_read"), _web_search_operation(), _web_source_operation()),
            conversation_id=f"format-before-source-{invalid_schema}", response_format=DEFAULT_RESPONSE_FORMAT,
        )
        assert result.status == "completed"
        assert len(executor.calls) == 2
        assert result.state["response_repair_count"] == 1
        assert result.state["fallback_repair_count"] == 1
        assert result.state["evidence_repair_count"] == 0
        assert {tool.name for tool in model.call_options[-1]["tools"]} == {STRUCTURED_OUTPUT_TOOL_NAME}
    asyncio.run(scenario())


def test_source_recovery_without_a_url_binds_search_not_an_untargeted_reader() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(responses=[
            _tool_call("failed-primary", "primary"),
            _named_tool_call("searched", "search_web_source", {"source_id": "auto", "query": "测试问题"}),
            _structured_output_call("supported", [{"kind": "fact", "content": "取得替代来源。", "source_ids": [1]}]),
        ])
        result, executor = await _run(
            model=model,
            executor=FakeAtomicExecutor({"search_source": [{"success": False, "source_refs": ["行情接口"]}]}),
            registry=_registry(_search_operation(category="source_read"), _web_search_operation(), _web_source_operation()),
            conversation_id="source-recovery-search-first", response_format=DEFAULT_RESPONSE_FORMAT,
        )
        assert result.status == "completed"
        assert {tool.name for tool in model.call_options[1]["tools"]} == {"search_web_source"}
        assert [call["tool_name"] for call in executor.calls] == ["search_source", "search_web_source"]
    asyncio.run(scenario())


def test_uncited_successful_web_read_cannot_clear_source_failures() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(responses=[
            _tool_call("failed-primary", "primary"),
            _named_tool_call("unrelated-web", "search_web_source", {"source_id": "auto", "query": "另一个问题"}),
            AIMessage(content="这段结论没有有效的证据引用。"),
            AIMessage(content="让我继续尝试获取数据。"),
        ])
        result, _ = await _run(
            model=model,
            executor=FakeAtomicExecutor({"search_source": [{"success": False}]}),
            registry=_registry(_search_operation(category="source_read"), _web_search_operation()),
            conversation_id="uncited-web-not-recovery",
        )
        assert result.status == "partial"
        assert result.error_code == "source_fallback_incomplete"
        assert result.state["fallback_repair_count"] == 1
        assert "让我" not in result.final_text

    asyncio.run(scenario())


def test_recovery_without_available_web_tools_ends_without_empty_tool_request() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(responses=[
            _tool_call("failed-primary", "primary"), AIMessage(content="让我尝试其他来源。"),
        ])
        result, _ = await _run(
            model=model,
            executor=FakeAtomicExecutor({"search_source": [{"success": False}]}),
            registry=_registry(_search_operation(category="source_read")),
            conversation_id="recovery-unavailable",
        )
        assert result.status == "partial"
        assert result.state["fallback_repair_count"] == 0
        assert len(model.calls) == 2
        assert "本轮分析已结束" in result.final_text

    asyncio.run(scenario())


def test_structured_terminal_candidate_cannot_bypass_source_fallback_gate() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(
            responses=[
                _named_tool_call(
                    "structured-source-failed",
                    "search_source",
                    {"source_id": "primary", "query": "测试问题"},
                ),
                _structured_output_call(
                    "structured-source-candidate",
                    [
                        {
                            "section": "结论",
                            "kind": "fact",
                            "content": "主来源返回失败。",
                            "source_ids": [],
                        }
                    ],
                ),
                _structured_output_call(
                    "structured-source-candidate-2",
                    [
                        {
                            "section": "结论",
                            "kind": "fact",
                            "content": "仍未完成网页核验。",
                            "source_ids": [],
                        }
                    ],
                ),
            ]
        )
        executor = FakeAtomicExecutor(
            {"search_source": [{"success": False, "error_code": "provider_unavailable"}]}
        )
        result, _executor = await _run(
            model=model,
            registry=_registry(_search_operation(category="source_read"), _web_search_operation()),
            executor=executor,
            conversation_id="structured-source-fallback-required",
            response_format=DEFAULT_RESPONSE_FORMAT,
        )

        assert result.status == "partial"
        assert result.error_code == "source_fallback_incomplete"
        assert result.state["fallback_repair_count"] == 1
        assert len(model.calls) == 2

    asyncio.run(scenario())


def test_source_fallback_does_not_stream_an_unverified_structured_candidate() -> None:
    run_id = "source-fallback-rejects-structured-candidate"
    broadcaster = RunBroadcaster(run_id=run_id)
    events = GraphEventBridge(broadcaster, run_id=run_id)
    malformed_table = "| 指标 | 2026H1 | 同比变化 | 是否含营业外收支 | 是否含营业外收支 |"
    candidate = {
        "profile": "research",
        "title": "新强联运营分析",
        "blocks": [
            {
                "kind": "fact",
                "presentation_type": "table",
                "content": malformed_table,
                "source_ids": [],
            }
        ],
    }

    update = OperationPolicyMiddleware._source_fallback_partial(
        state={"evidence": [], "tool_results": []},
        context=SimpleNamespace(events=events),
        last=AIMessage(content=""),
        requirements=[{
            "tool_name": "read_core_financial_indicators_ths",
            "reason": "主来源调用失败",
        }],
        candidate=candidate,
    )

    assert update["status"] == "partial"
    assert update["structured_answer"] is None
    assert "未能取得支持本次分析的有效外部数据" in update["answer_final"]
    assert "是否含营业外收支" not in broadcaster.assistant_text_snapshot
    assert "未能取得支持本次分析的有效外部数据" in broadcaster.assistant_text_snapshot


def test_partial_source_result_requires_web_fallback_when_no_declared_fallback_was_used() -> None:
    spec = _search_operation(category="source_read")

    assert _source_fallback_reason(
        {
            "tool_name": "search_source",
            "success": True,
            "result": {
                "success": True,
                "partial": True,
                "items": [{"headline": "只返回部分结果"}],
                "data_time": "2026-09-04",
            },
        },
        spec,
    ) == "主来源只返回了不完整结果"


def test_non_temporal_source_is_not_forced_to_web_fallback_for_unknown_time() -> None:
    spec = _search_operation(category="source_read")

    assert _source_fallback_reason(
        {
            "tool_name": "search_source",
            "success": True,
            "result": {
                "success": True,
                "item": {"name": "静态资料"},
                "data_time": None,
                "data_time_applicable": False,
                "freshness_unknown": True,
            },
        },
        spec,
    ) is None


def test_native_structured_answer_preserves_block_boundaries_and_evidence() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(
            responses=[
                _tool_call("structured-read", "primary"),
                _structured_output_call(
                    "structured-final",
                    [
                        {
                            "section": "财务数据",
                            "kind": "fact",
                            "content": "| 指标 | 结果 |\n|---|---|\n| 营收 | -6.2% |",
                            "source_ids": [1],
                        },
                        {
                            "section": "综合判断",
                            "kind": "inference",
                            "content": "已核验资料显示，增长仍有压力。",
                            "source_ids": [1],
                        },
                        {
                            "section": "操作建议",
                            "kind": "recommendation",
                            "content": "继续观察后续数据，不一次性重仓。",
                            "source_ids": [1],
                        },
                    ],
                    title="结构化分析",
                ),
                _reflection_output_call("structured-reflection-pass"),
            ]
        )
        result, _executor = await _run(
            model=model,
            conversation_id="structured-answer-contract",
            response_format=DEFAULT_RESPONSE_FORMAT,
        )

        assert result.status == "completed"
        assert result.error_code is None
        assert "| 营收 | -6.2% |" in result.final_text
        assert "操作建议" in result.final_text
        assert result.final_text.count("【证据 ev_structured-read】") == 3
        assert len(result.state["claim_evidence"]) == 3
        assert all(all(claim["checks"].values()) for claim in result.state["claim_evidence"])
        assert result.state["structured_answer_call_id"] == "structured-final"

    asyncio.run(scenario())


def test_research_judgment_runs_reflection_after_hard_evidence_checks() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(
            responses=[
                _tool_call("reflection-read", "primary"),
                _structured_output_call(
                    "reflection-candidate",
                    [
                        {
                            "section": "综合判断",
                            "kind": "inference",
                            "content": "已核验资料显示，增长仍有压力。",
                            "source_ids": [1],
                        }
                    ],
                    profile="research",
                ),
                _reflection_output_call("reflection-pass"),
            ]
        )
        result, _executor = await _run(
            model=model,
            conversation_id="research-reflection-pass",
            response_format=DEFAULT_RESPONSE_FORMAT,
        )

        assert result.status == "completed", (
            f"error_code={result.error_code!r}, reflection={result.state.get('reflection_review')!r}, "
            f"reflection_status={result.state.get('reflection_status')!r}"
        )
        assert result.error_code is None
        assert result.state["reflection_status"] == "passed"
        assert result.state["reflection_call_count"] == 1
        assert result.state["reflection_round"] == 1
        assert result.final_text.endswith("【证据 ev_reflection-read】")
        reflection = [
            item for item in result.stage_history or [] if item["stage"] == "reflection"
        ]
        assert reflection[-1]["details"]["verdict"] == "pass"
        assert model.structured_output_streams[-1] is False

    asyncio.run(scenario())


def test_reflection_revision_is_one_no_tool_structured_rewrite_then_rechecked() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(
            responses=[
                _tool_call("reflection-revise-read", "primary"),
                _structured_output_call(
                    "reflection-revise-candidate",
                    [
                        {
                            "section": "综合判断",
                            "kind": "recommendation",
                            "content": "建议立即重仓。",
                            "source_ids": [1],
                        }
                    ],
                    profile="research",
                ),
                _reflection_output_call(
                    "reflection-revise-request",
                    verdict="revise",
                    summary="建议需要收窄到证据能够支持的范围。",
                    issues=[
                        {
                            "block_index": 1,
                            "category": "reasoning",
                            "severity": "high",
                            "reason": "现有资料不足以支持立即重仓。",
                            "repair_instruction": "改为观察性建议，并保留证据边界。",
                        }
                    ],
                ),
                _structured_output_call(
                    "reflection-revised-answer",
                    [
                        {
                            "section": "综合判断",
                            "kind": "recommendation",
                            "content": "建议继续观察后续数据，不一次性重仓。",
                            "source_ids": [1],
                        }
                    ],
                    profile="research",
                ),
                _reflection_output_call("reflection-revised-pass"),
            ]
        )
        result, _executor = await _run(
            model=model,
            conversation_id="research-reflection-revise",
            response_format=DEFAULT_RESPONSE_FORMAT,
        )

        assert result.status == "completed"
        assert result.state["reflection_status"] == "passed"
        assert result.state["reflection_call_count"] == 2
        assert result.state["reflection_revision_count"] == 1
        assert result.state["reflection_round"] == 2
        assert "立即重仓" not in result.final_text
        assert "继续观察后续数据" in result.final_text
        refiner_tool_names = [
            str(
                tool.get("function", {}).get("name") or tool.get("name")
                if isinstance(tool, Mapping)
                else getattr(tool, "name", "")
            )
            for tool in model.call_options[3].get("tools", [])
        ]
        assert STRUCTURED_OUTPUT_TOOL_NAME in refiner_tool_names
        assert "search_source" not in refiner_tool_names
        assert any(
            item["stage"] == "reflection"
            and item.get("details", {}).get("reflection_status") == "revision_requested"
            for item in result.stage_history or []
        )

    asyncio.run(scenario())


def test_failed_reflection_rewrite_keeps_previous_low_risk_validated_answer() -> None:
    async def scenario() -> None:
        complete_content = (
            "新强联2026年半年度报告（第7页）：营业收入本期 2,073,339,945.39 元，"
            "同比 -6.17%；归母净利润本期 411,334,427.09 元，同比 +2.93%。"
        )
        model = ScriptedChatModel(
            responses=[
                _tool_call("reflection-fallback-read", "primary"),
                _structured_output_call(
                    "reflection-fallback-candidate",
                    [{"kind": "inference", "content": complete_content, "source_ids": [1]}],
                    profile="research",
                ),
                _reflection_output_call(
                    "reflection-fallback-request",
                    verdict="revise",
                    summary="仅有一处低严重度表述需要收窄。",
                    issues=[
                        {
                            "block_index": 1,
                            "category": "evidence_scope",
                            "severity": "low",
                            "reason": "页内表格标题未在命中内容中明确出现。",
                            "repair_instruction": "改称第7页表格，保留已核验数据。",
                        }
                    ],
                ),
                _structured_output_call(
                    "reflection-fallback-incomplete-rewrite",
                    [{"kind": "inference", "content": "| 指标 | 本报告期 | 同比 |", "source_ids": [1]}],
                    profile="research",
                ),
                _reflection_output_call(
                    "reflection-fallback-reject-incomplete",
                    verdict="revise",
                    summary="改写稿只剩表头，遗漏用户所问数据。",
                    issues=[
                        {
                            "block_index": 1,
                            "category": "completeness",
                            "severity": "high",
                            "reason": "没有数据行。",
                            "repair_instruction": "补回完整数据。",
                        }
                    ],
                ),
            ]
        )

        result, _executor = await _run(
            model=model,
            conversation_id="reflection-low-risk-fallback",
            response_format=DEFAULT_RESPONSE_FORMAT,
        )

        assert result.status == "partial", (
            f"reflection_status={result.state.get('reflection_status')!r}, "
            f"revision_count={result.state.get('reflection_revision_count')!r}, "
            f"review={result.state.get('reflection_review')!r}, "
            f"model_calls={len(model.calls)}"
        )
        assert result.error_code == "reflection_revision_exhausted"
        assert result.state["structured_answer"]["blocks"][0]["content"] == complete_content
        assert "2,073,339,945.39" in result.final_text
        assert "411,334,427.09" in result.final_text
        assert "| 指标 | 本报告期 | 同比 |" not in result.final_text
        assert "回退到修订前通过格式与证据硬校验的完整答复" in result.final_text

    asyncio.run(scenario())


def test_low_severity_quoted_phrase_repair_preserves_the_validated_answer() -> None:
    async def scenario() -> None:
        complete_content = (
            "营业收入本期 2,073,339,945.39 元，同比 -6.17%；"
            "归母净利润本期 411,334,427.09 元，同比 +2.93%。"
            "口径为合并报表归属于上市公司股东的净利润，数据见第7页。"
        )
        model = ScriptedChatModel(
            responses=[
                _tool_call("reflection-local-repair-read", "primary"),
                _structured_output_call(
                    "reflection-local-repair-candidate",
                    [{"kind": "inference", "content": complete_content, "source_ids": [1]}],
                    profile="research",
                ),
                _reflection_output_call(
                    "reflection-local-repair-request",
                    verdict="revise",
                    summary="仅一个低严重度短语超出原文。",
                    issues=[
                        {
                            "block_index": 1,
                            "category": "evidence_scope",
                            "severity": "low",
                            "reason": "证据未说明合并口径。",
                            "repair_instruction": "删除“合并报表”字样，保留其余内容。",
                        }
                    ],
                ),
                _reflection_output_call("reflection-local-repair-pass"),
            ]
        )

        result, _executor = await _run(
            model=model,
            conversation_id="reflection-exact-local-repair",
            response_format=DEFAULT_RESPONSE_FORMAT,
            user_text="查询新强联2026年半年度报告营业收入和归母净利润",
        )

        assert result.status == "completed"
        assert result.state["reflection_status"] == "passed"
        assert result.state["reflection_call_count"] == 2
        assert result.state["reflection_revision_count"] == 1
        assert len(model.calls) == 4
        assert model.structured_output_streams == [False, False]
        assert "2,073,339,945.39" in result.final_text
        assert "411,334,427.09" in result.final_text
        assert "合并报表" not in result.final_text
        assert "归属于上市公司股东的净利润" in result.final_text

    asyncio.run(scenario())


def test_low_severity_exact_literal_repair_preserves_complete_research_answer() -> None:
    async def scenario() -> None:
        table = (
            "| 项目 | 本报告期 | 同比 | 页码 |\n|---|---:|---:|---|\n"
            "| 营业收入 | 2,073,339,945.39 元 | -6.17% | 第7页 |\n"
            "| 归母净利润 | 411,334,427.09 元 | +2.93% | 第7页 |\n"
            "| 经营现金流 | 363,763,010.34 元 | +275.77% | 第7页 |"
        )
        note = (
            "口径说明：第55/56页现金流量表中的母公司经营活动现金流量净额为 "
            "271,351,507.32 元，属母公司口径、未采用。"
        )
        complete_content = f"{table}\n\n{note}"
        model = ScriptedChatModel(
            responses=[
                _tool_call("reflection-page-repair-read", "primary"),
                _structured_output_call(
                    "reflection-page-repair-candidate",
                    [
                        {
                            "kind": "inference",
                            "content": complete_content,
                            "source_ids": [1],
                        }
                    ],
                    profile="research",
                ),
                _reflection_output_call(
                    "reflection-page-repair-request",
                    verdict="revise",
                    summary="数值与页码准确，仅一处口径说明页码不够精确。",
                    issues=[
                        {
                            "block_index": 1,
                            "category": "evidence_scope",
                            "severity": "low",
                            "reason": "第55页为合并表，第56页才是母公司现金流量表。",
                            "repair_instruction": (
                                "将口径说明中的“第55/56页现金流量表”改为“第56页母公司现金流量表”，"
                                "保留 271,351,507.32 元及母公司口径说明。"
                            ),
                        }
                    ],
                ),
                _reflection_output_call("reflection-page-repair-pass"),
            ]
        )

        result, _executor = await _run(
            model=model,
            conversation_id="reflection-exact-page-repair",
            response_format=DEFAULT_RESPONSE_FORMAT,
            user_text="核对新强联半年报经营现金流数据及页码",
        )

        expected_content = complete_content.replace(
            "第55/56页现金流量表", "第56页母公司现金流量表"
        )
        assert result.status == "completed"
        assert result.state["reflection_status"] == "passed"
        assert result.state["reflection_revision_count"] == 1
        assert len(model.calls) == 4
        assert result.state["structured_answer"]["blocks"][0]["content"] == expected_content
        assert "2,073,339,945.39 元" in result.final_text
        assert "411,334,427.09 元" in result.final_text
        assert "363,763,010.34 元" in result.final_text
        assert "第56页母公司现金流量表" in result.final_text
        assert "第55/56页现金流量表" not in result.final_text
        assert "271,351,507.32 元，属母公司口径、未采用" in result.final_text

    asyncio.run(scenario())


def test_reflection_block_publishes_partial_and_does_not_claim_completion() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(
            responses=[
                _tool_call("reflection-block-read", "primary"),
                _structured_output_call(
                    "reflection-block-candidate",
                    [
                        {
                            "section": "综合判断",
                            "kind": "inference",
                            "content": "这条资料足以证明未来一定上涨。",
                            "source_ids": [1],
                        }
                    ],
                    profile="research",
                ),
                _reflection_output_call(
                    "reflection-block",
                    verdict="block",
                    summary="关键结论超出现有证据支持范围。",
                ),
            ]
        )
        result, _executor = await _run(
            model=model,
            conversation_id="research-reflection-block",
            response_format=DEFAULT_RESPONSE_FORMAT,
        )

        assert result.status == "partial"
        assert result.error_code == "reflection_blocked"
        assert result.state["reflection_status"] == "blocked"
        assert "关键结论超出现有证据支持范围" in result.final_text
        assert any(
            item["stage"] == "reflection" and item["status"] == "blocked"
            for item in result.stage_history or []
        )

    asyncio.run(scenario())


def test_native_structured_answer_repairs_a_material_block_that_lacks_evidence() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(
            responses=[
                _tool_call("structured-read-missing", "primary"),
                _structured_output_call(
                    "structured-missing-final",
                    [
                        {
                            "section": "财务数据",
                            "kind": "fact",
                            "content": "营收增长放缓。",
                            "source_ids": [],
                        },
                        {
                            "section": "风险",
                            "kind": "risk",
                            "content": "结论仍需结合后续数据观察。",
                            "source_ids": [1],
                        },
                    ],
                ),
                _structured_output_call(
                    "structured-repaired-final",
                    [
                        {
                            "section": "财务数据",
                            "kind": "fact",
                            "content": "营收增长放缓。",
                            "source_ids": [1],
                        },
                        {
                            "section": "风险",
                            "kind": "risk",
                            "content": "结论仍需结合后续数据观察。",
                            "source_ids": [1],
                        },
                    ],
                ),
                _reflection_output_call("structured-missing-reflection-pass"),
            ]
        )
        result, _executor = await _run(
            model=model,
            conversation_id="structured-answer-missing-evidence",
            response_format=DEFAULT_RESPONSE_FORMAT,
        )

        assert result.status == "completed"
        assert result.error_code is None
        assert "营收增长放缓" in result.final_text
        assert result.state["claim_evidence"][0]["checks"]["tool_success"] is True
        assert result.state["evidence_repair_count"] == 1
        assert len(model.calls) == 4

    asyncio.run(scenario())


def test_structured_repair_targets_mixed_valid_and_invalid_references() -> None:
    async def scenario() -> None:
        def blocks(ids):
            return [
                {"section": "风险", "kind": "risk", "content": "后续仍需观察。", "source_ids": ids},
                {"section": "声明", "kind": "disclaimer", "content": "截至2026-09-04，仅供研究参考。", "source_ids": []},
            ]
        model = ScriptedChatModel(responses=[
            _tool_call("mixed-read", "primary"),
            _structured_output_call("mixed-invalid", blocks([1, 99])),
            _structured_output_call("mixed-fixed", blocks([1])),
            _reflection_output_call("mixed-reflection-pass"),
        ])
        result, _ = await _run(model=model, conversation_id="mixed-references", response_format=DEFAULT_RESPONSE_FORMAT)
        assert result.status == "completed"
        assert result.state["evidence_repair_count"] == 1
        feedback = "\n".join(str(message.content) for message in model.calls[2])
        assert '"unresolved_evidence_ids": ["source:99"]' in feedback
        assert '"reference_integrity": false' in feedback
        assert result.state["claim_evidence"][1]["issues"] == []
    asyncio.run(scenario())


def test_exhausted_pdf_page_citation_repair_does_not_publish_mismatched_answer() -> None:
    async def scenario() -> None:
        result_item = {
            "evidence_id": "ev_kb_page_340",
            "page_start": 340,
            "page_end": 340,
            "filename": "Agentic_Design_Patterns_Complete.pdf",
            "snippet": "Weaknesses, Originality, Quality, Clarity, and Significance.",
            "url": "/api/v1/knowledge-bases/documents/doc-1/content#page=340",
        }
        search_outcome = {
            "success": True,
            "data_time": None,
            "source_refs": [result_item["url"]],
            "result": {"success": True, "results": [result_item], "result_items": []},
        }
        invalid_answer = [
            {
                "section": "Level 0 的主要局限",
                "kind": "fact",
                "content": "根据 PDF 第 14 页，Level 0 的局限是缺乏 current-event awareness。",
                "source_ids": [1],
            }
        ]
        model = ScriptedChatModel(responses=[
            _named_tool_call("rag-search-1", "search_knowledge_base", {"query": "Level 0 current events"}),
            _structured_output_call("rag-answer-1", invalid_answer, profile="general"),
            _structured_output_call("rag-answer-2", invalid_answer, profile="general"),
            _structured_output_call("rag-answer-3", invalid_answer, profile="general"),
        ])
        result, executor = await _run(
            model=model,
            registry=_registry(SEARCH_KNOWLEDGE_BASE_TOOL),
            executor=FakeAtomicExecutor({"search_knowledge_base": [search_outcome] * 2}),
            conversation_id="rag-page-citation-fail-closed",
            response_format=DEFAULT_RESPONSE_FORMAT,
            knowledge_base_ids=("kb-test",),
        )

        assert result.status == "partial"
        assert result.error_code == "evidence_link_incomplete"
        assert "未经核实的结论" in result.final_text
        assert "Level 0 的局限" not in result.final_text
        assert "#page=340" not in result.final_text
        final_blocks = result.state["structured_answer"]["blocks"]
        assert len(final_blocks) == 1
        assert final_blocks[0]["kind"] == "disclaimer"
        assert final_blocks[0]["evidence_ids"] == []
        assert sum(call["tool_name"] == "search_knowledge_base" for call in executor.calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("presentation_type", ["markdown", "table"])
def test_exhausted_pdf_repair_keeps_verified_block_and_omits_unverified_history(
    monkeypatch: pytest.MonkeyPatch, presentation_type: str,
) -> None:
    monkeypatch.setenv("AGENT_EVIDENCE_REPAIR_LIMIT", "1")

    async def scenario() -> None:
        hit = {
            "evidence_id": "ev_kb_cover_page",
            "page_start": 1,
            "page_end": 1,
            "filename": "新强联2026年半年度报告.pdf",
            "snippet": "公司全称：洛阳新强联回转支承股份有限公司。本报告期为2026年半年度。",
            "url": "/api/v1/knowledge-bases/documents/doc-1/content#page=1",
        }
        search_outcome = {
            "success": True,
            "data_time": None,
            "source_refs": [hit["url"]],
            "result": {"success": True, "results": [hit], "result_items": []},
        }
        mixed_blocks = [
            {
                "section": "报告封面",
                "kind": "fact",
                "content": "公司全称为洛阳新强联回转支承股份有限公司，见 PDF 第 1 页。",
                "source_ids": [1],
            },
            {
                "section": "补充说明",
                "kind": "fact",
                "content": "光合作用是植物将光能转化为化学能的过程。",
                "source_ids": [],
            },
        ]
        if presentation_type == "table":
            mixed_blocks[0].update({
                "presentation_type": "table",
                "content": "| 指标 | 内容 |\n| --- | --- |\n| 公司全称 | 洛阳新强联回转支承股份有限公司，PDF 第 1 页 |",
            })
        model = ScriptedChatModel(responses=[
            _named_tool_call("pdf-search", "search_knowledge_base", {"query": "报告封面 公司全称"}),
            _structured_output_call("mixed-pdf-answer", mixed_blocks, profile="general", title="报告分析"),
            # A regressed repair must not erase a previously verified block.
            _structured_output_call("mixed-pdf-answer-repair", [mixed_blocks[1]], profile="general"),
        ])
        result, executor = await _run(
            model=model,
            registry=_registry(SEARCH_KNOWLEDGE_BASE_TOOL),
            executor=FakeAtomicExecutor({"search_knowledge_base": [search_outcome]}),
            conversation_id="rag-retain-verified-block",
            response_format=DEFAULT_RESPONSE_FORMAT,
            knowledge_base_ids=("kb-test",),
            user_text="根据所选 PDF，报告封面上的公司全称是什么？请给出页码。",
        )
        assert result.status == "partial"
        assert result.error_code == "evidence_link_incomplete"
        assert "洛阳新强联回转支承股份有限公司" in result.final_text
        assert "PDF 第 1 页" in result.final_text
        assert "【证据 ev_kb_cover_page】" in result.final_text
        assert "光合作用" not in result.final_text
        assert "未通过校验的回答区块已省略" in result.final_text
        final_blocks = result.state["structured_answer"]["blocks"]
        assert result.state["structured_answer"]["title"] == "报告分析"
        assert [block["kind"] for block in final_blocks] == ["fact", "disclaimer"]
        final_claims = result.state["claim_evidence"]
        assert all("光合作用" not in claim["text"] for claim in final_claims)
        verified_claim = next(claim for claim in final_claims if "公司全称" in claim["text"])
        assert all(value is True for value in verified_claim["checks"].values())
        assert sum(call["tool_name"] == "search_knowledge_base" for call in executor.calls) == 1

    asyncio.run(scenario())


def test_pdf_absence_disclaimer_drops_unrelated_lexical_hit_citation() -> None:
    async def scenario() -> None:
        unrelated_hit = {
            "evidence_id": "ev_kb_shanghai_subsidiary",
            "page_start": 153,
            "page_end": 153,
            "filename": "新强联2026年半年度报告.pdf",
            "snippet": "新强联（上海）装备有限公司；子公司投资情况。",
            "url": "/api/v1/knowledge-bases/documents/doc-1/content#page=153",
        }
        search_outcome = {
            "success": True,
            "data_time": None,
            "source_refs": [unrelated_hit["url"]],
            "result": {
                "success": True,
                "results": [unrelated_hit],
                "result_items": [],
                "no_evidence": False,
            },
        }
        query_outcome = {
            "success": True,
            "data_time": None,
            "source_refs": [],
            "result": {
                "success": True,
                "results": [],
                "result_items": [],
                "no_evidence": True,
            },
        }
        model = ScriptedChatModel(responses=[
            _named_tool_call("pdf-absence-retry", "search_knowledge_base", {"query": "上海天气"}),
            _structured_output_call(
                "pdf-absence-disclaimer",
                [{
                    "section": "检索结果",
                    "kind": "disclaimer",
                    "content": "本轮检索未找到 PDF 提及上海天气的依据。",
                    "source_ids": [1],
                }],
                profile="general",
            ),
            _reflection_output_call("pdf-absence-reflection-pass"),
        ])
        query = "文档中是否提及今天上海的天气？只依据已选 PDF；如果没有明确说明，请直接说报告未提及，不要使用其他工具。"
        result, executor = await _run(
            model=model,
            registry=_registry(SEARCH_KNOWLEDGE_BASE_TOOL, _search_operation(), _web_search_operation()),
            executor=FakeAtomicExecutor({"search_knowledge_base": [search_outcome, query_outcome]}),
            conversation_id="pdf-absence-disclaimer-no-unrelated-citation",
            response_format=DEFAULT_RESPONSE_FORMAT,
            knowledge_base_ids=("kb-test",),
            user_text=query,
        )

        assert result.status == "completed", (
            result.error_code,
            result.final_text,
            result.state.get("terminal_detail"),
            result.state.get("evidence_feedback"),
        )
        assert result.final_text == "本轮检索未找到 PDF 提及该内容的依据。"
        assert "ev_kb_shanghai_subsidiary" not in result.final_text
        assert "#page=153" not in result.final_text
        assert "子公司" not in result.final_text
        final_block = result.state["structured_answer"]["blocks"][0]
        assert final_block["source_ids"] == []
        assert final_block["evidence_ids"] == []
        assert [call["tool_name"] for call in executor.calls] == ["search_knowledge_base"]
        assert all(
            {tool.name for tool in call["tools"]}.issubset({"search_knowledge_base", STRUCTURED_OUTPUT_TOOL_NAME})
            for call in model.call_options
        )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("user_text", "expected"),
    [
        ("只依据所选 PDF 第 7 页，列出营业收入并引用页码。", True),
        ("所选 PDF 中是否提及今天上海的天气？只依据该 PDF。", True),
        ("根据所选 PDF 第 7 页的营业收入，与今天股价进行比较。", False),
        ("根据 PDF 数据，结合当前市场估值分析。", False),
    ],
)
def test_only_pdf_scope_requires_an_explicit_exclusive_instruction(
    user_text: str,
    expected: bool,
) -> None:
    assert _user_requests_knowledge_base_only(user_text) is expected


@pytest.mark.parametrize(
    "user_text",
    [
        "根据所选 PDF 第 7 页，列出营业收入并引用页码。",
        "所选 PDF 中是否提及今天上海的天气？",
        "根据 PDF 数据，结合当前市场估值分析。",
    ],
)
def test_document_grounding_does_not_imply_pdf_only_source_scope(user_text: str) -> None:
    assert _user_requests_knowledge_base_only(user_text) is False


def test_pdf_answer_rebinds_exact_quote_to_matching_hit_from_this_run() -> None:
    async def scenario() -> None:
        page_14 = {
            "evidence_id": "ev_kb_page_14",
            "page_start": 14,
            "page_end": 14,
            "filename": "Agentic_Design_Patterns_Complete.pdf",
            "snippet": (
                "In a 'Level 0' configuration, the LLM operates without tools, memory, or "
                "environment interaction, responding solely based on its pretrained knowledge. "
                "The trade-off for this powerful internal reasoning is a complete lack of "
                "current-event awareness."
            ),
            "url": "/api/v1/knowledge-bases/documents/doc-1/content#page=14",
        }

        def search_result(hit: Mapping[str, Any]) -> dict[str, Any]:
            return {
                "success": True,
                "data_time": None,
                "source_refs": [str(hit["url"])],
                "result": {"success": True, "results": [dict(hit)], "result_items": []},
            }

        model = ScriptedChatModel(responses=[
            _structured_output_call(
                "rag-answer-wrong-page",
                [{
                    "section": "Level 0 的主要局限",
                    "kind": "fact",
                    "content": (
                        "Level 0 缺乏对当前事件的感知（a complete lack of current-event awareness），"
                        "原文见第 14 页。"
                    ),
                    "source_ids": [1],
                }],
                profile="general",
            ),
            _named_tool_call("rag-search-right", "search_knowledge_base", {"query": "Level 0 current-event awareness"}),
            _structured_output_call(
                "rag-answer-exact-quote",
                [{
                    "section": "Level 0 的主要局限",
                    "kind": "fact",
                    "content": (
                        "Level 0 缺乏对当前事件的感知（a complete lack of current-event awareness），"
                        "原文见第 14 页。"
                    ),
                    "source_ids": [1],
                }],
                profile="general",
            ),
            _reflection_output_call("rag-answer-reflection-pass"),
        ])
        result, executor = await _run(
            model=model,
            registry=_registry(SEARCH_KNOWLEDGE_BASE_TOOL),
            executor=FakeAtomicExecutor({
                "search_knowledge_base": [search_result(page_14)],
            }),
            conversation_id="rag-exact-quote-rebind",
            response_format=DEFAULT_RESPONSE_FORMAT,
            knowledge_base_ids=("kb-test",),
        )

        assert result.status == "completed", (
            result.error_code,
            result.final_text,
            result.state.get("terminal_detail"),
            result.state.get("evidence_feedback"),
        )
        assert "#page=14" not in result.final_text
        assert "【证据 ev_kb_page_14】" in result.final_text
        assert result.state["claim_evidence"][0]["evidence"][0]["source_refs"] == [page_14["url"]]
        assert sum(call["tool_name"] == "search_knowledge_base" for call in executor.calls) == 1

    asyncio.run(scenario())


def test_tool_strategy_retries_schema_errors_without_reusing_a_previous_structured_response() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(
            responses=[
                _tool_call("structured-validation-read", "primary"),
                _structured_output_call("invalid-structured", [], title="无效"),
                _structured_output_call(
                    "valid-structured",
                    [
                        {
                            "section": "结论",
                            "kind": "fact",
                            "content": "来源已返回可用材料。",
                            "source_ids": [1],
                        }
                    ],
                ),
            ]
        )
        result, _executor = await _run(
            model=model,
            conversation_id="structured-answer-schema-retry",
            response_format=DEFAULT_RESPONSE_FORMAT,
        )

        assert result.status == "completed"
        assert len(model.calls) == 3
        assert result.final_text.endswith("【证据 ev_structured-validation-read】")
        assert result.state["structured_answer_call_id"] == "valid-structured"

    asyncio.run(scenario())


def test_publication_contract_repairs_header_only_research_table_once() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(
            responses=[
                _tool_call("table-validation-read", "primary"),
                _structured_output_call(
                    "header-only-table",
                    [{
                        "kind": "fact",
                        "presentation_type": "markdown",
                        "content": "| 指标 | 数值 | 页码 |",
                        "source_ids": [1],
                    }],
                ),
                _structured_output_call(
                    "complete-table",
                    [{
                        "kind": "fact",
                        "presentation_type": "markdown",
                        "content": "| 指标 | 数值 | 页码 |\n|---|---|---|\n| 营业收入 | 100 元 | 第 7 页 |",
                        "source_ids": [1],
                    }],
                ),
            ]
        )
        result, _executor = await _run(
            model=model,
            conversation_id="structured-answer-table-publication-retry",
            response_format=DEFAULT_RESPONSE_FORMAT,
            user_text="最终只输出一个完整 Markdown 表格。",
        )

        assert result.status == "completed"
        assert len(model.calls) == 3
        repair_context = "\n".join(
            str(getattr(message, "content", "")) for message in model.calls[-1]
        )
        assert "不完整的 Markdown 表格" in repair_context
        assert "填写表格数据行" in repair_context
        assert "已核验步骤中的金额" in repair_context
        assert "营业收入 | 100 元 | 第 7 页" in result.final_text
        assert result.final_text.endswith("【证据 ev_table-validation-read】")

    asyncio.run(scenario())


def test_structured_answer_uses_the_existing_scoped_content_access_gate() -> None:
    async def scenario() -> None:
        url = "https://example.test/structured-news"
        model = ScriptedChatModel(
            responses=[
                _named_tool_call(
                    "structured-reference-news",
                    "read_company_news_akshare",
                    {"symbol": "600519"},
                ),
                _structured_output_call(
                    "structured-reference-candidate",
                    [
                        {
                            "section": "新闻结论",
                            "kind": "fact",
                            "content": "索引显示存在相关消息。",
                            "source_ids": [1],
                        }
                    ],
                ),
                _named_tool_call(
                    "structured-reference-body",
                    "read_web_source",
                    {"source_id": "http", "url": url},
                ),
                _structured_output_call(
                    "structured-reference-final",
                    [
                        {
                            "section": "新闻结论",
                            "kind": "fact",
                            "content": "正文已确认该消息。",
                            "source_ids": [2],
                        }
                    ],
                ),
            ]
        )
        executor = FakeAtomicExecutor(
            {
                "read_company_news_akshare": [
                    {
                        "source_refs": [url],
                        "result": {
                            "items": [{"title": "测试新闻", "url": url}],
                            "reference_links": [url],
                            "content_access": {
                                "mode": "reference_only",
                                "content_read": False,
                                "content_extracted": False,
                                "content_read_required": True,
                            },
                        },
                    }
                ],
                "read_web_source": [
                    {
                        "source_refs": [url],
                        "result": {"url": url, "content": "测试新闻正文。"},
                    }
                ],
            }
        )
        result, _executor = await _run(
            model=model,
            registry=_registry(_company_news_operation(), _web_source_operation()),
            executor=executor,
            conversation_id="structured-answer-content-access",
            response_format=DEFAULT_RESPONSE_FORMAT,
        )

        assert result.status == "completed"
        assert [call["tool_name"] for call in executor.calls] == [
            "read_company_news_akshare",
            "read_web_source",
        ]
        assert result.state["content_access_repair_count"] == 1
        assert result.final_text.endswith("【证据 ev_structured-reference-body】")
        assert result.state["structured_answer_call_id"] == "structured-reference-final"
        assert len(model.calls) == 4
        content_repair_request = model.call_options[2]
        assert content_repair_request["tool_choice"] == "required"
        assert {tool.name for tool in content_repair_request["tools"]} == {"read_web_source"}
        final_request = model.call_options[3]
        assert final_request["tool_choice"] == "any"
        assert {tool.name for tool in final_request["tools"]} == {
            "read_company_news_akshare",
            "read_web_source",
            STRUCTURED_OUTPUT_TOOL_NAME,
        }

    asyncio.run(scenario())


def test_reference_sources_are_selected_before_answer_and_read_by_the_server() -> None:
    async def scenario() -> None:
        url = "https://example.test/proactive-news"
        model = ScriptedChatModel(
            responses=[
                _named_tool_call(
                    "proactive-reference-news",
                    "read_company_news_akshare",
                    {"symbol": "600519"},
                ),
                _named_tool_call(
                    "proactive-selection",
                    "select_content_sources",
                    {"source_ids": [1]},
                ),
                _structured_output_call(
                    "proactive-final",
                    [
                        {
                            "section": "新闻结论",
                            "kind": "fact",
                            "content": "正文已确认该消息。",
                            "source_ids": [2],
                        }
                    ],
                    profile="research",
                ),
            ]
        )
        executor = FakeAtomicExecutor(
            {
                "read_company_news_akshare": [
                    {
                        "source_refs": [url],
                        "result": {
                            "success": True,
                            "items": [{"title": "测试新闻", "url": url}],
                            "reference_links": [url],
                            "content_access": {
                                "mode": "reference_only",
                                "content_read": False,
                                "content_extracted": False,
                                "content_read_required": True,
                            },
                        },
                    }
                ],
                "read_web_source": [
                    {
                        "source_refs": [url],
                        "result": {
                            "success": True,
                            "url": url,
                            "content": "新闻正文已读取。",
                        },
                    }
                ],
            }
        )
        result, _executor = await _run(
            model=model,
            registry=_registry(
                _company_news_operation(),
                _web_source_operation(),
                _content_selection_operation(),
            ),
            executor=executor,
            conversation_id="proactive-content-selection",
            response_format=DEFAULT_RESPONSE_FORMAT,
        )

        assert result.status == "completed"
        assert [call["tool_name"] for call in executor.calls] == [
            "read_company_news_akshare",
            "select_content_sources",
            "read_web_source",
        ]
        assert result.state["content_access_repair_count"] == 0
        assert result.state["pending_content_reads"] == []
        assert len(model.calls) == 3
        selection_request = model.call_options[1]
        assert selection_request["tool_choice"] == "required"
        assert {tool.name for tool in selection_request["tools"]} == {
            "select_content_sources"
        }
        selection_message = next(
            message
            for message in model.calls[2]
            if getattr(message, "type", "") == "tool"
            and getattr(message, "name", "") == "select_content_sources"
        )
        assert "新闻正文已读取" in str(selection_message.content)
        assert any(
            item["stage"] == "content_access"
            and item["status"] == "started"
            and item["details"].get("mode") == "proactive_selection"
            for item in result.stage_history or []
        )

    asyncio.run(scenario())


def test_structured_output_plain_repair_is_bounded_and_publishes_one_partial_answer() -> None:
    async def scenario() -> None:
        candidate = "这是模型返回的普通文本候选。"
        model = ScriptedChatModel(
            responses=[
                AIMessage(content=candidate),
                AIMessage(content=candidate),
            ]
        )
        result, _executor = await _run(
            model=model,
            registry=_registry(),
            conversation_id="structured-output-plain-repair-bound",
            response_format=DEFAULT_RESPONSE_FORMAT,
        )

        assert result.status == "partial"
        assert result.error_code == "structured_output_incomplete"
        assert result.state["response_repair_count"] == 1
        assert len(model.calls) == 2
        assert result.final_text.count(candidate) == 1
        assert any(
            item["stage"] == "response_format" and item["status"] == "failed"
            for item in result.stage_history or []
        )

    asyncio.run(scenario())


def test_structured_output_reports_provider_token_limit_instead_of_plain_text() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(
            responses=[
                AIMessage(content="", response_metadata={"finish_reason": "length"}),
                AIMessage(content="", response_metadata={"finish_reason": "length"}),
            ]
        )
        result, _executor = await _run(
            model=model,
            registry=_registry(),
            conversation_id="structured-output-length-repair-bound",
            response_format=DEFAULT_RESPONSE_FORMAT,
        )

        assert result.status == "partial"
        assert result.error_code == "structured_output_incomplete"
        failed = [
            item for item in result.stage_history or []
            if item["stage"] == "response_format" and item["status"] == "failed"
        ]
        assert failed
        assert "max_tokens" in failed[-1]["details"]["reason"]
        assert "普通文本" not in failed[-1]["details"]["reason"]

    asyncio.run(scenario())


def test_invalid_structured_output_repair_is_bounded() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(
            responses=[
                _structured_output_call("invalid-structured-1", []),
                _structured_output_call("invalid-structured-2", []),
            ]
        )
        result, _executor = await _run(
            model=model,
            registry=_registry(),
            conversation_id="structured-output-invalid-repair-bound",
            response_format=DEFAULT_RESPONSE_FORMAT,
        )

        assert result.status == "partial"
        assert result.error_code == "structured_output_incomplete"
        assert result.state["response_repair_count"] == 1
        assert len(model.calls) == 2

    asyncio.run(scenario())


def test_content_repair_keeps_the_structured_candidate_when_model_returns_plain_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_CONTENT_ACCESS_REPAIR_LIMIT", "1")

    async def scenario() -> None:
        first_url = "https://example.test/news/1"
        second_url = "https://example.test/news/2"
        model = ScriptedChatModel(
            responses=[
                _named_tool_call(
                    "reference-news",
                    "read_company_news_akshare",
                    {"symbol": "600519"},
                ),
                _structured_output_call(
                    "reference-candidate",
                    [
                        {
                            "section": "新闻结论",
                            "kind": "fact",
                            "content": "索引显示存在相关消息。",
                            "source_ids": [1],
                        }
                    ],
                ),
                AIMessage(content="索引显示存在相关消息。【证据 ev_reference-news】"),
            ]
        )
        executor = FakeAtomicExecutor(
            {
                "read_company_news_akshare": [
                    {
                        "source_refs": [first_url, second_url],
                        "result": {
                            "items": [
                                {"title": "测试新闻 1", "url": first_url},
                                {"title": "测试新闻 2", "url": second_url},
                            ],
                            "reference_links": [first_url, second_url],
                            "content_access": {
                                "mode": "reference_only",
                                "content_read": False,
                                "content_extracted": False,
                                "content_read_required": True,
                            },
                        },
                    }
                ]
            }
        )
        result, _executor = await _run(
            model=model,
            registry=_registry(_company_news_operation(), _web_source_operation()),
            executor=executor,
            conversation_id="structured-output-content-repair-plain",
            response_format=DEFAULT_RESPONSE_FORMAT,
        )

        assert result.status == "partial"
        assert result.error_code == "content_access_incomplete"
        assert len(model.calls) == 3
        assert result.state["structured_answer_call_id"] == "reference-candidate"
        assert result.final_text.count("索引显示存在相关消息。") == 1
        assert "正文取证未完成" in result.final_text
        content_repair_request = model.call_options[2]
        assert content_repair_request["tool_choice"] == "required"
        assert {tool.name for tool in content_repair_request["tools"]} == {"read_web_source"}

    asyncio.run(scenario())


def test_structured_response_channel_cannot_end_a_later_turn_with_plain_text() -> None:
    async def scenario() -> None:
        conversation_id = "structured-answer-cross-turn"
        manager = LangGraphRuntimeManager(registry=_registry(_search_operation()))
        await manager.start(testing=True)
        try:
            first = await manager.run_new(
                messages=[{"role": "user", "content": "第一轮"}],
                user_text="第一轮",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="structured-cross-turn-1",
                conversation_id=conversation_id,
                # This script tests the Direct answer contract, not Auto's
                # separate OrchestratorRoute model call.
                agent_mode="direct",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=ScriptedChatModel(
                    responses=[
                        _tool_call("structured-cross-read", "primary"),
                        _structured_output_call(
                            "structured-cross-first",
                            [
                                {
                                    "kind": "fact",
                                    "content": "第一轮结果。",
                                    "source_ids": [1],
                                }
                            ],
                        ),
                    ]
                ),
                executor=FakeAtomicExecutor(),
            )
            assert first.status == "completed"

            second_model = ScriptedChatModel(
                responses=[
                    AIMessage(content="第二轮候选文本。"),
                    _structured_output_call(
                        "structured-cross-second",
                        [
                            {
                                "kind": "context",
                                "content": "第二轮结构化结果。",
                                "source_ids": [],
                            }
                        ],
                    ),
                ]
            )
            second = await manager.run_new(
                messages=[{"role": "user", "content": "第二轮"}],
                user_text="第二轮",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="structured-cross-turn-2",
                conversation_id=conversation_id,
                agent_mode="direct",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=second_model,
                executor=FakeAtomicExecutor(),
            )

            assert second.status == "completed"
            assert second.final_text == "第二轮结构化结果。"
            assert len(second_model.calls) == 2
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_stale_structured_response_does_not_end_a_turn_after_a_rejected_tool_call() -> None:
    async def scenario() -> None:
        conversation_id = "structured-response-rejected-tool-cross-turn"
        manager = LangGraphRuntimeManager(
            registry=_registry(_search_operation()),
            response_format=DEFAULT_RESPONSE_FORMAT,
        )
        await manager.start(testing=True)
        try:
            first = await manager.run_new(
                messages=[{"role": "user", "content": "第一轮"}],
                user_text="第一轮",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="structured-rejected-tool-cross-turn-1",
                conversation_id=conversation_id,
                agent_mode="direct",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=ScriptedChatModel(responses=[
                    _tool_call("first-turn-read", "primary"),
                    _structured_output_call(
                        "first-turn-answer",
                        [{"kind": "fact", "content": "第一轮结果。", "source_ids": [1]}],
                    ),
                ]),
                executor=FakeAtomicExecutor(),
            )
            assert first.status == "completed"

            second_model = ScriptedChatModel(responses=[
                _named_tool_call("rejected-unknown-operation", "not_in_registry", {}),
                _structured_output_call(
                    "second-turn-answer",
                    [{"kind": "context", "content": "第二轮已重新回答。", "source_ids": []}],
                ),
            ])
            second = await manager.run_new(
                messages=[{"role": "user", "content": "第二轮"}],
                user_text="第二轮",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="structured-rejected-tool-cross-turn-2",
                conversation_id=conversation_id,
                agent_mode="direct",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=second_model,
                executor=FakeAtomicExecutor(),
            )

            assert second.status == "completed", (
                second.error_code,
                second.final_text,
                second.state.get("terminal_detail"),
                second.state.get("structured_response"),
                len(second_model.calls),
            )
            assert second.final_text == "第二轮已重新回答。"
            assert len(second_model.calls) == 2
            assert any(
                isinstance(message, ToolMessage)
                and message.tool_call_id == "rejected-unknown-operation"
                for message in second_model.calls[-1]
            )
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_partial_provider_status_is_not_promoted_to_an_assistant_answer() -> None:
    class Graph:
        def __init__(self) -> None:
            self.update: dict[str, Any] | None = None

        async def aget_state(self, _config: Mapping[str, Any]) -> Any:
            return SimpleNamespace(values={"answer_final": "", "answer_draft": ""})

        async def aupdate_state(self, _config: Mapping[str, Any], update: Mapping[str, Any]) -> None:
            self.update = dict(update)

    class Events:
        def __init__(self) -> None:
            self.stages: list[tuple[str, ...]] = []
            self.texts: list[str] = []

        def close_open_stages(self, **_kwargs: Any) -> None:
            return None

        def stage(self, *args: Any, **_kwargs: Any) -> None:
            self.stages.append(tuple(str(arg) for arg in args))

        def text(self, value: str) -> None:
            self.texts.append(value)

    async def scenario() -> None:
        graph = Graph()
        events = Events()
        manager = LangGraphRuntimeManager(registry=_registry())
        result = await manager._terminate_partial(
            graph,
            config={},
            context=SimpleNamespace(events=events),
            error_code="model_provider_timeout",
            message="上游模型服务返回超时；已保留已有工具观察和证据。",
        )

        assert result["answer_final"] == ""
        assert graph.update == {
            "answer_final": "",
            "status": "partial",
            "error_code": "model_provider_timeout",
            "terminal_detail": "上游模型服务返回超时；已保留已有工具观察和证据。",
        }
        assert events.texts == []
        assert events.stages[-1] == ("publish", "failed", "上游模型服务返回超时；已保留已有工具观察和证据。")

    asyncio.run(scenario())


def test_provider_timeout_does_not_publish_unaccepted_header_only_table() -> None:
    class Graph:
        def __init__(self) -> None:
            self.update: dict[str, Any] | None = None

        async def aget_state(self, _config: Mapping[str, Any]) -> Any:
            return SimpleNamespace(
                values={
                    "answer_final": "",
                    "answer_draft": "| 指标 | 数值 | 页码 |",
                    "user_text": "最终只输出一个完整 Markdown 表格，不要只给表头。",
                    "structured_answer": {
                        "profile": "research",
                        "blocks": [
                            {
                                "kind": "fact",
                                "presentation_type": "table",
                                "content": "| 指标 | 数值 | 页码 |",
                                "source_ids": [1],
                            }
                        ],
                    },
                    "evidence": [],
                    "tool_results": [],
                }
            )

        async def aupdate_state(
            self,
            _config: Mapping[str, Any],
            update: Mapping[str, Any],
        ) -> None:
            self.update = dict(update)

    class Events:
        def __init__(self) -> None:
            self.texts: list[str] = []

        def close_open_stages(self, **_kwargs: Any) -> None:
            return None

        def stage(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def text(self, value: str) -> None:
            self.texts.append(value)

    async def scenario() -> None:
        graph = Graph()
        events = Events()
        manager = LangGraphRuntimeManager(registry=_registry())
        result = await manager._terminate_partial(
            graph,
            config={},
            context=SimpleNamespace(events=events),
            error_code="model_provider_timeout",
            message="上游模型服务返回超时；已保留已有工具观察和证据。",
        )

        assert result["status"] == "partial"
        assert "| 指标 | 数值 | 页码 |" not in result["answer_final"]
        assert "未发布不完整内容" in result["answer_final"]
        assert graph.update is not None
        assert graph.update["structured_answer"] is None
        assert graph.update["answer_draft"] == result["answer_final"]
        assert events.texts == [result["answer_final"]]

    asyncio.run(scenario())


def test_model_stage_reports_prompt_footprint_without_imposing_a_new_limit() -> None:
    async def scenario() -> None:
        registry = _registry(_search_operation())
        model = ScriptedChatModel(responses=[
            _tool_call("prompt-source", "primary"),
            AIMessage(content="读取完成【证据 ev_prompt-source】"),
        ])
        result, _executor = await _run(
            model=model,
            registry=registry,
            conversation_id="prompt-footprint",
        )

        model_started = [
            item
            for item in result.stage_history or []
            if item["stage"] == "model" and item["status"] == "started"
        ]
        assert len(model_started) == len(model.calls) == 2
        catalog_text = ToolCatalog(registry).model_context()
        for stage, messages in zip(model_started, model.calls, strict=True):
            system_text = str(next(message.content for message in messages if message.type == "system"))
            assert system_text.count(catalog_text) == 1
            details = stage["details"]
            assert details["operation_count"] == details["bound_tool_count"] == 1
            assert details["directory_character_count"] == len(catalog_text)
            assert details["system_prompt_character_count"] == len(system_text)
        assert result.status == "completed"

    asyncio.run(scenario())


def test_native_rss_validation_preserves_error_feedback_and_allows_corrected_call() -> None:
    from src.tools.source_operations import TOOLS

    async def scenario() -> None:
        operation = next(tool for tool in TOOLS if tool.name == "read_rss_source")
        model = ScriptedChatModel(responses=[
            _named_tool_call("rss-invalid", operation.name, {
                "source_id": "cls_subject", "source_params": {"subject": "未来产业"},
            }),
            _named_tool_call("rss-valid", operation.name, {
                "source_id": "cls_subject", "source_params": {"id": "101"},
            }),
            AIMessage(content="读取完成【证据 ev_rss-valid】"),
        ])
        result, executor = await _run(
            model=model, registry=_registry(operation), conversation_id="rss-validation",
        )
        assert [call["action_id"] for call in executor.calls] == ["rss-valid"]
        assert executor.calls[0]["arguments"]["source_params"] == {"id": "101"}
        failed = result.state["tool_results"][0]
        assert failed["success"] is False, result.state["tool_results"]
        assert failed["error_code"] == "invalid_arguments"
        assert "subject" in str(failed["errors"])
        assert "invalid JSON" not in str(failed["errors"])
        feedback = next(message for message in model.calls[1] if message.type == "tool")
        assert feedback.status == "error"
        assert "subject" in str(feedback.content)
        assert len(result.state["evidence"]) == 1
        assert result.state["evidence"][0]["action_id"] == "rss-valid"
        assert result.status == "completed"

    asyncio.run(scenario())


def test_successful_group_read_is_kept_as_context_for_the_next_model_turn() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(
            responses=[
                _tool_call("group-context", "primary"),
                AIMessage(content="我会继续围绕新能源分组回答【证据 ev_group-context】。"),
            ]
        )
        result, _executor = await _run(
            model=model,
            executor=FakeAtomicExecutor(
                {
                    "search_source": [
                        {
                            "result": {
                                "_agent_context": {
                                    "type": "stock_group",
                                    "group_id": "3",
                                    "group_name": "新能源",
                                    "member_count": 12,
                                    "source": "manual",
                                }
                            }
                        }
                    ]
                }
            ),
            conversation_id="group-context",
        )

        assert result.status == "completed"
        assert result.state["conversation_context"] == {
            "type": "stock_group",
            "group_id": "3",
            "group_name": "新能源",
            "member_count": 12,
            "source": "manual",
        }
        assert any("新能源" in str(message.content) for message in model.calls[1])

    asyncio.run(scenario())


@pytest.mark.parametrize("history_mode", ["auto", "continue", "replace", "branch", "reset"])
def test_group_context_follows_checkpoint_branch_ownership(history_mode) -> None:
    async def scenario() -> None:
        registry = _registry(_search_operation())
        executor = FakeAtomicExecutor(
            {
                "search_source": [
                    {
                        "result": {
                            "_agent_context": {
                                "type": "stock_group",
                                "group_id": "3",
                                "group_name": "新能源",
                                "member_count": 12,
                            }
                        }
                    }
                ]
            }
        )
        manager = LangGraphRuntimeManager(registry=registry, response_format=None)
        await manager.start(testing=True)
        try:
            first = await manager.run_new(
                messages=[{"role": "user", "content": "读取新能源分组"}],
                agent_mode="direct",
                user_text="读取新能源分组",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="run-group-context-1",
                conversation_id="group-context-persisted",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=ScriptedChatModel(
                    responses=[
                        _tool_call("group-context-persisted", "primary"),
                        AIMessage(content="已读取新能源分组【证据 ev_group-context-persisted】"),
                    ]
                ),
                executor=executor,
            )
            assert first.state["conversation_context"]["group_name"] == "新能源"

            second_model = ScriptedChatModel(
                responses=[AIMessage(content="继续围绕刚才的分组回答。")]
            )
            second = await manager.run_new(
                messages=[{"role": "user", "content": "继续刚才的分析"}],
                agent_mode="direct",
                user_text="继续刚才的分析",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="run-group-context-2",
                conversation_id="group-context-persisted",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=second_model,
                executor=executor,
                history_mode=history_mode,
            )
            if history_mode in {"auto", "continue"}:
                assert second.state["conversation_context"]["group_name"] == "新能源"
                assert any("新能源" in str(message.content) for message in second_model.calls[0])
                assert sum(str(message.content) == "读取新能源分组" for message in second_model.calls[0]) == 1
            else:
                assert second.state["conversation_context"] is None
                assert all("新能源" not in str(message.content) for message in second_model.calls[0])
                assert second.state["evidence"] == []
                assert second.state["tool_results"] == []
            assert sum("继续刚才的分析" in str(message.content) for message in second_model.calls[0]) == 1
        finally:
            await manager.close()

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


def test_native_tool_handler_reuses_the_application_executor_contract() -> None:
    async def scenario() -> None:
        evidence_id = (
            "ev_"
            + action_fingerprint(
                run_id="run-native-read-adapter",
                action_id="native-read",
                tool_name="search_source",
                arguments={"source_id": "primary", "query": "测试问题"},
            )[:20]
        )
        operation = replace(
            _search_operation(),
            executor=lambda **_kwargs: {
                "success": True,
                "url": "https://source.example/native",
                "data_time": "2026-08-08",
            },
        )
        manager = LangGraphRuntimeManager(
            registry=_registry(operation),
            response_format=None,
        )
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "测试问题"}],
                user_text="测试问题",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="run-native-read-adapter",
                conversation_id="native-read-adapter",
                agent_mode="direct",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=ScriptedChatModel(
                    responses=[
                        _tool_call("native-read", "primary"),
                        AIMessage(content=f"读取完成【证据 {evidence_id}】"),
                    ]
                ),
            )
            assert result.status == "completed"
            assert result.state["tool_results"][0]["success"] is True
            assert result.state["evidence"]
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_cancelled_tool_turn_is_removed_before_the_next_checkpoint_continuation() -> None:
    async def scenario() -> None:
        manager = LangGraphRuntimeManager(
            registry=_registry(_search_operation()),
            response_format=None,
        )
        executor = BlockingAtomicExecutor()
        await manager.start(testing=True)
        try:
            first_task = asyncio.create_task(
                manager.run_new(
                    messages=[{"id": "old-user", "role": "user", "content": "旧问题"}],
                    user_text="旧问题",
                    system_prompt="",
                    llm_config={},
                    database=None,
                    controller=None,
                    run_id="cancelled-tool-run",
                    agent_mode="direct",
                    conversation_id="cancelled-tool-continuation",
                    run_attempt=1,
                    tenant_id="tenant",
                    owner_id="owner",
                    model=ScriptedChatModel(
                        responses=[
                            _tool_call("pending-tool", "primary"),
                        ]
                    ),
                    executor=executor,
                )
            )
            await executor.started.wait()
            first_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first_task

            checkpoint = await manager.graph.aget_state(
                manager.graph_config("cancelled-tool-continuation")
            )
            assert any(
                isinstance(message, AIMessage)
                and any(call.get("id") == "pending-tool" for call in message.tool_calls)
                for message in checkpoint.values["messages"]
            )

            executor.block = False
            next_model = ScriptedChatModel(responses=[AIMessage(content="新问题已处理")])
            result = await manager.run_new(
                messages=[{"id": "new-user", "role": "user", "content": "新问题"}],
                user_text="新问题",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="continued-after-cancel",
                agent_mode="direct",
                conversation_id="cancelled-tool-continuation",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=next_model,
                executor=executor,
            )

            assert result.status == "completed"
            assert all(
                not (
                    isinstance(message, AIMessage)
                    and any(call.get("id") == "pending-tool" for call in message.tool_calls)
                )
                for message in next_model.calls[0]
            )
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_snapshot_branch_replaces_the_native_checkpoint_messages() -> None:
    async def scenario() -> None:
        manager = LangGraphRuntimeManager(registry=_registry(), response_format=None)
        await manager.start(testing=True)
        try:
            await manager.run_new(
                messages=[{"id": "old-user", "role": "user", "content": "旧问题"}],
                user_text="旧问题",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="before-snapshot-branch",
                agent_mode="direct",
                conversation_id="snapshot-branch",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=ScriptedChatModel(responses=[AIMessage(content="旧答案")]),
            )

            await manager.replace_checkpoint_messages(
                "snapshot-branch",
                [{"id": "new-user", "role": "user", "content": "保留的问题"}],
            )
            checkpoint = await manager.graph.aget_state(manager.graph_config("snapshot-branch"))

            assert [message.id for message in checkpoint.values["messages"]] == ["new-user"]
            assert checkpoint.values["status"] == "idle"
            assert checkpoint.values["answer_final"] == ""
        finally:
            await manager.close()

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


def test_material_answer_sections_reenter_model_when_their_local_evidence_is_missing() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(
            responses=[
                _tool_call("evidence", "primary"),
                AIMessage(
                    content=(
                        "| 报告期 | 营收同比 |\n"
                        "|---|---|\n"
                        "| 2026H1 | -6.2% |\n\n"
                        "核心观察：营收增长放缓。【证据 ev_evidence】\n\n"
                        "### 综合判断\n\n"
                        "**结论：当前估值处于低位，但需注意风险。**\n\n"
                        "**操作建议：**\n"
                        "- 分批关注后续数据，不一次性重仓"
                    )
                ),
                AIMessage(
                    content=(
                        "| 报告期 | 营收同比 |\n"
                        "|---|---|\n"
                        "| 2026H1 | -6.2% |\n\n"
                        "> 来源：财务数据【证据 ev_evidence】\n\n"
                        "核心观察：营收增长放缓。【证据 ev_evidence】\n\n"
                        "### 综合判断\n\n"
                        "**结论：当前估值处于低位，但需注意风险。【证据 ev_evidence】**\n\n"
                        "**操作建议：**\n"
                        "- 分批关注后续数据，不一次性重仓【证据 ev_evidence】"
                    )
                ),
            ]
        )
        result, _executor = await _run(model=model, conversation_id="material-evidence-repair")

        assert result.status == "completed"
        assert result.state["evidence_repair_count"] == 1
        assert len(model.calls) == 3
        assert len(result.state["claim_evidence"]) == 4
        assert all(claim["evidence_ids"] == ["ev_evidence"] for claim in result.state["claim_evidence"])
        repair = next(
            item
            for item in result.stage_history or []
            if item["stage"] == "evidence" and item["status"] == "started"
        )
        repair_text = "\n".join(
            str(target.get("text") or "")
            for target in repair["details"]["repair_targets"]
        )
        assert "报告期" in repair_text
        assert "结论" in repair_text
        assert "操作建议" in repair_text

    asyncio.run(scenario())


def test_unresolved_evidence_id_is_reported_once_and_does_not_enter_claim_mapping() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(
            responses=[
                _tool_call("evidence", "primary"),
                AIMessage(content="已核对来源【证据 ev_missing】"),
                AIMessage(content="已核对来源【证据 ev_evidence】"),
            ]
        )
        result, _executor = await _run(model=model, conversation_id="invalid-evidence-id")

        assert result.status == "completed"
        assert result.state["evidence_repair_count"] == 1
        assert result.state["claim_evidence"][0]["evidence_ids"] == ["ev_evidence"]
        repair = next(
            item
            for item in result.stage_history or []
            if item["stage"] == "evidence" and item["status"] == "started"
        )
        assert repair["details"]["unresolved_evidence_ids"] == ["ev_missing"]
        assert sum(
            "引用了无法解析的 evidence_id" in issue
            for issue in repair["details"]["issues"]
        ) == 1

    asyncio.run(scenario())


def test_unique_short_evidence_id_is_canonicalized_before_publication() -> None:
    async def scenario() -> None:
        action_id = "abcdefghi1234567890"
        model = ScriptedChatModel(
            responses=[
                _tool_call(action_id, "primary"),
                AIMessage(content="已核对来源【证据 ev_abcdefghi】"),
            ]
        )
        result, _executor = await _run(model=model, conversation_id="evidence-prefix")

        assert result.status == "completed"
        assert result.final_text == "已核对来源【证据 ev_abcdefghi1234567890】"
        assert result.state["claim_evidence"][0]["evidence_ids"] == [
            "ev_abcdefghi1234567890"
        ]

    asyncio.run(scenario())


def test_exhausted_evidence_repair_keeps_validator_details_out_of_final_answer() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(
            responses=[
                _tool_call("evidence", "primary"),
                AIMessage(content="候选回答【证据 ev_missing】"),
                AIMessage(content="修订回答【证据 ev_missing】"),
                AIMessage(content="最终回答【证据 ev_missing】"),
            ]
        )
        result, _executor = await _run(model=model, conversation_id="evidence-budget")

        assert result.status == "partial"
        assert result.error_code == "evidence_link_incomplete"
        assert "本轮外部证据关联未能完整通过" not in result.final_text
        assert "引用了无法解析的 evidence_id" in str(
            next(
                item
                for item in result.stage_history or []
                if item["stage"] == "evidence" and item["status"] == "failed"
            )["details"]["issues"]
        )

    asyncio.run(scenario())


def test_reference_only_source_requires_successful_web_body_read_before_final_answer() -> None:
    async def scenario() -> None:
        url = "https://example.test/news/1"
        unselected_url = "https://example.test/news/2"
        model = ScriptedChatModel(
            responses=[
                _named_tool_call(
                    "company-news",
                    "read_company_news_akshare",
                    {"symbol": "600519"},
                ),
                AIMessage(content="候选回答：公司近期有利好消息【证据 ev_company-news】。"),
                _named_tool_call(
                    "read-content",
                    "read_web_source",
                    {"source_id": "http", "url": url},
                ),
                AIMessage(content="正文确认了该消息【证据 ev_read-content】"),
            ]
        )
        result, executor = await _run(
            model=model,
            registry=_registry(_company_news_operation(), _web_source_operation()),
            executor=FakeAtomicExecutor(
                {
                    "read_company_news_akshare": [
                        {
                            "source_refs": [url, unselected_url],
                            "result": {
                                "success": True,
                                "items": [
                                    {"title": "模型选择的新闻", "url": url},
                                    {"title": "未选择的新闻", "url": unselected_url},
                                ],
                                "content_access": {
                                    "mode": "reference_only",
                                    "content_read": False,
                                    "content_extracted": False,
                                    "content_read_required": True,
                                },
                                "reference_links": [url, unselected_url],
                            },
                        }
                    ],
                    "read_web_source": [
                        {
                            "source_refs": [url],
                            "result": {
                                "success": True,
                                "url": url,
                                "final_url": url,
                                "content": "这是新闻正文。",
                                "extraction_method": "http+markdown",
                            },
                        }
                    ],
                }
            ),
            conversation_id="content-access-success",
        )

        assert result.status == "completed"
        assert [call["tool_name"] for call in executor.calls] == [
            "read_company_news_akshare",
            "read_web_source",
        ]
        assert result.state["content_access_targets"] == [
            {
                "url": url,
                "kind": "article",
                "title": "模型选择的新闻",
                "tool_name": "read_company_news_akshare",
                "action_id": "company-news",
            },
            {
                "url": unselected_url,
                "kind": "article",
                "title": "未选择的新闻",
                "tool_name": "read_company_news_akshare",
                "action_id": "company-news",
            }
        ]
        assert result.state["pending_content_reads"] == []
        # A cited reference-only result asks the model to select one relevant
        # URL.  Content access never fans out to the unselected candidate.
        assert result.state["content_access_repair_count"] == 1
        assert len(model.calls) == 4
        content_repair_request = model.call_options[2]
        assert content_repair_request["tool_choice"] == "required"
        assert {tool.name for tool in content_repair_request["tools"]} == {"read_web_source"}
        final_request = model.call_options[3]
        assert final_request["tool_choice"] is None
        assert {tool.name for tool in final_request["tools"]} == {
            "read_company_news_akshare",
            "read_web_source",
        }
        assert any(
            "read_web_source" in str(message.content) and url in str(message.content)
            for message in model.calls[2]
            if getattr(message, "type", "") == "system"
        )
        assert any(
            item["stage"] == "content_access" and item["status"] == "started"
            for item in result.stage_history or []
        )

    asyncio.run(scenario())


def test_reference_body_gate_is_scoped_to_the_cited_tool_call() -> None:
    async def scenario() -> None:
        news_url = "https://example.test/news/1"
        report_url = "https://example.test/report/1.pdf"
        model = ScriptedChatModel(
            responses=[
                _named_tool_call(
                    "news-call",
                    "read_company_news_akshare",
                    {"symbol": "600519"},
                ),
                _named_tool_call(
                    "report-call",
                    "read_company_research_reports_akshare",
                    {"symbol": "600519"},
                ),
                AIMessage(content="新闻结论【证据 ev_news-call】"),
                _named_tool_call(
                    "read-news",
                    "read_web_source",
                    {"source_id": "http", "url": news_url},
                ),
                AIMessage(content="新闻结论已核对【证据 ev_news-call】"),
            ]
        )
        result, executor = await _run(
            model=model,
            registry=_registry(
                _company_news_operation(),
                _company_research_operation(),
                _web_source_operation(),
            ),
            executor=FakeAtomicExecutor(
                {
                    "read_company_news_akshare": [
                        {
                            "source_refs": [news_url],
                            "result": {
                                "success": True,
                                "items": [{"title": "新闻", "url": news_url}],
                                "reference_links": [news_url],
                                "content_access": {
                                    "mode": "reference_only",
                                    "content_read": False,
                                    "content_extracted": False,
                                    "content_read_required": True,
                                },
                            },
                        }
                    ],
                    "read_company_research_reports_akshare": [
                        {
                            "source_refs": [report_url],
                            "result": {
                                "success": True,
                                "items": [{"title": "研报", "url": report_url}],
                                "reference_links": [report_url],
                                "content_access": {
                                    "mode": "reference_only",
                                    "content_read": False,
                                    "content_extracted": False,
                                    "content_read_required": True,
                                },
                            },
                        }
                    ],
                    "read_web_source": [
                        {
                            "source_refs": [news_url],
                            "result": {
                                "success": True,
                                "url": news_url,
                                "content": "新闻正文",
                            },
                        }
                    ],
                }
            ),
            conversation_id="content-access-per-tool-call",
        )

        assert result.status == "completed"
        assert [call["tool_name"] for call in executor.calls] == [
            "read_company_news_akshare",
            "read_company_research_reports_akshare",
            "read_web_source",
        ]
        assert [item["url"] for item in result.state["required_content_reads"]] == [news_url]
        assert result.state["pending_content_reads"] == []
        assert len(model.calls) == 5

    asyncio.run(scenario())


def test_reference_body_gate_does_not_accept_another_action_body_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_CONTENT_ACCESS_REPAIR_LIMIT", "1")

    async def scenario() -> None:
        news_url = "https://example.test/news/1"
        second_news_url = "https://example.test/news/2"
        report_url = "https://example.test/report/1.pdf"
        model = ScriptedChatModel(
            responses=[
                _named_tool_call(
                    "news-call",
                    "read_company_news_akshare",
                    {"symbol": "600519"},
                ),
                _named_tool_call(
                    "report-call",
                    "read_company_research_reports_akshare",
                    {"symbol": "600519"},
                ),
                AIMessage(content="新闻结论【证据 ev_news-call】"),
                _named_tool_call(
                    "read-report",
                    "read_web_source",
                    {"source_id": "http", "url": report_url},
                ),
                AIMessage(content="新闻结论【证据 ev_news-call】"),
            ]
        )
        result, executor = await _run(
            model=model,
            registry=_registry(
                _company_news_operation(),
                _company_research_operation(),
                _web_source_operation(),
            ),
            executor=FakeAtomicExecutor(
                {
                    "read_company_news_akshare": [
                        {
                            "result": {
                                "success": True,
                                "items": [
                                    {"title": "新闻 1", "url": news_url},
                                    {"title": "新闻 2", "url": second_news_url},
                                ],
                                "content_access": {
                                    "mode": "reference_only",
                                    "content_read": False,
                                    "content_extracted": False,
                                    "content_read_required": True,
                                },
                                "reference_links": [news_url, second_news_url],
                            }
                        }
                    ],
                    "read_company_research_reports_akshare": [
                        {
                            "result": {
                                "success": True,
                                "items": [{"title": "研报", "url": report_url}],
                                "content_access": {
                                    "mode": "reference_only",
                                    "content_read": False,
                                    "content_extracted": False,
                                    "content_read_required": True,
                                },
                                "reference_links": [report_url],
                            }
                        }
                    ],
                    "read_web_source": [
                        {
                            "result": {
                                "success": True,
                                "url": report_url,
                                "content": "研报正文",
                            }
                        }
                    ],
                }
            ),
            conversation_id="content-access-cross-action",
        )

        assert result.status == "partial"
        assert result.error_code == "content_access_incomplete"
        assert [call["tool_name"] for call in executor.calls] == [
            "read_company_news_akshare",
            "read_company_research_reports_akshare",
            "read_web_source",
        ]
        assert result.state["content_access_repair_count"] == 1
        assert result.state["required_content_reads"] == []
        assert "正文取证未完成" in result.final_text
        failed_stage = next(
            item
            for item in result.stage_history or []
            if item["stage"] == "content_access" and item["status"] == "failed"
        )
        assert failed_stage["details"]["selection_required_count"] == 1
        assert failed_stage["details"]["selection_required_action_ids"] == ["news-call"]

    asyncio.run(scenario())


def test_failed_content_read_keeps_pending_url_and_publishes_unverified_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_CONTENT_ACCESS_REPAIR_LIMIT", "1")

    async def scenario() -> None:
        url = "https://example.test/report.pdf"
        model = ScriptedChatModel(
            responses=[
                _named_tool_call(
                    "company-news",
                    "read_company_news_akshare",
                    {"symbol": "600519"},
                ),
                AIMessage(content="仅基于标题作出的候选结论【证据 ev_company-news】。"),
                _named_tool_call(
                    "read-content",
                    "read_web_source",
                    {"source_id": "http", "url": url},
                ),
                AIMessage(content="正文读取失败，但仍尝试给出结论【证据 ev_company-news】。"),
            ]
        )
        result, executor = await _run(
            model=model,
            registry=_registry(_company_news_operation(), _web_source_operation()),
            executor=FakeAtomicExecutor(
                {
                    "read_company_news_akshare": [
                        {
                            "source_refs": [url],
                            "result": {
                                "success": True,
                                "items": [{"title": "测试研报", "url": url}],
                                "content_access": {
                                    "mode": "reference_only",
                                    "content_read": False,
                                    "content_extracted": False,
                                    "content_read_required": True,
                                },
                                "reference_links": [url],
                            },
                        }
                    ],
                    "read_web_source": [
                        {
                            "success": False,
                            "source_refs": [url],
                            "errors": ["正文解析失败"],
                            "result": {
                                "success": False,
                                "url": url,
                                "content": "",
                            },
                        }
                    ],
                }
            ),
            conversation_id="content-access-failure",
        )

        assert result.status == "partial"
        assert result.error_code == "content_access_incomplete"
        assert "正文未核验" in result.final_text
        assert result.state["pending_content_reads"][0]["url"] == url
        assert result.state["claim_evidence"]
        assert result.state["claim_evidence"][0]["evidence_ids"] == ["ev_company-news"]
        assert [call["tool_name"] for call in executor.calls] == [
            "read_company_news_akshare",
            "read_web_source",
        ]

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


def _matrix_tool_call(action_id: str, source_id: str = "primary") -> AIMessage:
    return _tool_call(action_id, source_id)


def _matrix_parallel_call(action_ids: list[str]) -> AIMessage:
    return AIMessage(
        content="先完成这一轮独立来源核验。",
        tool_calls=[
            {
                "name": "search_source",
                "args": {"source_id": "primary" if index % 2 == 0 else "secondary", "query": "测试问题"},
                "id": action_id,
                "type": "tool_call",
            }
            for index, action_id in enumerate(action_ids)
        ],
    )


def _matrix_case(
    case_id: int,
) -> tuple[ScriptedChatModel, ToolRegistry, FakeAtomicExecutor, int]:
    if case_id in {1, 19}:
        return (
            ScriptedChatModel(responses=[AIMessage(content=f"第 {case_id} 轮无需外部取证。")]),
            _registry(),
            FakeAtomicExecutor(),
            0,
        )

    if case_id == 18:
        auto_operation = ToolSpec(
            name="search_source",
            description="从自动来源检索公开材料",
            parameters=object_schema(
                {
                    "source_id": {"type": "string", "enum": ["auto"]},
                    "query": {"type": "string"},
                },
                required=("source_id", "query"),
            ),
            executor=lambda **_kwargs: {"success": True},
            source_catalog=(
                {"id": "auto", "name": "自动来源", "purpose": "测试来源"},
            ),
            max_attempts=1,
        )
        return (
            ScriptedChatModel(
                responses=[
                    _matrix_tool_call("matrix-auto", "auto"),
                    AIMessage(content="自动来源已返回结果【证据 ev_matrix-auto】"),
                ]
            ),
            _registry(auto_operation),
            FakeAtomicExecutor(),
            1,
        )

    if case_id == 11:
        return (
            ScriptedChatModel(
                responses=[
                    _matrix_tool_call("matrix-evidence-repair"),
                    AIMessage(content="候选回答暂未标注来源。"),
                    AIMessage(content="修订后保留可追溯结论【证据 ev_matrix-evidence-repair】"),
                ]
            ),
            _registry(_search_operation()),
            FakeAtomicExecutor(),
            1,
        )

    action_ids = {
        2: ["matrix-single"],
        3: ["matrix-sequence-a", "matrix-sequence-b"],
        4: ["matrix-parallel-a", "matrix-parallel-b"],
        5: ["matrix-parallel-1", "matrix-parallel-2", "matrix-parallel-3"],
        6: ["matrix-failed", "matrix-alternate"],
        7: ["matrix-empty", "matrix-empty-alternate"],
        8: ["matrix-stale", "matrix-stale-alternate"],
        9: ["matrix-unknown", "matrix-unknown-alternate"],
        10: ["matrix-partial"],
        12: ["matrix-dated"],
        13: ["matrix-context"],
        14: ["matrix-cited-a", "matrix-cited-b"],
        15: ["matrix-repeat-a", "matrix-repeat-b"],
        16: ["matrix-empty-only"],
        17: ["matrix-stale-only"],
        20: ["matrix-plan-a", "matrix-plan-b"],
    }[case_id]
    cited_ids = {
        6: ["matrix-alternate"],
        8: ["matrix-stale-alternate"],
        9: ["matrix-unknown-alternate"],
    }.get(case_id, action_ids)
    final_ids = "、".join(f"【证据 ev_{action_id}】" for action_id in cited_ids)

    if case_id in {4, 5, 14}:
        responses = [_matrix_parallel_call(action_ids), AIMessage(content=f"本轮已完成核验{final_ids}")]
    elif case_id == 20:
        responses = [
            AIMessage(content="先规划：先读取基础资料，再交叉核对。", tool_calls=[
                {
                    "name": "search_source",
                    "args": {"source_id": "primary", "query": "测试问题"},
                    "id": action_ids[0],
                    "type": "tool_call",
                }
            ]),
            AIMessage(content="上一轮已完成基础读取，下一步核对备用来源。", tool_calls=[
                {
                    "name": "search_source",
                    "args": {"source_id": "secondary", "query": "测试问题"},
                    "id": action_ids[1],
                    "type": "tool_call",
                }
            ]),
            AIMessage(content=f"已完成两轮交叉核验{final_ids}"),
        ]
    else:
        responses = []
        for index, action_id in enumerate(action_ids):
            responses.append(_matrix_tool_call(action_id, "primary" if index % 2 == 0 else "secondary"))
        responses.append(AIMessage(content=f"本轮已完成处理{final_ids}"))

    outcomes: dict[str, list[Mapping[str, Any]]] = {}
    if case_id == 6:
        outcomes["search_source"] = [
            {"success": False, "errors": ["primary unavailable"], "error_code": "provider_unavailable"},
            {"result": {"headline": "备用来源结果"}},
        ]
    elif case_id == 7:
        outcomes["search_source"] = [
            {"result": {"result_count": 0, "items": []}},
            {"result": {"headline": "备用来源结果"}},
        ]
    elif case_id == 8:
        outcomes["search_source"] = [
            {"data_time": "2025-01-01", "result": {"is_stale": True, "headline": "过期结果"}},
            {"result": {"headline": "新来源结果"}},
        ]
    elif case_id == 9:
        outcomes["search_source"] = [
            {"data_time": None, "result": {"freshness_unknown": True, "headline": "时间未知"}},
            {"result": {"headline": "已确认结果"}},
        ]
    elif case_id == 10:
        outcomes["search_source"] = [{"partial": True, "result": {"headline": "部分结果"}}]
    elif case_id == 13:
        outcomes["search_source"] = [{
            "result": {
                "_agent_context": {
                    "type": "stock_group",
                    "group_id": "matrix",
                    "group_name": "测试分组",
                    "member_count": 3,
                }
            }
        }]
    elif case_id == 16:
        outcomes["search_source"] = [{"result": {"result_count": 0, "items": []}}]
    elif case_id == 17:
        outcomes["search_source"] = [{"data_time": "2025-01-01", "result": {"is_stale": True}}]

    if case_id == 12:
        responses[-1] = AIMessage(content="截至 2026-08-08，资料已返回【证据 ev_matrix-dated】")
    elif case_id == 13:
        responses[-1] = AIMessage(content="测试分组已读取【证据 ev_matrix-context】")
    elif case_id == 16:
        responses[-1] = AIMessage(content="本次搜索没有返回结果，不能据此得出外部事实【证据 ev_matrix-empty-only】")
    elif case_id == 17:
        responses[-1] = AIMessage(content="该来源只有较早资料，时效不能确认【证据 ev_matrix-stale-only】")

    return (
        ScriptedChatModel(responses=responses),
        _registry(_search_operation()),
        FakeAtomicExecutor(outcomes),
        len(action_ids),
    )


@pytest.mark.parametrize(
    "case_id",
    list(range(1, 21)),
    ids=[
        "plain-1",
        "single-read",
        "sequential-reads",
        "parallel-2",
        "parallel-3",
        "failed-then-alternate",
        "empty-then-alternate",
        "stale-then-alternate",
        "freshness-unknown-then-alternate",
        "partial-result",
        "evidence-repair",
        "dated-evidence",
        "context-handoff",
        "multiple-citations",
        "repeated-source-rounds",
        "empty-only-honest-answer",
        "stale-only-honest-answer",
        "auto-source",
        "plain-2",
        "visible-plan-rounds",
    ],
)
def test_native_agent_loop_20_behavior_scenarios(case_id: int) -> None:
    async def scenario() -> None:
        model, registry, executor, expected_tool_count = _matrix_case(case_id)
        result, executor = await _run(
            model=model,
            registry=registry,
            executor=executor,
            conversation_id=f"matrix-{case_id}",
        )
        assert result.status == "completed"
        assert len(executor.calls) == expected_tool_count
        assert result.final_text.strip()
        assert any(item["stage"] == "publish" for item in result.stage_history or [])
        if expected_tool_count:
            assert result.state["tool_call_count"] == expected_tool_count

    asyncio.run(scenario())
