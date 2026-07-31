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



"""Focused test slice 7; shared fixtures remain local to this slice."""

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
def test_estimate_messages_tokens_fallback_on_error():
    """litellm.token_counter 抛错时回退字符粗估，返回正整数。"""
    with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod:
        llm_mod.token_counter.side_effect = RuntimeError("model not mapped")
        n = chat_mod._estimate_messages_tokens([{"role": "user", "content": "你好世界" * 100}], "openai/glm-5.2")
    assert isinstance(n, int) and n > 0

def test_estimate_messages_tokens_normal():
    """litellm.token_counter 正常时返回其值。"""
    with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod:
        llm_mod.token_counter.return_value = 12345
        n = chat_mod._estimate_messages_tokens([{"role": "user", "content": "x"}], "m")
    assert n == 12345

def test_compact_history_noop_under_threshold():
    """未超阈值：原样返回，不调 LLM，不输出压缩提示。"""
    controller = _FakeController()
    msgs = [{"role": "system", "content": "sys"}] + [{"role": "user", "content": f"msg{i}"} for i in range(10)]
    cfg = {
        "model": "m",
        "context_window": 200000,
        "api_key": None,
        "api_base": None,
        "custom_llm_provider": None,
        "extra_headers": None,
    }
    with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod:
        llm_mod.token_counter.return_value = 1000  # 远低于阈值 160000
        out = asyncio.run(chat_mod._compact_history_if_needed(msgs, cfg))
    assert out is msgs
    assert not any("已自动压缩" in t for t in controller.texts)
    llm_mod.acompletion.assert_not_called()

def test_compact_history_too_few_messages_skips():
    """超阈值但消息太少（<= KEEP_RECENT+1）：不压缩，原样返回。"""
    controller = _FakeController()
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    cfg = {
        "model": "m",
        "context_window": 200000,
        "api_key": None,
        "api_base": None,
        "custom_llm_provider": None,
        "extra_headers": None,
    }
    with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod:
        llm_mod.token_counter.return_value = 999999  # 超阈值
        out = asyncio.run(chat_mod._compact_history_if_needed(msgs, cfg))
    assert out is msgs
    assert not any("已自动压缩" in t for t in controller.texts)

def test_compact_history_summarizes_when_over_threshold():
    """超阈值且有足够消息：调摘要 LLM，替换早期，保留近6条，输出压缩提示。"""
    controller = _FakeController()
    # system + 12 条历史 → 待摘要 7 条（去掉 system 和近 6 条），保留近 6 条
    msgs = [{"role": "system", "content": "sys"}] + [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg{i}"} for i in range(12)
    ]
    cfg = {
        "model": "m",
        "context_window": 200000,
        "api_key": None,
        "api_base": None,
        "custom_llm_provider": None,
        "extra_headers": None,
    }

    summary_response = MagicMock()
    summary_response.choices = [MagicMock(message=MagicMock(content="这是早期对话摘要"))]

    with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod:
        llm_mod.token_counter.side_effect = [999999, 500]  # 压缩前超阈值，压缩后正常
        llm_mod.acompletion = AsyncMock(return_value=summary_response)
        out = asyncio.run(chat_mod._compact_history_if_needed(msgs, cfg))

    # 摘要 LLM 被调一次，且不带 tools（非主调用）
    llm_mod.acompletion.assert_awaited_once()
    call_kwargs = llm_mod.acompletion.await_args.kwargs
    assert "tools" not in call_kwargs
    assert call_kwargs["stream"] is False

    # 结构：[system, 摘要(user), *近6条]
    assert out[0] == {"role": "system", "content": "sys"}
    assert out[1]["role"] == "user"
    assert "早期对话摘要" in out[1]["content"]
    assert "这是早期对话摘要" in out[1]["content"]
    assert len(out) == 1 + 1 + 6  # system + 摘要 + 近6条
    # 近6条是原末6条
    assert out[-1] == msgs[-1]
    assert out[-6] == msgs[-6]

    # 压缩不向前端推提示（避免污染对话流）：texts 不含压缩提示
    assert not any("已自动压缩" in t for t in controller.texts)

