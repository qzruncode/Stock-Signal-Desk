"""Native LangChain streaming and append-only publication contracts."""

from __future__ import annotations

import asyncio
from typing import Any, TypedDict

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk
from langgraph.graph import END, START, StateGraph

from src.agent.langgraph_runtime.events import GraphEventBridge, project_stage_history_for_client
from src.agent.langgraph_runtime.model import LiteLLMChatModel, LiteLLMGateway
from src.agent.langgraph_runtime.model_projection import StructuredContractProjectionCallback
from src.agent.langgraph_runtime.planning import PlanningRoute
from src.agent.langgraph_runtime.team.events import TeamWorkerEventBridge


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


def test_structured_projection_callback_survives_native_tool_binding() -> None:
    async def scenario() -> None:
        class Events:
            def __init__(self) -> None:
                self.projections: list[str] = []

            def publish_model_projection(self, text: str, **_: Any) -> None:
                self.projections.append(text)

        async def completion(**_: Any) -> _AsyncProviderStream:
            return _AsyncProviderStream([
                {
                    "id": "projection-1",
                    "model": "test-model",
                    "choices": [{"delta": {"tool_calls": [{
                        "index": 0,
                        "id": "projection-call",
                        "function": {
                            "name": "StructuredAgentAnswer",
                            "arguments": '{"progress_text":"已完成取证，',
                        },
                    }]}}],
                },
                {
                    "choices": [{"delta": {"tool_calls": [{
                        "index": 0,
                        "function": {
                            "arguments": '正在整理答案。","blocks":[]}',
                        },
                    }]}}],
                },
            ])

        events = Events()
        callback = StructuredContractProjectionCallback(
            events,
            projection_id="run-direct:answer:1",
            scope="direct",
            collaboration_id="",
            agent_id="",
            task_id="",
            phase="answer",
            kind="answer-progress",
            target_tool_name="StructuredAgentAnswer",
            display_part_name="agent-model-projection",
        )
        gateway = LiteLLMGateway(
            llm_config={"model": "test-model"},
            database=None,
            run_id="run-projection",
            worker_id="worker",
            completion=completion,
        )
        model = LiteLLMChatModel(gateway=gateway, llm_config={"model": "test-model"})
        # LangGraph binds tools after middleware returns.  Keeping the
        # callback on the chat-model instance is the part that must survive
        # that native binding step.
        model = model.model_copy(update={"callbacks": [callback]})
        await model.bind_tools(
            [{
                "type": "function",
                "function": {
                    "name": "StructuredAgentAnswer",
                    "parameters": {"type": "object"},
                },
            }],
            tool_choice="required",
        ).ainvoke([HumanMessage(content="生成结构化回答")])

        assert events.projections == ["已完成取证，", "已完成取证，正在整理答案。"]

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
            self.data: list[dict[str, Any]] = []

        def append_text(self, value: str) -> None:
            self.texts.append(value)

        def add_data(self, value: dict[str, Any]) -> None:
            self.data.append(value)

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
    # Domain-tool detection is only the semantic boundary.  The raw chunks
    # remain buffered until the completed AIMessage is available, so a
    # provisional provider fragment can never become visible prose.
    assert controller.texts == []
    assert controller.data == []
    events.model_message(
        AIMessage(
            content="先规划：先查资料。",
            tool_calls=[{"name": "search_source", "args": {}, "id": "call-1", "type": "tool_call"}],
        )
    )
    events.commit_model_progress("先规划：先查资料。")

    assert controller.texts == []
    assert len(controller.data) == 1
    assert controller.data[0]["part"]["name"] == "agent-model-projection"
    assert controller.data[0]["part"]["data"]["text"] == "先规划：先查资料。"

    events.begin_model_turn(2)
    events.model_message(AIMessageChunk(content="未核验候选答案"))
    events.commit_model_answer("已核验的最终答案")
    events.text("已核验的最终答案")

    assert controller.texts == [
        "已核验的最终答案",
    ]

    events.begin_model_turn(3)
    events.model_message(AIMessageChunk(content="最终答案"))
    events.commit_model_answer("最终答案")

    assert controller.texts[-1:] == ["最终答案"]


def test_graph_event_bridge_does_not_stream_structured_answer_candidate() -> None:
    class Controller:
        def __init__(self) -> None:
            self.texts: list[str] = []

        def append_text(self, value: str) -> None:
            self.texts.append(value)

    controller = Controller()
    events = GraphEventBridge(controller, run_id="run-structured-candidate")
    events.begin_model_turn(1)
    events.model_message(
        AIMessageChunk(
            content="候选答案不应提前展示",
            tool_call_chunks=[
                {
                    "name": "StructuredAgentAnswer",
                    "args": "{\"progress_text\":\"正在整理，",
                    "id": "answer-call",
                    "index": 0,
                }
            ],
        )
    )
    events.commit_model_progress()

    assert controller.texts == []
    events.commit_model_answer("验收后的最终答案")
    assert controller.texts == ["验收后的最终答案"]


