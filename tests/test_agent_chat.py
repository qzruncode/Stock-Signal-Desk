# -*- coding: utf-8 -*-
"""Agent chat endpoint tests — LLM config, ReAct loop, and SSE stream.

Covers api.v1.endpoints.agent.chat:
- _get_llm_config: model/api_key/api_base resolution (normal / boundary paths)
- _run_react_loop: no-tool-call exit, tool-call iteration, unknown tool, LLM failure
- _stream_final_answer_without_tools: normal output, LLM failure fallback, empty content
- agent_chat SSE: HTTP-level normal stream and LLM-call failure path

Async tests use asyncio.run (the project does not use pytest-asyncio).
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.v1.endpoints.agent import chat as chat_mod
import src.auth as auth


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture(autouse=True)
def disable_auth():
    auth._auth_enabled = None
    with patch("api.middlewares.auth.is_auth_enabled", return_value=False), \
         patch("src.auth.is_auth_enabled", return_value=False):
        yield
    auth._auth_enabled = None


def _mock_llm_chunk(content=None, tool_calls=None):
    """Build a litellm streaming chunk mock."""
    delta = MagicMock()
    delta.content = content
    delta.tool_calls = tool_calls
    chunk = MagicMock()
    chunk.choices = [MagicMock(delta=delta)]
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
        self._stream_tasks = []

    def append_text(self, text):
        self.texts.append(text)

    async def add_tool_call(self, name, tool_call_id=None):
        tool = MagicMock()
        tool.append_args_text = MagicMock()
        tool.set_response = MagicMock()
        return tool


# ---------------------------------------------------------------------------
# _get_llm_config
# ---------------------------------------------------------------------------

def _make_config(**overrides):
    cfg = MagicMock()
    cfg.litellm_model = overrides.get("litellm_model", "gpt-4o")
    cfg.openai_base_url = overrides.get("openai_base_url", None)
    cfg.llm_model_list = overrides.get("llm_model_list", [])
    return cfg


def test_get_llm_config_uses_litellm_model_default():
    with patch("api.v1.endpoints.agent.chat.get_config", return_value=_make_config()), \
         patch("api.v1.endpoints.agent.chat.get_api_keys_for_model", return_value=[]), \
         patch("api.v1.endpoints.agent.chat.extra_litellm_params", return_value={}):
        cfg = chat_mod._get_llm_config()
    assert cfg["model"] == "gpt-4o"
    assert cfg["api_key"] is None
    assert cfg["api_base"] is None


def test_get_llm_config_prefers_model_list_api_key():
    config = _make_config(
        litellm_model="gpt-4o",
        llm_model_list=[{"litellm_params": {"api_key": "sk-list", "api_base": "http://list"}}],
    )
    with patch("api.v1.endpoints.agent.chat.get_config", return_value=config), \
         patch("api.v1.endpoints.agent.chat.get_api_keys_for_model", return_value=[]), \
         patch("api.v1.endpoints.agent.chat.extra_litellm_params", return_value={}):
        cfg = chat_mod._get_llm_config()
    assert cfg["api_key"] == "sk-list"
    assert cfg["api_base"] == "http://list"


def test_get_llm_config_falls_back_to_api_keys_for_model():
    config = _make_config(litellm_model="gpt-4o", llm_model_list=[])
    with patch("api.v1.endpoints.agent.chat.get_config", return_value=config), \
         patch("api.v1.endpoints.agent.chat.get_api_keys_for_model", return_value=["sk-fallback"]), \
         patch("api.v1.endpoints.agent.chat.extra_litellm_params", return_value={}):
        cfg = chat_mod._get_llm_config()
    assert cfg["api_key"] == "sk-fallback"


def test_get_llm_config_extra_params_provide_api_base_when_missing():
    config = _make_config(litellm_model="gpt-4o", openai_base_url=None, llm_model_list=[])
    with patch("api.v1.endpoints.agent.chat.get_config", return_value=config), \
         patch("api.v1.endpoints.agent.chat.get_api_keys_for_model", return_value=[]), \
         patch("api.v1.endpoints.agent.chat.extra_litellm_params",
               return_value={"api_base": "http://extra", "extra_headers": {"X": "1"}}):
        cfg = chat_mod._get_llm_config()
    assert cfg["api_base"] == "http://extra"
    assert cfg["extra_headers"] == {"X": "1"}


# ---------------------------------------------------------------------------
# _run_react_loop
# ---------------------------------------------------------------------------

def test_run_react_loop_exits_when_no_tool_calls():
    """LLM returns content without tool_calls -> loop exits returning content."""
    controller = _FakeController()
    fake_acompletion = _async_completion([_mock_llm_chunk(content="最终答案")])

    fake_cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod, \
             patch("api.v1.endpoints.agent.chat._flush_substreams", new=AsyncMock()):
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._run_react_loop(
                controller, [{"role": "user", "content": "hi"}], fake_cfg
            )

    result = asyncio.run(run())
    assert result == "最终答案"
    assert "最终答案" in controller.texts


def test_run_react_loop_handles_unknown_tool_name():
    """Tool name not in registry -> tool error response, loop continues."""
    controller = _FakeController()
    call_count = {"n": 0}

    async def fake_acompletion(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            tc = _mock_tool_call_delta(name="nonexistent_tool", arguments="{}")
            return _AsyncChunkStream([_mock_llm_chunk(tool_calls=[tc])])
        return _AsyncChunkStream([_mock_llm_chunk(content="done")])

    fake_cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}
    registry = MagicMock()
    registry.get_all_schemas.return_value = []
    registry.get_tool_names.return_value = ["get_kline"]

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod, \
             patch("api.v1.endpoints.agent.chat._registry", registry), \
             patch("api.v1.endpoints.agent.chat._flush_substreams", new=AsyncMock()):
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._run_react_loop(
                controller, [{"role": "user", "content": "hi"}], fake_cfg
            )

    result = asyncio.run(run())
    assert result == "done"
    assert call_count["n"] == 2


def test_run_react_loop_executes_known_tool_and_continues():
    """Known tool -> executed, result fed back, loop continues to final answer."""
    controller = _FakeController()
    call_count = {"n": 0}

    async def fake_acompletion(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            tc = _mock_tool_call_delta(name="get_kline", arguments='{"symbol":"000001"}')
            return _AsyncChunkStream([_mock_llm_chunk(tool_calls=[tc])])
        return _AsyncChunkStream([_mock_llm_chunk(content="分析完成")])

    fake_cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}
    registry = MagicMock()
    registry.get_all_schemas.return_value = []
    registry.get_tool_names.return_value = ["get_kline"]
    registry.execute.return_value = {"data": []}

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod, \
             patch("api.v1.endpoints.agent.chat._registry", registry), \
             patch("api.v1.endpoints.agent.chat._compact_tool_result", return_value={"data": []}), \
             patch("api.v1.endpoints.agent.chat._maybe_attach_search_fallback", return_value={"data": []}), \
             patch("api.v1.endpoints.agent.chat._format_result", return_value="{}"), \
             patch("api.v1.endpoints.agent.chat._flush_substreams", new=AsyncMock()):
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._run_react_loop(
                controller, [{"role": "user", "content": "hi"}], fake_cfg
            )

    result = asyncio.run(run())
    assert result == "分析完成"
    registry.execute.assert_called_once_with("get_kline", {"symbol": "000001"})


def test_run_react_loop_llm_call_failure_returns_empty():
    """LLM acompletion raises -> error text appended, returns empty string."""
    controller = _FakeController()

    async def fake_acompletion(**kwargs):
        raise RuntimeError("LLM down")

    fake_cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod, \
             patch("api.v1.endpoints.agent.chat._flush_substreams", new=AsyncMock()):
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._run_react_loop(
                controller, [{"role": "user", "content": "hi"}], fake_cfg
            )

    result = asyncio.run(run())
    assert result == ""
    assert any("分析出错" in t for t in controller.texts)


# ---------------------------------------------------------------------------
# _stream_final_answer_without_tools
# ---------------------------------------------------------------------------

def test_stream_final_answer_normal_output():
    controller = _FakeController()
    fake_acompletion = _async_completion([_mock_llm_chunk(content="总结"), _mock_llm_chunk(content="内容")])

    fake_cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod:
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._stream_final_answer_without_tools(
                controller, [{"role": "user", "content": "hi"}], fake_cfg
            )

    result = asyncio.run(run())
    assert result == "总结内容"
    assert controller.texts == ["总结", "内容"]


def test_stream_final_answer_llm_failure_appends_error_text():
    controller = _FakeController()

    async def fake_acompletion(**kwargs):
        raise RuntimeError("LLM down")

    fake_cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod:
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._stream_final_answer_without_tools(
                controller, [{"role": "user", "content": "hi"}], fake_cfg
            )

    result = asyncio.run(run())
    assert result == ""
    assert any("生成最终总结时出错" in t for t in controller.texts)


def test_stream_final_answer_empty_content_appends_hint():
    controller = _FakeController()
    fake_acompletion = _async_completion([_mock_llm_chunk(content=None)])

    fake_cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod:
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._stream_final_answer_without_tools(
                controller, [{"role": "user", "content": "hi"}], fake_cfg
            )

    result = asyncio.run(run())
    assert result == ""
    assert any("没有产出最终总结" in t for t in controller.texts)


# ---------------------------------------------------------------------------
# agent_chat SSE (HTTP-level)
# ---------------------------------------------------------------------------

def test_agent_chat_sse_normal_stream(client):
    """Happy path: agent_chat returns a 200 streaming response with content.

    DataStreamResponse JSON-encodes text, so CJK appears as \\uXXXX escapes.
    """
    fake_acompletion = _async_completion([_mock_llm_chunk(content="你好")])

    marker = "你好".encode("unicode_escape")
    with patch("api.v1.endpoints.agent.chat._get_llm_config",
               return_value={"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}), \
         patch("api.v1.endpoints.agent.chat.litellm") as llm_mod, \
         patch("api.v1.endpoints.agent.chat._flush_substreams", new=AsyncMock()):
        llm_mod.acompletion = fake_acompletion
        with client.stream("POST", "/api/v1/agent/chat",
                            json={"messages": [{"role": "user", "content": "hi"}]}) as response:
            assert response.status_code == 200
            body = b""
            for chunk in response.iter_bytes():
                body += chunk
                if marker in body:
                    break

    assert marker in body


def test_agent_chat_sse_llm_failure_still_returns_stream(client):
    """LLM call raises inside the loop -> stream still returns 200 with error text."""
    async def fake_acompletion(**kwargs):
        raise RuntimeError("LLM down")

    marker = "分析出错".encode("unicode_escape")
    with patch("api.v1.endpoints.agent.chat._get_llm_config",
               return_value={"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}), \
         patch("api.v1.endpoints.agent.chat.litellm") as llm_mod, \
         patch("api.v1.endpoints.agent.chat._flush_substreams", new=AsyncMock()):
        llm_mod.acompletion = fake_acompletion
        with client.stream("POST", "/api/v1/agent/chat",
                            json={"messages": [{"role": "user", "content": "hi"}]}) as response:
            assert response.status_code == 200
            body = b""
            for chunk in response.iter_bytes():
                body += chunk
                if marker in body:
                    break

    assert marker in body
