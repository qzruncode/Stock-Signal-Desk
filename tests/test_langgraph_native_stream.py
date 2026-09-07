"""Native LangChain streaming and append-only publication contracts."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from src.agent.langgraph_runtime.events import GraphEventBridge
from src.agent.langgraph_runtime.model import LiteLLMChatModel, LiteLLMGateway


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
                                "function": {"name": "search_source", "arguments": "{\"query\":\""},
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
                                "function": {"arguments": "测试\"}"},
                            }
                        ],
                        "reasoning_content": "内部推理不会进入用户答案",
                    }
                }
            ]
        },
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ]


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