def test_graph_event_bridge_drops_incomplete_tool_progress_projection() -> None:
    class Controller:
        def __init__(self) -> None:
            self.texts: list[str] = []
            self.data: list[dict[str, Any]] = []

        def append_text(self, value: str) -> None:
            self.texts.append(value)

        def add_data(self, value: dict[str, Any]) -> None:
            self.data.append(value)

    controller = Controller()
    events = GraphEventBridge(controller, run_id="run-progress-boundary")
    events.begin_model_turn(1)
    events.model_message(AIMessageChunk(
        content="我来查询通威股份（600438）的最新实时",
        tool_call_chunks=[{
            "name": "read_realtime_quote",
            "args": "{}",
            "id": "call-1",
            "index": 0,
        }],
    ))
    events.commit_model_progress("我来查询通威股份（600438）的最新实时")
    assert controller.data == []

    events.begin_model_turn(2)
    events.model_message(AIMessageChunk(
        content="通威股份（600438买入。",
        tool_call_chunks=[{
            "name": "read_realtime_quote",
            "args": "{}",
            "id": "call-2",
            "index": 0,
        }],
    ))
    events.commit_model_progress("通威股份（600438买入。")
    assert controller.data == []

    events.begin_model_turn(3)
    events.model_message(AIMessageChunk(
        content="我来查询通威股份（600438）的最新实时行情。",
        tool_call_chunks=[{
            "name": "read_realtime_quote",
            "args": "{}",
            "id": "call-3",
            "index": 0,
        }],
    ))
    events.commit_model_progress("我来查询通威股份（600438）的最新实时行情。")
    assert len(controller.data) == 1
    assert controller.data[0]["part"]["data"]["text"] == "我来查询通威股份（600438）的最新实时行情。"


def test_structured_contract_projection_streams_only_the_explicit_direct_progress_field() -> None:
    class Events:
        def __init__(self) -> None:
            self.projections: list[dict[str, Any]] = []

        def publish_model_projection(self, text: str, **kwargs: Any) -> None:
            self.projections.append({"text": text, **kwargs})

    async def scenario() -> None:
        events = Events()
        callback = StructuredContractProjectionCallback(
            events,
            projection_id="run-direct:answer:1",
            scope="direct",
            collaboration_id="",
            agent_id="",
            task_id="",
            phase="answer",
            kind="answer-progress",
            target_tool_name="StructuredAgentAnswer",
            display_part_name="agent-model-projection",
        )
        await callback.on_llm_new_token(
            "",
            chunk=ChatGenerationChunk(message=AIMessageChunk(
                content="",
                tool_call_chunks=[{
                    "name": "StructuredAgentAnswer",
                    "args": '{"progress_text":"我已经完成取证，',
                    "index": 0,
                }],
            )),
        )
        await callback.on_llm_new_token(
            "",
            chunk=ChatGenerationChunk(message=AIMessageChunk(
                content="",
                tool_call_chunks=[{
                    "args": '正在整理答案。","profile":"research"}',
                    "index": 0,
                }],
            )),
        )
        await callback.on_llm_new_token(
            "",
            chunk=ChatGenerationChunk(message=AIMessageChunk(
                content="",
                tool_call_chunks=[{
                    "name": "search_source",
                    "args": '{"progress_text":"不应展示"}',
                    "index": 1,
                }],
            )),
        )

        assert [item["text"] for item in events.projections] == [
            "我已经完成取证，",
            "我已经完成取证，正在整理答案。",
        ]
        assert all(item["display_part_name"] == "agent-model-projection" for item in events.projections)

    asyncio.run(scenario())


def test_graph_event_bridge_keeps_structured_answer_blocks_markdown_separated() -> None:
    class Controller:
        def __init__(self) -> None:
            self.texts: list[str] = []
            self.data: list[dict[str, Any]] = []

        def append_text(self, value: str) -> None:
            self.texts.append(value)

        def add_data(self, value: dict[str, Any]) -> None:
            self.data.append(value)

    controller = Controller()
    events = GraphEventBridge(controller, run_id="run-structured-answer-blocks")
    events.commit_model_answer(
        "# 行情与基本面\n\n## 行情\n\n行情已核验。\n\n## 基本面\n\n基本面已核验。",
        structured_answer={
            "profile": "general",
            "title": "行情与基本面",
            "blocks": [
                {"section": "行情", "kind": "fact", "content": "行情已核验。"},
                {"section": "基本面", "kind": "fact", "content": "基本面已核验。"},
            ],
        },
    )

    assert controller.texts == [
        "# 行情与基本面\n\n## 行情\n\n行情已核验。",
        "\n\n",
        "## 基本面\n\n基本面已核验。",
    ]


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


