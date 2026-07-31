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



"""Focused test slice 6; shared fixtures remain local to this slice."""

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
def test_truncated_final_answer_discards_partial_text_and_uses_complete_fallback():
    controller = _FakeController()
    fake_acompletion = _async_completion(
        [
            _mock_llm_chunk(content="写到一半的残稿", finish_reason="length"),
        ]
    )
    evidence = [
        {
            "tool": "get_multi_stock_snapshot",
            "result": {
                "success": True,
                "data_time": "2026-07-17T14:49:21+08:00",
                "quote_basis": "盘中实时快照（不是收盘价）",
                "items": [
                    {
                        "symbol": "300508",
                        "name": "维宏股份",
                        "quote": {"price": 38.03, "change_pct": -10.41, "pe_dynamic": -57.72, "pb_ratio": 4.97},
                        "financial": {"net_profit": -1.0, "debt_ratio_pct": 25.9},
                        "technical": {"is_stale": False},
                    }
                ],
            },
        }
    ]
    fake_cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod:
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._stream_final_answer_without_tools(
                controller,
                [{"role": "user", "content": "这些公司能买吗"}],
                fake_cfg,
                evidence=evidence,
            )

    result = asyncio.run(run())
    assert "维宏股份 (300508)" in result
    assert "写到一半的残稿" not in result
    assert all("写到一半的残稿" not in text for text in controller.texts)

def test_final_answer_rejects_claims_from_an_uncalled_evidence_dimension():
    controller = _FakeController()
    fake_acompletion = _async_completion(
        [
            _mock_llm_chunk(content="盘中价格下跌，说明主力资金仍在流出。", finish_reason="stop"),
        ]
    )
    evidence = [
        {
            "tool": "get_multi_stock_snapshot",
            "result": {
                "success": True,
                "data_time": "2026-07-17T14:49:21+08:00",
                "quote_basis": "盘中实时快照（不是收盘价）",
                "quote_is_intraday": True,
                "items": [
                    {
                        "symbol": "300508",
                        "name": "维宏股份",
                        "quote": {"price": 38.03, "change_pct": -10.41, "pe_dynamic": -57.72, "pb_ratio": 4.97},
                        "financial": {"net_profit": -1.0, "debt_ratio_pct": 25.9},
                        "technical": {"is_stale": False},
                    }
                ],
            },
        }
    ]
    fake_cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod:
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._stream_final_answer_without_tools(
                controller,
                [{"role": "user", "content": "能买吗"}],
                fake_cfg,
                evidence=evidence,
            )

    result = asyncio.run(run())
    assert "主力资金" not in result
    assert "维宏股份 (300508)" in result

