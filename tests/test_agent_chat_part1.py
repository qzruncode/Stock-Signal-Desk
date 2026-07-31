# -*- coding: utf-8 -*-
"""Agent chat endpoint tests — standard-task pipeline and SSE stream.

Covers api.v1.endpoints.agent.chat:
- _get_llm_config: model/api_key/api_base resolution (normal / boundary paths)
- _run_standard_task_pipeline: fixed workflows and policy-validated execution
- _stream_final_answer_without_tools: normal output, LLM failure fallback, empty content
- agent_chat SSE: HTTP-level normal stream and LLM-call failure path

Async tests use asyncio.run (the project does not use pytest-asyncio).
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.v1.endpoints.agent import chat as chat_mod
import src.auth as auth



"""Focused test slice 1; shared fixtures remain local to this slice."""

@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)

@pytest.fixture(autouse=True)
def disable_auth():
    auth._auth_enabled = None
    with (
        patch("api.middlewares.auth.is_auth_enabled", return_value=False),
        patch("src.auth.is_auth_enabled", return_value=False),
        patch.object(
            chat_mod,
            "execute_tool_isolated",
            side_effect=lambda name, arguments, **_kwargs: chat_mod._registry.execute(name, arguments),
        ),
    ):
        yield
    auth._auth_enabled = None

def _mock_llm_chunk(content=None, tool_calls=None, finish_reason=None, reasoning_content=None):
    """Build a litellm streaming chunk mock."""
    delta = MagicMock()
    delta.content = content
    delta.tool_calls = tool_calls
    delta.reasoning_content = reasoning_content
    chunk = MagicMock()
    chunk.choices = [MagicMock(delta=delta, finish_reason=finish_reason)]
    return chunk

class _AsyncChunkStream:
    """Wrap a list of chunks into an async iterator for `async for`."""

    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        self._iter = iter(self._chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration

def _async_completion(chunks):
    """Return an async fake acompletion that yields the given chunks."""

    async def _fake(**kwargs):
        return _AsyncChunkStream(chunks)

    return _fake

def _mock_tool_call_delta(idx=0, name="", arguments="", tc_id="call_1"):
    tc = MagicMock()
    tc.index = idx
    tc.id = tc_id
    tc.function = MagicMock()
    tc.function.name = name
    tc.function.arguments = arguments
    return tc

class _FakeController:
    """Minimal RunController substitute capturing appended text."""

    def __init__(self):
        self.texts = []
        self.reasoning = []
        self.data = []
        self._stream_tasks = []
        self.tool_calls = []

    def append_text(self, text):
        self.texts.append(text)

    def append_reasoning(self, text):
        self.reasoning.append(text)

    def add_data(self, value):
        self.data.append(value)

    async def add_tool_call(self, name, tool_call_id=None):
        self.tool_calls.append((name, tool_call_id))
        tool = MagicMock()
        tool.append_args_text = MagicMock()
        tool.set_response = MagicMock()
        return tool
def test_pipeline_contract_failure_degrades_to_a_no_tool_answer() -> None:
    controller = _FakeController()
    state = {}
    error = chat_mod.OrchestratorV2Error(
        chat_mod.AgentErrorCode.PLANNER_SCHEMA_INVALID,
        "capability contract rejected result_selection",
        task_id="discover",
    )

    with (
        patch.object(
            chat_mod,
            "plan_intent_graph_v2",
            new=AsyncMock(side_effect=error),
        ),
        patch.object(
            chat_mod,
            "_stream_final_answer_without_tools",
            new=AsyncMock(return_value="这是不依赖实时数据的降级回答。"),
        ),
    ):
        result = asyncio.run(
            chat_mod._run_standard_task_pipeline(
                controller,
                [{"role": "user", "content": "人形机器人哪些领域最受益？"}],
                {"model": "test-model"},
                "",
                state=state,
                run_id="run-contract-failed",
            )
        )

    assert result == "这是不依赖实时数据的降级回答。"
    assert state["_run_status"] == "partial"
    assert state["_run_error_code"] == "planner_schema_invalid"
    assert chat_mod._terminal_run_status(state) == "partial"
    assert chat_mod._terminal_run_status({"_run_status": "partial"}) == "partial"
    assert chat_mod._terminal_run_status({}) == "completed"

def test_pipeline_runs_one_safe_goal_recovery_then_returns_best_effort(
    monkeypatch,
) -> None:
    controller = _FakeController()
    state = {}
    goal = {
        "objective": "研判未来一至六个月市场主线",
        "question_type": "forecast",
        "uncertainty_mode": "scenario",
        "time_horizon": "未来一至六个月",
        "deliverables": ["候选主线排序", "成立条件与失效信号"],
        "claims": [
            {
                "claim_id": "mainline",
                "question": "未来市场主线及其验证条件是什么",
                "required_dimensions": [
                    "market_mainline",
                    "research_consensus",
                    "macro_policy",
                    "industry_structure",
                ],
                "optional_dimensions": [],
                "mandatory": True,
            }
        ],
    }
    submitted_outlines = 0

    def response(function_name, payload):
        return {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": function_name,
                                    "arguments": json.dumps(
                                        payload,
                                        ensure_ascii=False,
                                    ),
                                },
                            }
                        ],
                    },
                }
            ],
        }

    async def completion(**kwargs):
        nonlocal submitted_outlines
        function_name = kwargs["tool_choice"]["function"]["name"]
        if function_name == "submit_intent_outline_v2":
            submitted_outlines += 1
            payload = {
                "goal": goal,
                "nodes": [
                    {
                        "node_id": ("mainline" if submitted_outlines == 1 else "repair_1_macro"),
                        "capability": ("market_mainline_research" if submitted_outlines == 1 else "macro_analysis"),
                        "objective": "补充宏观政策证据",
                        "input_refs": [],
                        "result_selection": None,
                    }
                ],
                "needs_clarification": False,
                "clarification_question": None,
            }
            return response(function_name, payload)
        if function_name == "verify_intent_outline_v2":
            return response(
                function_name,
                {
                    "accepted": True,
                    "confidence": 0.95,
                    "missing_capabilities": [],
                    "extraneous_node_ids": [],
                    "resource_issues": [],
                    "rationale": "目标与市场主线能力匹配。",
                },
            )
        if function_name == "submit_macro_analysis_intent_v2":
            return response(
                function_name,
                {
                    "indicators": ["GDP"],
                    "periods": 4,
                    "bond_yield": False,
                    "country": None,
                    "term": None,
                    "days": None,
                    "monetary_operations": False,
                    "instrument": None,
                    "research_query": None,
                    "research_subjects": [],
                    "limit": None,
                },
            )
        raise AssertionError(function_name)

    def execute_dispatch(_self, request, **_kwargs):
        if request.tool_name == "prepare_market_mainline_snapshot":
            return {
                "success": False,
                "partial": False,
                "available": False,
                "errors": ["primary source unavailable"],
                "warnings": [],
            }
        assert request.tool_name == "get_macro_indicator"
        return {
            "success": True,
            "partial": False,
            "indicator": "GDP",
            "items": [],
            "errors": [],
            "warnings": [],
        }

    monkeypatch.setenv("AGENT_GOAL_MAX_REVISIONS", "1")
    with (
        patch.object(
            chat_mod.litellm,
            "acompletion",
            new=completion,
        ),
        patch.object(
            chat_mod.ToolDispatcher,
            "execute",
            new=execute_dispatch,
        ),
        patch.object(
            chat_mod,
            "_stream_final_answer_without_tools",
            new=AsyncMock(return_value="基于现有证据的条件化主线研判。"),
        ),
    ):
        result = asyncio.run(
            chat_mod._run_standard_task_pipeline(
                controller,
                [{"role": "user", "content": "未来市场主线会是什么？"}],
                {"model": "test-model"},
                state=state,
                run_id="run-goal-recovery",
            )
        )

    assert result == "基于现有证据的条件化主线研判。"
    goal_state = state["_terminal_trace"]["goal_state"]
    assert goal_state["plan_revision"] == 1
    assert goal_state["evaluation"]["disposition"] == "best_effort"
    assert goal_state["evaluation"]["terminal_reason"] == "budget_exhausted"
    assert goal_state["attempted_capabilities"] == [
        "market_mainline_research",
        "macro_analysis",
    ]
    assert [name for name, _ in controller.tool_calls] == [
        "prepare_market_mainline_snapshot",
        "get_macro_indicator",
    ]

def test_agent_execution_has_no_retry_classifier() -> None:
    assert not hasattr(chat_mod, "_structured_tool_failure_code")

def test_structured_completion_streams_provider_reasoning_and_rebuilds_tool_call():
    controller = _FakeController()
    received_kwargs = {}

    async def completion(**kwargs):
        received_kwargs.update(kwargs)
        return _AsyncChunkStream(
            [
                _mock_llm_chunk(reasoning_content="先识别用户要比较的产业领域。"),
                _mock_llm_chunk(
                    tool_calls=[
                        _mock_tool_call_delta(
                            name="submit_industry_research_intent_v2",
                            arguments='{"themes":',
                        ),
                    ]
                ),
                _mock_llm_chunk(
                    tool_calls=[
                        _mock_tool_call_delta(
                            name="",
                            arguments='["人形机器人"]}',
                            tc_id="",
                        ),
                    ]
                ),
            ]
        )

    response = asyncio.run(
        chat_mod._stream_structured_model_completion(
            controller,
            completion,
            stream=False,
            tool_choice={
                "type": "function",
                "function": {"name": "submit_industry_research_intent_v2"},
            },
        )
    )

    assert received_kwargs["stream"] is True
    assert "分析过程都必须使用简体中文" in received_kwargs["messages"][0]["content"]
    tool_call = response["choices"][0]["message"]["tool_calls"][0]
    assert tool_call["function"] == {
        "name": "submit_industry_research_intent_v2",
        "arguments": '{"themes":["人形机器人"]}',
    }
    reasoning = "".join(controller.reasoning)
    assert "模型可见分析 · submit_industry_research_intent_v2" in reasoning
    assert "先识别用户要比较的产业领域。" in reasoning

def test_structured_completion_coalesces_small_reasoning_deltas():
    controller = _FakeController()

    async def completion(**_kwargs):
        return _AsyncChunkStream(
            [
                *[_mock_llm_chunk(reasoning_content="分析") for _ in range(300)],
                _mock_llm_chunk(content="{}"),
            ]
        )

    asyncio.run(
        chat_mod._stream_structured_model_completion(
            controller,
            completion,
            messages=[],
        )
    )

    reasoning = "".join(controller.reasoning)
    assert reasoning.endswith("分析" * 300 + "\n")
    assert len(controller.reasoning) < 10

def test_structured_completion_hides_provider_reasoning_that_ignores_chinese():
    controller = _FakeController()

    async def completion(**_kwargs):
        return _AsyncChunkStream(
            [
                _mock_llm_chunk(
                    reasoning_content=(
                        "The user asks for a market forecast. " "I will inspect the capability catalog."
                    ),
                ),
                _mock_llm_chunk(content="{}"),
            ]
        )

    response = asyncio.run(
        chat_mod._stream_structured_model_completion(
            controller,
            completion,
            messages=[],
        )
    )

    assert response["choices"][0]["message"]["content"] == "{}"
    assert response["choices"][0]["message"]["reasoning_content"]
    assert "The user asks" not in "".join(controller.reasoning)
    assert "模型可见分析" not in "".join(controller.reasoning)

def test_visible_reasoning_language_contract_preserves_existing_system_prompt():
    original_messages = [
        {"role": "system", "content": "保持结构化输出。"},
        {"role": "user", "content": "分析人形机器人。"},
    ]

    normalized = chat_mod._with_chinese_visible_reasoning(original_messages)

    assert normalized is not original_messages
    assert normalized[0]["content"].startswith("保持结构化输出。")
    assert "分析过程都必须使用简体中文" in normalized[0]["content"]
    assert normalized[1] == original_messages[1]
    assert original_messages[0]["content"] == "保持结构化输出。"

def test_structured_completion_rebuilds_json_content_without_a_tool_choice():
    controller = _FakeController()

    async def completion(**kwargs):
        assert kwargs["stream"] is True
        assert "tool_choice" not in kwargs
        assert "tools" not in kwargs
        return _AsyncChunkStream(
            [
                _mock_llm_chunk(reasoning_content="只修复结构化传输。"),
                _mock_llm_chunk(content='{"items":['),
                _mock_llm_chunk(content=('{"board_id":"BK1100","role_id":"reducer","tier":1}]}')),
            ]
        )

    response = asyncio.run(
        chat_mod._stream_structured_model_completion(
            controller,
            completion,
            stream=False,
            messages=[],
        )
    )

    message = response["choices"][0]["message"]
    assert message["tool_calls"] == []
    assert message["content"] == ('{"items":[{"board_id":"BK1100",' '"role_id":"reducer","tier":1}]}')
    assert message["reasoning_content"] == "只修复结构化传输。"

def test_structured_completion_emits_heartbeat_while_model_has_no_delta():
    controller = _FakeController()

    class DelayedStream:
        def __init__(self):
            self._sent = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._sent:
                raise StopAsyncIteration
            self._sent = True
            await asyncio.sleep(0.03)
            return _mock_llm_chunk(content="{}")

    async def completion(**_kwargs):
        return DelayedStream()

    with patch.object(chat_mod, "MODEL_STREAM_HEARTBEAT_SECONDS", 0.005):
        asyncio.run(
            chat_mod._stream_structured_model_completion(
                controller,
                completion,
                tool_choice={
                    "type": "function",
                    "function": {"name": "submit_intent_outline_v2"},
                },
            )
        )

    assert "已等待" in "".join(controller.reasoning)