def test_graph_event_bridge_uses_one_native_controller_for_ordered_projection() -> None:
    class Controller:
        def __init__(self) -> None:
            self.texts: list[str] = []
            self.data: list[dict[str, Any]] = []
            self.tool_calls: list[dict[str, Any]] = []
            self.events: list[tuple[str, Any]] = []

        def append_text(self, value: str) -> None:
            self.texts.append(value)
            self.events.append(("text", value))

        def add_data(self, value: dict[str, Any]) -> None:
            self.data.append(value)
            self.events.append(("data", value))

        async def add_tool_call(self, tool_name: str, *, tool_call_id: str, parent_id: str | None = None):
            call = {"tool_name": tool_name, "tool_call_id": tool_call_id, "parent_id": parent_id}
            self.tool_calls.append(call)
            self.events.append(("tool-call", call))

            class Handle:
                def append_args_text(_self, value: str) -> None:
                    call["args_text"] = value
                    controller.events.append(("tool-args", value))

                def set_response(_self, result: Any, is_error: bool = False) -> None:
                    call["result"] = result
                    call["is_error"] = is_error
                    controller.events.append(("tool-result", result))

            return Handle()

    class State(TypedDict):
        done: bool

    controller = Controller()
    bridge = GraphEventBridge(controller, run_id="run-native-projection")

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
        # The controller is the single ordered presentation sink.  LangGraph
        # still drives the node, but its state/update stream is not rematerialized
        # as a second chat projection.
        async for _chunk in graph.astream(
            {"done": False},
            stream_mode=["updates"],
            version="v2",
        ):
            pass

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
    assert [kind for kind, _value in controller.events] == [
        "data",
        "text",
        "tool-call",
        "tool-args",
        "tool-result",
        "text",
    ]


def test_team_stage_is_control_plane_and_model_projection_is_the_only_team_prose() -> None:
    class Controller:
        def __init__(self) -> None:
            self.texts: list[str] = []
            self.data: list[dict[str, Any]] = []

        def append_text(self, value: str) -> None:
            self.texts.append(value)

        def add_data(self, value: dict[str, Any]) -> None:
            self.data.append(value)

    controller = Controller()
    bridge = GraphEventBridge(controller, run_id="team-projection")
    stage = bridge.stage(
        "planning",
        "started",
        "服务端生命周期摘要",
        user_message="这段旧的固定文案不能进入 Team 正文",
        details={"team_id": "team-1", "task_id": "market-task", "agent_id": "market"},
    )
    bridge.publish_model_projection(
        "模型根据当前证据说明实际进展。",
        scope="expert",
        collaboration_id="team-1",
        agent_id="market",
        task_id="market-task",
        phase="worker",
        kind="report",
    )

    assert "user_message" not in stage["details"]
    assert controller.texts == []
    assert controller.data[-1]["event"] == "agent_display_part"
    part = controller.data[-1]["part"]
    assert part["name"] == "team-model-projection"
    assert part["data"]["projection_source"] == "model"
    assert part["data"]["text"] == "模型根据当前证据说明实际进展。"
    assert part["part_id"].endswith(":p1")


def test_model_projection_does_not_publish_a_second_shorter_contract_summary() -> None:
    class Controller:
        def __init__(self) -> None:
            self.data: list[dict[str, Any]] = []

        def add_data(self, value: dict[str, Any]) -> None:
            self.data.append(value)

    controller = Controller()
    bridge = GraphEventBridge(controller, run_id="team-single-projection")
    projection_id = "team-single-projection:team:plan"
    rich_text = "我已经根据任务拆分了独立证据方向，现在开始并行核验。"

    bridge.publish_model_projection(
        rich_text,
        scope="coordinator",
        collaboration_id="team-1",
        phase="planning",
        kind="plan",
        projection_id=projection_id,
    )
    bridge.publish_model_projection(
        "开始并行核验。",
        scope="coordinator",
        collaboration_id="team-1",
        phase="planning",
        kind="plan",
        projection_id=projection_id,
    )

    assert len(controller.data) == 1
    assert controller.data[0]["part"]["data"]["text"] == rich_text