def test_agent_chat_sse_normal_stream(client):
    """Happy path: agent_chat returns a 200 streaming response with content.

    DataStreamResponse JSON-encodes text, so CJK appears as \\uXXXX escapes.
    """

    async def fake_acompletion(**kwargs):
        function_name = kwargs.get("tool_choice", {}).get("function", {}).get("name")
        if function_name == "submit_intent_outline_v2":
            payload = {
                "goal": {
                    "objective": "回应问候",
                    "question_type": "direct",
                    "uncertainty_mode": "bounded",
                    "time_horizon": None,
                    "deliverables": ["直接回应用户问候"],
                    "claims": [
                        {
                            "claim_id": "answer",
                            "question": "向用户给出自然、直接的回应",
                            "required_dimensions": ["general_knowledge"],
                            "optional_dimensions": [],
                            "mandatory": True,
                        }
                    ],
                },
                "nodes": [
                    {
                        "node_id": "answer",
                        "capability": "general_response",
                        "objective": "回应问候",
                        "input_refs": [],
                        "result_selection": None,
                    }
                ],
                "needs_clarification": False,
                "clarification_question": None,
            }
        elif function_name == "submit_general_response_intent_v2":
            payload = {}
        else:
            return _AsyncChunkStream([_mock_llm_chunk(content="你好")])
        return {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": function_name,
                                    "arguments": json.dumps(payload),
                                },
                            }
                        ],
                    },
                }
            ],
        }

    marker = "你好".encode("unicode_escape")
    with (
        patch(
            "api.v1.endpoints.agent.chat._get_llm_config",
            return_value={"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None},
        ),
        patch("api.v1.endpoints.agent.chat.litellm") as llm_mod,
        patch("api.v1.endpoints.agent.chat._flush_substreams", new=AsyncMock()),
    ):
        llm_mod.acompletion = fake_acompletion
        with client.stream(
            "POST", "/api/v1/agent/chat", json={"messages": [{"role": "user", "content": "hi"}]}
        ) as response:
            assert response.status_code == 200
            body = b""
            for chunk in response.iter_bytes():
                body += chunk
                if marker in body:
                    break

    assert marker in body

def test_agent_chat_sse_llm_failure_still_returns_stream(client):
    """Planner failure still returns a 200 stream and never opens data tools."""

    async def fake_acompletion(**kwargs):
        raise RuntimeError("LLM down")

    marker = "当前回答服务暂时不可用".encode("unicode_escape")
    with (
        patch(
            "api.v1.endpoints.agent.chat._get_llm_config",
            return_value={"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None},
        ),
        patch("api.v1.endpoints.agent.chat.litellm") as llm_mod,
        patch("api.v1.endpoints.agent.chat._flush_substreams", new=AsyncMock()),
    ):
        llm_mod.acompletion = fake_acompletion
        with client.stream(
            "POST", "/api/v1/agent/chat", json={"messages": [{"role": "user", "content": "hi"}]}
        ) as response:
            assert response.status_code == 200
            body = b""
            for chunk in response.iter_bytes():
                body += chunk
                if marker in body:
                    break

    assert marker in body

def test_normalize_passthrough_string_content():
    """字符串 content 的 user/system 透传，role 缺省补 user。"""
    out = chat_mod._normalize_incoming_messages(
        [
            {"role": "user", "content": "你好"},
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi", "id": "u1"},
        ]
    )
    assert out[0] == {"role": "user", "content": "你好"}
    assert out[1] == {"role": "system", "content": "sys"}
    # 保留非 role 字段
    assert out[2]["role"] == "user"
    assert out[2]["content"] == "hi"
    assert out[2]["id"] == "u1"

def test_normalize_user_aisdk_array_to_string():
    """user 数组 content → 拼 text 为字符串。"""
    out = chat_mod._normalize_incoming_messages(
        [
            {"role": "user", "content": [{"type": "text", "text": "分析茅台"}, {"type": "text", "text": "行情"}]},
        ]
    )
    assert out[0] == {"role": "user", "content": "分析茅台\n行情"}

def test_normalize_assistant_with_tool_calls():
    """assistant 含 text + 多个 tool-call → content 字符串 + tool_calls 数组。"""
    out = chat_mod._normalize_incoming_messages(
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "正在查询"},
                    {
                        "type": "tool-call",
                        "toolCallId": "call_a",
                        "toolName": "get_kline",
                        "input": {"symbol": "600519"},
                    },
                    {"type": "tool-call", "toolCallId": "call_b", "toolName": "get_realtime_quotes", "input": {}},
                ],
            },
        ]
    )
    msg = out[0]
    assert msg["role"] == "assistant"
    assert msg["content"] == "正在查询"
    assert len(msg["tool_calls"]) == 2
    tc0 = msg["tool_calls"][0]
    assert tc0["id"] == "call_a"
    assert tc0["type"] == "function"
    assert tc0["function"]["name"] == "get_kline"
    assert json.loads(tc0["function"]["arguments"]) == {"symbol": "600519"}
    # 空 input → "{}"
    assert msg["tool_calls"][1]["function"]["arguments"] == "{}"

def test_normalize_assistant_drops_reasoning():
    """assistant 的 reasoning part 被丢弃，不进 content 也不进 tool_calls。"""
    out = chat_mod._normalize_incoming_messages(
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "reasoning", "text": "我需要先查行情"},
                    {"type": "text", "text": "查询中"},
                ],
            },
        ]
    )
    assert out[0] == {"role": "assistant", "content": "查询中"}

