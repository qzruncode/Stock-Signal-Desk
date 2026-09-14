"""Native LangChain streaming and append-only publication contracts."""

from __future__ import annotations

import asyncio
from typing import Any, TypedDict

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langgraph.graph import END, START, StateGraph

from src.agent.langgraph_runtime.events import GraphEventBridge
from src.agent.langgraph_runtime.model import LiteLLMChatModel, LiteLLMGateway
from src.agent.langgraph_runtime.planning import PlanningRoute


class _AsyncProviderStream:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self._items = iter(items)
        self.closed = False

    def __aiter__(self) -> "_AsyncProviderStream":
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._items)
        except StopIteration as exc:
            raise StopAsyncIteration from exc

    async def aclose(self) -> None:
        self.closed = True


def _stream_items() -> list[dict[str, Any]]:
    return [
        {
            "id": "chat-1",
            "model": "test-model",
            "choices": [{"delta": {"role": "assistant", "content": "先规划："}}],
        },
        {"choices": [{"delta": {"content": "核验来源，再给结论。"}}]},
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-1",
                                "function": {"name": "search_source", "arguments": '{"query":"'},
                            }
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {"arguments": '测试"}'},
                            }
                        ],
                        "reasoning_content": "内部推理不会进入用户答案",
                    }
                }
            ]
        },
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ]