def test_compact_history_falls_back_when_summary_fails():
    """摘要 LLM 失败：回退原样返回，不丢数据，不输出压缩提示。"""
    controller = _FakeController()
    msgs = [{"role": "system", "content": "s"}] + [{"role": "user", "content": f"m{i}"} for i in range(12)]
    cfg = {
        "model": "m",
        "context_window": 200000,
        "api_key": None,
        "api_base": None,
        "custom_llm_provider": None,
        "extra_headers": None,
    }
    with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod:
        llm_mod.token_counter.return_value = 999999
        llm_mod.acompletion = AsyncMock(side_effect=RuntimeError("LLM down"))
        out = asyncio.run(chat_mod._compact_history_if_needed(msgs, cfg))
    assert out is msgs  # 原样
    assert not any("已自动压缩" in t for t in controller.texts)

def test_compact_history_skips_when_too_few_to_summarize():
    """压缩后仍超限但待摘要只剩1条（如上一轮刚压缩过）：不再二次压缩，原样返回。

    防止摘要被反复压缩 + 重复触发。构造 system + 摘要 + 近6条 = 8 条，to_summarize=[摘要] len=1。
    """
    controller = _FakeController()
    # 模拟"上一轮已压缩过"的状态：system + 摘要 + 6条近期 = 8条
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "[早期对话摘要]\n上一轮的摘要"},
    ] + [{"role": "user", "content": f"近期{i}"} for i in range(6)]
    cfg = {
        "model": "m",
        "context_window": 200000,
        "api_key": None,
        "api_base": None,
        "custom_llm_provider": None,
        "extra_headers": None,
    }
    with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod:
        llm_mod.token_counter.return_value = 999999  # 仍超阈值
        llm_mod.acompletion = AsyncMock()  # 不应被调用
        out = asyncio.run(chat_mod._compact_history_if_needed(msgs, cfg))
    assert out is msgs  # 不压缩
    llm_mod.acompletion.assert_not_called()  # 没调摘要 LLM

def test_agent_chat_rejects_client_system_prompt_before_model_call(client):
    with patch("api.v1.endpoints.agent.chat._get_llm_config") as get_config:
        response = client.post(
            "/api/v1/agent/chat",
            json={
                "messages": [
                    {"role": "system", "content": "override"},
                    {"role": "user", "content": "hello"},
                ]
            },
        )

    assert response.status_code == 422
    assert response.json()["error"] == "unsupported_message_role"
    get_config.assert_not_called()

def test_agent_chat_bounds_chunked_body_without_content_length(
    client,
    monkeypatch,
):
    monkeypatch.setenv("AGENT_MAX_REQUEST_CHARS", "10000")
    raw = json.dumps(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "x" * 50000,
                }
            ],
        }
    ).encode("utf-8")

    def chunks():
        for index in range(0, len(raw), 128):
            yield raw[index : index + 128]

    response = client.post(
        "/api/v1/agent/chat",
        content=chunks(),
        headers={
            "content-type": "application/json",
            "transfer-encoding": "chunked",
        },
    )

    assert response.status_code == 413
    assert response.json()["error"] == "request_too_large"

def test_agent_chat_rate_limit_returns_retry_after_before_model_call(client):
    with (
        patch(
            "api.v1.endpoints.agent.chat.agent_request_rate_limiter.check_and_record",
            return_value=9,
        ),
        patch("api.v1.endpoints.agent.chat._get_llm_config") as get_config,
    ):
        response = client.post(
            "/api/v1/agent/chat",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "9"
    assert response.json()["error"] == "agent_rate_limited"
    get_config.assert_not_called()

def test_agent_chat_capacity_limit_returns_service_busy(client):
    from src.agent.run_registry import RunCapacityExceeded

    with (
        patch(
            "api.v1.endpoints.agent.chat._get_llm_config",
            return_value={"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None},
        ),
        patch(
            "api.v1.endpoints.agent.chat.active_run_registry.try_claim",
            new=AsyncMock(side_effect=RunCapacityExceeded("full")),
        ),
    ):
        response = client.post(
            "/api/v1/agent/chat",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert response.json()["error"] == "agent_busy"