def test_client_stage_projection_keeps_team_worker_starts_without_nested_envelopes() -> None:
    worker_stages = [
        {
            "event": "agent_stage",
            "engine": "langgraph_agent_loop",
            "run_id": "team-client-projection",
            "schema_version": "team.v1",
            "collaboration_id": "team-1",
            "sequence": index,
            "scope": "expert",
            "stage": "planning",
            "status": "started",
            "action_id": f"team-1:{role}:{role}-task:worker",
            "summary": f"{role}方向开始核验",
            "details": {
                "team_id": "team-1",
                "task_id": f"{role}-task",
                "agent_id": f"team-1:{role}:{role}-task:attempt-1",
                "expert_id": role,
                "user_message": f"我现在开始核验{role}方向的信息。",
                "collaboration_event": {
                    "safe_details": {"repeated": "x" * 20_000},
                },
            },
        }
        for index, role in enumerate(("market", "fundamental", "news"), start=1)
    ]

    projected = project_stage_history_for_client(worker_stages)

    assert [item["action_id"] for item in projected] == [
        "team-1:market:market-task:worker",
        "team-1:fundamental:fundamental-task:worker",
        "team-1:news:news-task:worker",
    ]
    assert all("collaboration_event" not in item.get("details", {}) for item in projected)
    assert all(item["details"]["expert_id"] for item in projected)


def test_worker_model_progress_is_forwarded_to_its_expert_lane_without_child_answer_leak() -> None:
    class Controller:
        def __init__(self) -> None:
            self.texts: list[str] = []
            self.data: list[dict[str, Any]] = []

        def append_text(self, value: str) -> None:
            self.texts.append(value)

        def add_data(self, value: dict[str, Any]) -> None:
            self.data.append(value)

    controller = Controller()
    parent = GraphEventBridge(controller, run_id="team-worker-projection")
    child = TeamWorkerEventBridge(
        parent,
        team_id="team-1",
        task_id="market-task",
        agent_id="team-1:market:market-task:attempt-1",
        expert_id="market",
        attempt=1,
    )
    child.begin_model_turn(1)
    child.model_message(AIMessageChunk(content="行情工具返回了新的观察。"))
    child.commit_model_progress()
    child.begin_model_turn(2)
    child.model_message(AIMessageChunk(content="worker 内部最终回答不应直接展示"))
    child.commit_model_answer("worker 内部最终回答不应直接展示")

    assert controller.texts == []
    assert len(controller.data) == 1
    projection = controller.data[0]["part"]["data"]
    assert projection["scope"] == "expert"
    assert projection["task_id"] == "market-task"
    assert projection["agent_id"] == "team-1:market:market-task:attempt-1"
    assert projection["kind"] == "worker-progress"
    assert projection["text"] == "行情工具返回了新的观察。"


def test_reviewer_candidate_is_not_projected_as_a_second_team_answer() -> None:
    class Controller:
        def __init__(self) -> None:
            self.data: list[dict[str, Any]] = []

        def add_data(self, value: dict[str, Any]) -> None:
            self.data.append(value)

    controller = Controller()
    parent = GraphEventBridge(controller, run_id="team-reviewer-projection")
    child = TeamWorkerEventBridge(
        parent,
        team_id="team-1",
        task_id="synthesis",
        agent_id="team-1:reviewer",
        expert_id="review",
        scope="review",
    )
    child.begin_model_turn(1)
    child.model_message(AIMessageChunk(content="完整的候选综合答案"))
    child.commit_model_progress()
    child.publish_model_projection("结构化合约中的候选答案")

    assert controller.data == []


def test_worker_model_projection_waits_for_complete_tool_planning_turn() -> None:
    class Controller:
        def __init__(self) -> None:
            self.data: list[dict[str, Any]] = []

        def add_data(self, value: dict[str, Any]) -> None:
            self.data.append(value)

    controller = Controller()
    parent = GraphEventBridge(controller, run_id="team-worker-live-projection")
    child = TeamWorkerEventBridge(
        parent,
        team_id="team-1",
        task_id="market-task",
        agent_id="team-1:market:market-task:attempt-1",
        expert_id="market",
        attempt=1,
    )
    child.begin_model_turn(1)
    child.model_message(AIMessageChunk(content="我先核验行情数据，"))
    child.model_message(
        AIMessageChunk(
            content="再计算技术指标。",
            tool_call_chunks=[
                {"name": "read_realtime_quote", "args": "{}", "id": "quote", "index": 0}
            ],
        )
    )
    child.model_message(
        AIMessageChunk(
            content="同时读取K线。",
            tool_call_chunks=[{"args": "", "index": 0}],
        )
    )

    # The first tool-call chunk is not a model-turn boundary.  No partial
    # sentence may reach the lane before the provider has finished the
    # message.  The explicit final chunk commits one complete projection.
    assert controller.data == []
    child.model_message(
        AIMessageChunk(
            content="",
            tool_call_chunks=[{"args": "", "index": 0}],
            chunk_position="last",
        )
    )

    assert len(controller.data) == 1
    projection = controller.data[0]["part"]["data"]
    assert projection["text"] == "我先核验行情数据，再计算技术指标。同时读取K线。"
    assert projection["scope"] == "expert"
    assert projection["task_id"] == "market-task"


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