def test_normalize_tool_result_to_openai():
    """AI SDK tool-result(json) → OpenAI {tool_call_id, content:json字符串}。"""
    out = chat_mod._normalize_incoming_messages(
        [
            {
                "role": "tool",
                "content": [
                    {
                        "type": "tool-result",
                        "toolCallId": "call_x",
                        "toolName": "get_kline",
                        "output": {"type": "json", "value": {"symbol": "600519", "latest": {"close": 1500}}},
                    },
                ],
            },
        ]
    )
    msg = out[0]
    assert msg["role"] == "tool"
    assert msg["tool_call_id"] == "call_x"
    payload = json.loads(msg["content"])
    assert payload["symbol"] == "600519"
    assert payload["latest"] == {"close": 1500}

def test_normalize_tool_result_error_prefix():
    """error-json / isError → content 带 [工具执行错误] 前缀。"""
    # error-json
    out1 = chat_mod._normalize_incoming_messages(
        [
            {
                "role": "tool",
                "content": [
                    {
                        "type": "tool-result",
                        "toolCallId": "c1",
                        "output": {"type": "error-json", "value": {"error": "boom"}},
                    },
                ],
            },
        ]
    )
    assert out1[0]["content"].startswith("[工具执行错误] ")
    # isError
    out2 = chat_mod._normalize_incoming_messages(
        [
            {
                "role": "tool",
                "content": [
                    {
                        "type": "tool-result",
                        "toolCallId": "c2",
                        "isError": True,
                        "output": {"type": "json", "value": {"msg": "fail"}},
                    },
                ],
            },
        ]
    )
    assert out2[0]["content"].startswith("[工具执行错误] ")

def test_normalize_history_tool_detail_arrays_slimmed():
    """从前端回传的历史 tool 结果明细数组被裁剪（_slim_tool_content 生效）。"""
    out = chat_mod._normalize_incoming_messages(
        [
            {
                "role": "tool",
                "content": [
                    {
                        "type": "tool-result",
                        "toolCallId": "call_hist",
                        "toolName": "get_kline",
                        "output": {
                            "type": "json",
                            "value": {
                                "symbol": "600519",
                                "count": 60,
                                "latest": {"date": "2026-07-07", "close": 1500.0},
                                "recent": [{"date": f"2026-07-0{i}"} for i in range(1, 6)],
                            },
                        },
                    },
                ],
            },
        ]
    )
    payload = json.loads(out[0]["content"])
    assert payload.get("_slimmed") is True
    assert "recent" not in payload  # 明细被裁
    assert payload["latest"] == {"date": "2026-07-07", "close": 1500.0}  # 摘要保留

def test_normalize_passthrough_openai_format():
    """已是 OpenAI 格式（tool 有 tool_call_id、assistant 有 tool_calls）→ 透传不转。"""
    openai_tool = {"role": "tool", "tool_call_id": "call_z", "content": '{"a":1}'}
    openai_asst = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "call_z", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
    }
    out = chat_mod._normalize_incoming_messages([openai_tool, openai_asst])
    assert out[0] is openai_tool
    assert out[1] is openai_asst

def test_normalize_robustness():
    """非 dict 跳过；未知 part type 跳过；缺 toolCallId 生成默认 id。"""
    out = chat_mod._normalize_incoming_messages(
        [
            "not a dict",
            {"role": "assistant", "content": [{"type": "unknown-type", "foo": "bar"}]},
            {"role": "tool", "content": [{"type": "tool-result", "output": {"type": "json", "value": {"x": 1}}}]},
        ]
    )
    # 非 dict 被跳过
    assert len(out) == 2
    # 未知 part：assistant 无 text 无 tool-call → content None
    assert out[0] == {"role": "assistant", "content": None}
    # 缺 toolCallId：生成 call_ 开头的 id；历史 tool 结果会被裁剪（加 _slimmed 标记）
    assert out[1]["tool_call_id"].startswith("call_")
    payload = json.loads(out[1]["content"])
    assert payload["x"] == 1
    assert payload.get("_slimmed") is True
