"""Focused checks for the generic LangChain/LangGraph Agent loop."""

from __future__ import annotations

import asyncio
from collections import OrderedDict, defaultdict
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, Mapping

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict, Field

from src.agent.langgraph_runtime.executor import action_fingerprint
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
        }
        assert events.texts == []
        assert events.stages[-1] == ("publish", "failed", "上游模型服务返回超时；已保留已有工具观察和证据。")

    asyncio.run(scenario())


def test_model_stage_reports_prompt_footprint_without_imposing_a_new_limit() -> None:
    async def scenario() -> None:
        result, _executor = await _run(
            model=ScriptedChatModel(responses=[AIMessage(content="这是一个可用的回答。")]),
            registry=_registry(_search_operation()),
            conversation_id="prompt-footprint",
        )

        model_started = next(
            item
            for item in result.stage_history or []
            if item["stage"] == "model" and item["status"] == "started"
        )
        details = model_started["details"]
        assert details["operation_count"] == 1
        assert details["bound_tool_count"] == 1
        assert details["directory_character_count"] > 0
        assert details["system_prompt_character_count"] > 0
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


def test_group_context_survives_a_later_turn_on_the_same_checkpoint_thread() -> None:
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
        manager = LangGraphRuntimeManager(registry=registry)
        await manager.start(testing=True)
        try:
            first = await manager.run_new(
                messages=[{"role": "user", "content": "读取新能源分组"}],
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
            )
            assert second.state["conversation_context"]["group_name"] == "新能源"
            assert any("新能源" in str(message.content) for message in second_model.calls[0])
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
        manager = LangGraphRuntimeManager(registry=_registry(operation))
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