@pytest.mark.parametrize(
    ("choice", "expected"),
    [
        ("any", "required"),
        (True, "required"),
        (False, None),
        ("auto", "auto"),
        ("required", "required"),
        ("search_source", {"type": "function", "function": {"name": "search_source"}}),
        (
            {"type": "function", "function": {"name": "search_source"}},
            {"type": "function", "function": {"name": "search_source"}},
        ),
    ],
)
def test_litellm_adapter_normalizes_native_tool_choice_before_provider_call(choice, expected):
    async def scenario():
        requests = []

        async def completion(**kwargs):
            requests.append(kwargs)
            return _AsyncProviderStream(_stream_items())

        gateway = LiteLLMGateway(
            llm_config={"model": "test-model"},
            database=None,
            run_id="choice",
            worker_id="worker",
            completion=completion,
        )
        model = LiteLLMChatModel(gateway=gateway, llm_config={"model": "test-model"})
        tool = {
            "type": "function",
            "function": {
                "name": "search_source",
                "description": "查询",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        await model.bind_tools([tool], tool_choice=choice).ainvoke([HumanMessage(content="查询")])
        assert requests[0].get("tool_choice") == expected

    asyncio.run(scenario())


def test_litellm_adapter_uses_langchain_async_stream_and_merges_tool_calls() -> None:
    async def scenario() -> None:
        requests: list[dict[str, Any]] = []

        async def completion(**kwargs: Any) -> _AsyncProviderStream:
            requests.append(kwargs)
            return _AsyncProviderStream(_stream_items())

        gateway = LiteLLMGateway(
            llm_config={"model": "test-model"},
            database=None,
            run_id="run-stream",
            worker_id="worker",
            completion=completion,
        )
        model = LiteLLMChatModel(gateway=gateway, llm_config={"model": "test-model"})
        chunks = [chunk async for chunk in model.astream([HumanMessage(content="测试")])]

        assert requests and requests[0]["stream"] is True
        assert len(requests) == 1
        assert any(isinstance(chunk, AIMessageChunk) for chunk in chunks)
        combined = chunks[0]
        for chunk in chunks[1:]:
            combined = combined + chunk
        assert "先规划" in str(combined.content)
        assert combined.tool_calls[0]["name"] == "search_source"
        assert combined.tool_calls[0]["args"] == {"query": "测试"}
        assert combined.tool_calls[0]["id"] == "call-1"

    asyncio.run(scenario())


def test_structured_output_uses_exact_tool_and_non_streaming_request() -> None:
    async def scenario() -> None:
        requests: list[dict[str, Any]] = []

        async def completion(**kwargs: Any) -> dict[str, Any]:
            requests.append(kwargs)
            return {
                "id": "route-1",
                "model": "test-model",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "route-call",
                                    "type": "function",
                                    "function": {
                                        "name": "PlanningRoute",
                                        "arguments": '{"mode":"planned","reason":"需要跨来源核验"}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            }

        gateway = LiteLLMGateway(
            llm_config={"model": "test-model"},
            database=None,
            run_id="run-structured",
            worker_id="worker",
            completion=completion,
        )
        model = LiteLLMChatModel(gateway=gateway, llm_config={"model": "test-model"})
        result = await model.with_structured_output(
            PlanningRoute,
            include_raw=True,
            tool_choice="PlanningRoute",
            stream=False,
        ).ainvoke([HumanMessage(content="判断是否需要规划")])

        assert result["parsing_error"] is None
        assert result["parsed"].mode == "planned"
        assert requests[0]["stream"] is False
        assert "stream_options" not in requests[0]
        assert requests[0]["parallel_tool_calls"] is False
        assert requests[0]["tool_choice"] == {
            "type": "function",
            "function": {"name": "PlanningRoute"},
        }

    asyncio.run(scenario())


def test_litellm_gateway_requires_a_model_from_configuration() -> None:
    with pytest.raises(ValueError, match="configured model settings"):
        LiteLLMGateway(
            llm_config={},
            database=None,
            run_id="run-missing-model",
            worker_id="worker",
        )


def test_native_callback_reports_cumulative_stream_usage_only_once() -> None:
    from langchain_core.callbacks import UsageMetadataCallbackHandler

    async def scenario():
        async def completion(**kwargs):
            assert kwargs['stream_options'] == {'include_usage': True}
            return _AsyncProviderStream([
                {'model': 'test-model', 'choices': [{'delta': {'content': 'answer'}}],
                 'usage': {'prompt_tokens': 10, 'completion_tokens': 1, 'total_tokens': 11}},
                {'model': 'test-model', 'choices': [],
                 'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}},
            ])
        gateway = LiteLLMGateway(llm_config={'model': 'test-model'}, database=None,
                                run_id='usage', worker_id='test', completion=completion)
        usage = UsageMetadataCallbackHandler()
        model = LiteLLMChatModel(gateway=gateway, llm_config={'model': 'test-model'}, callbacks=[usage])
        response = await model.ainvoke([HumanMessage(content='test')])
        assert response.response_metadata['model_name'] == 'test-model'
        assert response.usage_metadata['total_tokens'] == 15
        assert usage.usage_metadata['test-model']['total_tokens'] == 15
    asyncio.run(scenario())


def test_graph_event_bridge_streams_native_chunks_and_deduplicates_accepted_answer() -> None:
    class Controller:
        def __init__(self) -> None:
            self.texts: list[str] = []

        def append_text(self, value: str) -> None:
            self.texts.append(value)

    controller = Controller()
    events = GraphEventBridge(controller, run_id="run-events")
    events.begin_model_turn(1)
    events.model_message(AIMessageChunk(content="先规划："))
    events.model_message(
        AIMessageChunk(
            content="先查资料。",
            tool_call_chunks=[
                {"name": "search_source", "args": "{}", "id": "call-1", "index": 0}
            ],
        )
    )
    events.model_message(
        AIMessage(
            content="先规划：先查资料。",
            tool_calls=[{"name": "search_source", "args": {}, "id": "call-1", "type": "tool_call"}],
        )
    )
    events.commit_model_progress()

    events.begin_model_turn(2)
    events.model_message(AIMessageChunk(content="未核验候选答案"))
    events.commit_model_answer("已核验的最终答案")
    events.text("已核验的最终答案")

    assert controller.texts == [
        "先规划：",
        "先查资料。",
        "已核验的最终答案",
    ]

    events.begin_model_turn(3)
    events.model_message(AIMessageChunk(content="最终答案"))
    events.commit_model_answer("最终答案")

    assert controller.texts[-1:] == ["最终答案"]


def test_graph_event_bridge_keeps_planning_progress_separate_from_accepted_answer() -> None:
    class Controller:
        def __init__(self) -> None:
            self.texts: list[str] = []

        def append_text(self, value: str) -> None:
            self.texts.append(value)

    controller = Controller()
    events = GraphEventBridge(controller, run_id="run-progress")

    events.progress("已完成取证，接下来整理结论。\n\n")
    events.text("最终答案")

    assert controller.texts == ["已完成取证，接下来整理结论。\n\n", "最终答案"]

    events.begin_model_turn(4)
    events.commit_model_answer("最终答案")

    assert controller.texts[-1:] == ["最终答案"]


def test_graph_event_bridge_assigns_one_round_id_to_each_model_phase() -> None:
    events = GraphEventBridge(None, run_id="run-phase-identity")

    events.begin_model_turn(1)
    first = events.stage("model", "started", "第一阶段")
    parallel_tool = events.stage("tool", "started", "执行 search_source", action_id="call-1")
    events.begin_model_turn(2)
    second = events.stage("model", "started", "第二阶段")

    assert first["round_id"] == "1"
    assert parallel_tool["round_id"] == "1"
    assert second["round_id"] == "2"


def test_graph_event_bridge_uses_langgraph_custom_stream_for_ordered_projection() -> None:
    class Controller:
        def __init__(self) -> None:
            self.texts: list[str] = []
            self.data: list[dict[str, Any]] = []
            self.tool_calls: list[dict[str, Any]] = []

        def append_text(self, value: str) -> None:
            self.texts.append(value)

        def add_data(self, value: dict[str, Any]) -> None:
            self.data.append(value)

        async def add_tool_call(self, tool_name: str, *, tool_call_id: str, parent_id: str | None = None):
            call = {"tool_name": tool_name, "tool_call_id": tool_call_id, "parent_id": parent_id}
            self.tool_calls.append(call)

            class Handle:
                def append_args_text(_self, value: str) -> None:
                    call["args_text"] = value

                def set_response(_self, result: Any, is_error: bool = False) -> None:
                    call["result"] = result
                    call["is_error"] = is_error

            return Handle()

    class State(TypedDict):
        done: bool

    controller = Controller()
    bridge = GraphEventBridge(controller, run_id="run-custom-projection")

    async def node(_state: State) -> dict[str, bool]:
        bridge.stage(
            "planning",
            "failed",
            "内部契约失败",
            details={"planning_phase": "contract_retry", "attempt": 1, "max_attempts": 2},
            user_message="刚才的研究计划格式不够完整，我正在修正后继续。",
        )
        tool = await bridge.add_tool_call("read_source", tool_call_id="call-custom", parent_id="action-custom")
        tool.append_args_text('{"source_id":"primary"}')
        tool.set_response({"success": True, "value": "ok"})
        bridge.progress("已完成读取，接下来整理结论。\n\n")
        return {"done": True}

    graph = (
        StateGraph(State)
        .add_node("node", node)
        .add_edge(START, "node")
        .add_edge("node", END)
        .compile()
    )

    async def scenario() -> None:
        async for chunk in graph.astream(
            {"done": False},
            stream_mode=["custom"],
            version="v2",
        ):
            if chunk.get("type") == "custom":
                await bridge.consume_stream_record(chunk.get("data"))

    asyncio.run(scenario())

    assert controller.data[0]["stage"] == "planning"
    assert controller.data[0]["details"]["user_message"] == "刚才的研究计划格式不够完整，我正在修正后继续。"
    assert controller.texts == [
        "刚才的研究计划格式不够完整，我正在修正后继续。\n\n",
        "已完成读取，接下来整理结论。\n\n",
    ]
    assert controller.tool_calls == [{
        "tool_name": "read_source",
        "tool_call_id": "call-custom",
        "parent_id": "action-custom",
        "args_text": '{"source_id":"primary"}',
        "result": {"success": True, "value": "ok"},
        "is_error": False,
    }]


def test_graph_event_bridge_coalesces_duplicate_stage_copy_but_keeps_audit_events() -> None:
    class Controller:
        def __init__(self) -> None:
            self.texts: list[str] = []
            self.data: list[dict[str, Any]] = []

        def append_text(self, value: str) -> None:
            self.texts.append(value)

        def add_data(self, value: dict[str, Any]) -> None:
            self.data.append(value)

    controller = Controller()
    events = GraphEventBridge(controller, run_id="run-duplicate-progress")

    events.stage(
        "evidence",
        "started",
        "第一次校验",
        user_message="我正在逐段核对结论和来源，确保每个判断都有对应依据。",
    )
    events.stage(
        "evidence",
        "started",
        "第二次校验",
        user_message="我正在逐段核对结论和来源，确保每个判断都有对应依据。",
    )

    assert len(controller.data) == 2
    assert controller.texts == ["我正在逐段核对结论和来源，确保每个判断都有对应依据。\n\n"]


def test_graph_event_bridge_coalesces_progress_already_contained_in_prior_projection() -> None:
    class Controller:
        def __init__(self) -> None:
            self.texts: list[str] = []

        def append_text(self, value: str) -> None:
            self.texts.append(value)

    controller = Controller()
    events = GraphEventBridge(controller, run_id="run-contained-progress")

    events.progress("上一轮已经确认代码和名称，下一步补充行业归属。\n\n")
    events.progress("确认代码和名称，下一步补充行业归属。\n\n")

    assert controller.texts == ["上一轮已经确认代码和名称，下一步补充行业归属。\n\n"]


def test_graph_event_bridge_coalesces_near_duplicate_progress_revisions() -> None:
    class Controller:
        def __init__(self) -> None:
            self.texts: list[str] = []

        def append_text(self, value: str) -> None:
            self.texts.append(value)

    controller = Controller()
    events = GraphEventBridge(controller, run_id="run-near-duplicate-progress")
    first = (
        "市场宽度数据已确认：当前沪深A股上涨家数多于下跌家数（2946 vs 2086），"
        "涨停51家、跌停11家，市场活跃度56.45%，整体偏强。但主要指数（上证、深证、创业板、科创50）"
        "的行情数据因数据源未就绪而获取失败，需要重试获取指数行情，才能完整判断当前市场环境。"
    )
    revised = (
        "市场宽度数据已确认：当前沪深A股上涨家数明显多于下跌家数（2946 vs 2086），"
        "涨停51家、跌停11家，市场活跃度56.45%，整体环境偏强。但主要指数（上证、深证、创业板、科创50）"
        "的行情数据因数据源未就绪而两次获取失败，指数层面的市场强弱判断仍缺失。需要重试获取主要指数行情，"
        "才能完整判断当前市场环境，再进入板块资金流分析。"
    )

    events.progress(first)
    events.progress(revised)

    assert controller.texts == [first]
