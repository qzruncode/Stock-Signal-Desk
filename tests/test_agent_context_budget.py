"""Targeted tests for final model-request context budgeting."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from langchain.agents.middleware import ModelRequest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.runtime import Runtime
from pydantic import ConfigDict, Field

from src.agent.langgraph_runtime.context import ContextBudgetMiddleware, completed_answers_as_context
from src.agent.model_runtime import ModelContextWindowExceededError


class CountingChatModel(BaseChatModel):
    """Small model double with deterministic message-token accounting."""

    llm_config: dict[str, Any] = Field(default_factory=dict, exclude=True)
    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def _llm_type(self) -> str:
        return "counting_context_model"

    def get_num_tokens_from_messages(
        self,
        messages: list[BaseMessage],
        tools: list[Any] | None = None,
    ) -> int:
        return sum(len(str(message.content)) for message in messages)

    def bind_tools(self, tools: list[Any], *, tool_choice: Any | None = None, **kwargs: Any) -> Any:
        return self.bind(tools=tools, tool_choice=tool_choice, **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])


class EventRecorder:
    def __init__(self) -> None:
        self.stages: list[tuple[Any, ...]] = []

    def stage(self, *args: Any, **kwargs: Any) -> None:
        self.stages.append(args)


def _request(model: CountingChatModel, messages: list[BaseMessage], events: EventRecorder) -> ModelRequest:
    return ModelRequest(
        model=model,
        messages=messages,
        system_message=SystemMessage(content="system"),
        runtime=Runtime(context=SimpleNamespace(events=events)),
    )


def test_prior_typed_answer_becomes_context_without_touching_current_tool_pairs():
    from tests.test_langgraph_agent_runtime import _structured_output_call, _named_tool_call

    old_answer = _structured_output_call("old-answer", [{"kind": "answer", "content": "上一轮结论"}], profile="general")
    current_call = _named_tool_call("current", "search_source", {"query": "本轮"})
    current_answer = _structured_output_call(
        "new-answer", [{"kind": "answer", "content": "本轮候选"}], profile="general"
    )
    messages = [
        HumanMessage(content="旧问题"),
        old_answer,
        ToolMessage(content="Returning structured response", tool_call_id="old-answer"),
        HumanMessage(content="新问题"),
        current_call,
        ToolMessage(content="真实本轮观察", tool_call_id="current"),
        current_answer,
        ToolMessage(content="本轮修订反馈", tool_call_id="new-answer", status="error"),
    ]
    projected = completed_answers_as_context(messages)
    assert projected[1].content == "上一轮结论" and not projected[1].tool_calls
    assert projected[2:] == messages[3:]
    assert messages[1].tool_calls and len(messages) == 8


def test_context_budget_trims_transient_messages_and_preserves_canonical_input(monkeypatch):
    monkeypatch.setenv("AGENT_CONTEXT_OUTPUT_TOKENS", "256")
    monkeypatch.setenv("AGENT_CONTEXT_SAFETY_TOKENS", "256")
    model = CountingChatModel(profile={"max_input_tokens": 2_000}, llm_config={"max_tokens": 256})
    original = [
        HumanMessage(content="old user " + "x" * 700, id="old-user"),
        AIMessage(content="old answer " + "y" * 700, id="old-answer"),
        HumanMessage(content="latest " + "z" * 400, id="latest"),
    ]
    events = EventRecorder()
    request = _request(model, original, events)
    received: list[ModelRequest] = []

    async def handler(next_request: ModelRequest) -> ModelRequest:
        received.append(next_request)
        return next_request

    result = asyncio.run(ContextBudgetMiddleware().awrap_model_call(request, handler))

    assert result is received[0]
    assert request.messages == original
    assert len(received[0].messages) < len(original)
    assert received[0].messages[-1].content.startswith("latest")
    assert any(stage[:2] == ("context", "compacted") for stage in events.stages)


def test_context_budget_keeps_summary_anchor_when_it_fits(monkeypatch):
    monkeypatch.setenv("AGENT_CONTEXT_OUTPUT_TOKENS", "256")
    monkeypatch.setenv("AGENT_CONTEXT_SAFETY_TOKENS", "256")
    model = CountingChatModel(profile={"max_input_tokens": 2_000}, llm_config={"max_tokens": 256})
    original = [
        HumanMessage(
            content="conversation summary with the early constraint",
            id="summary",
            additional_kwargs={"lc_source": "summarization"},
        ),
        HumanMessage(content="old context " + "x" * 700, id="old-context"),
        AIMessage(content="old answer " + "y" * 700, id="old-answer"),
        HumanMessage(content="latest " + "z" * 400, id="latest"),
    ]
    request = _request(model, original, EventRecorder())
    received: list[ModelRequest] = []

    async def handler(next_request: ModelRequest) -> ModelRequest:
        received.append(next_request)
        return next_request

    asyncio.run(ContextBudgetMiddleware().awrap_model_call(request, handler))

    assert received[0].messages[0].id == "summary"
    assert received[0].messages[-1].id == "latest"


def test_context_budget_reports_when_latest_message_cannot_fit(monkeypatch):
    monkeypatch.setenv("AGENT_CONTEXT_OUTPUT_TOKENS", "256")
    monkeypatch.setenv("AGENT_CONTEXT_SAFETY_TOKENS", "256")
    model = CountingChatModel(profile={"max_input_tokens": 1_000}, llm_config={"max_tokens": 256})
    request = _request(
        model,
        [HumanMessage(content="too large " + "x" * 1_000, id="latest")],
        EventRecorder(),
    )

    async def handler(value: ModelRequest) -> ModelRequest:
        return value

    with pytest.raises(ModelContextWindowExceededError) as raised:
        asyncio.run(ContextBudgetMiddleware().awrap_model_call(request, handler))

    assert raised.value.context_window == 1_000
    assert raised.value.message_count == 1
