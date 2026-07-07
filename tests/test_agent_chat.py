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
import json
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

def test_get_llm_config_uses_anthropic_settings_env():
    with patch.dict(
        chat_mod.os.environ,
        {
            "ANTHROPIC_BASE_URL": "https://anthropic-gateway.example/v1",
            "ANTHROPIC_AUTH_TOKEN": "token-123",
            "ANTHROPIC_MODEL": "claude-sonnet-4-6",
        },
        clear=True,
    ):
        cfg = chat_mod._get_llm_config()

    assert cfg == {
        "model": "claude-sonnet-4-6",
        "custom_llm_provider": "anthropic",
        "api_key": "token-123",
        "api_base": "https://anthropic-gateway.example/v1",
        "extra_headers": {"authorization": "Bearer token-123"},
    }


def test_get_llm_config_keeps_gateway_model_name_with_provider_prefix():
    with patch.dict(
        chat_mod.os.environ,
        {
            "ANTHROPIC_BASE_URL": "https://anthropic-gateway.example/v1",
            "ANTHROPIC_AUTH_TOKEN": "token-123",
            "ANTHROPIC_MODEL": "openai/glm-5.2",
        },
        clear=True,
    ):
        cfg = chat_mod._get_llm_config()

    assert cfg["model"] == "openai/glm-5.2"
    assert cfg["custom_llm_provider"] == "anthropic"


def test_get_llm_config_strips_saved_setting_values():
    with patch.dict(
        chat_mod.os.environ,
        {
            "ANTHROPIC_BASE_URL": "  https://anthropic-gateway.example/v1  ",
            "ANTHROPIC_AUTH_TOKEN": "  token-123  ",
            "ANTHROPIC_MODEL": "  claude-sonnet-4-6  ",
        },
        clear=True,
    ):
        cfg = chat_mod._get_llm_config()

    assert cfg["api_base"] == "https://anthropic-gateway.example/v1"
    assert cfg["api_key"] == "token-123"
    assert cfg["model"] == "claude-sonnet-4-6"


def test_get_llm_config_removes_pasted_terminal_style_fragments_from_model():
    with patch.dict(
        chat_mod.os.environ,
        {
            "ANTHROPIC_BASE_URL": "https://anthropic-gateway.example/v1",
            "ANTHROPIC_AUTH_TOKEN": "token-123",
            "ANTHROPIC_MODEL": "\x1b[1mopenai/glm-5.2[1m]",
        },
        clear=True,
    ):
        cfg = chat_mod._get_llm_config()

    assert cfg["model"] == "openai/glm-5.2"
    assert cfg["custom_llm_provider"] == "anthropic"


def test_get_llm_config_errors_when_anthropic_settings_incomplete():
    with patch.dict(
        chat_mod.os.environ,
        {
            "ANTHROPIC_BASE_URL": "https://anthropic-gateway.example/v1",
            "ANTHROPIC_AUTH_TOKEN": "",
            "ANTHROPIC_MODEL": "",
        },
        clear=True,
    ):
        with pytest.raises(chat_mod.AgentModelConfigError) as exc_info:
            chat_mod._get_llm_config()

    message = str(exc_info.value)
    assert "AI 助手模型未配置完整" in message
    assert "鉴权令牌(ANTHROPIC_AUTH_TOKEN)" in message
    assert "主模型(ANTHROPIC_MODEL)" in message


# ---------------------------------------------------------------------------
# _run_react_loop
# ---------------------------------------------------------------------------

def test_run_react_loop_exits_when_no_tool_calls():
    """LLM returns content without tool_calls -> loop exits returning content."""
    controller = _FakeController()
    captured_kwargs = {}

    async def fake_acompletion(**kwargs):
        captured_kwargs.update(kwargs)
        return _AsyncChunkStream([_mock_llm_chunk(content="最终答案")])

    fake_cfg = {
        "model": "openai/glm-5.2",
        "custom_llm_provider": "anthropic",
        "api_key": "token-123",
        "api_base": "https://anthropic-gateway.example",
        "extra_headers": None,
    }

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
    assert captured_kwargs["model"] == "openai/glm-5.2"
    assert captured_kwargs["custom_llm_provider"] == "anthropic"
    assert captured_kwargs["api_key"] == "token-123"
    assert captured_kwargs["api_base"] == "https://anthropic-gateway.example"


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


def test_run_react_loop_executes_multiple_tools_concurrently():
    """LLM 一次返回多个 tool_call → 并发执行，结果全部回灌。

    并发证明用耗时：每个工具同步 sleep 0.3s，串行 ≈ 0.6s，并发 ≈ 0.3s。
    阈值 0.5s 居中，足以区分两种模式且对 CI 抖动有冗余。
    """
    import time

    controller = _FakeController()
    call_count = {"n": 0}
    sleep_seconds = 0.3

    def _execute(name, args):
        time.sleep(sleep_seconds)  # 同步阻塞取数
        return {"data": [name, args]}

    async def fake_acompletion(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            tc_a = _mock_tool_call_delta(idx=0, name="get_kline", arguments='{"symbol":"000001"}', tc_id="call_a")
            tc_b = _mock_tool_call_delta(idx=1, name="get_realtime_quotes", arguments='{"symbols":"000001"}', tc_id="call_b")
            return _AsyncChunkStream([_mock_llm_chunk(tool_calls=[tc_a, tc_b])])
        return _AsyncChunkStream([_mock_llm_chunk(content="完成")])

    fake_cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}
    registry = MagicMock()
    registry.get_all_schemas.return_value = []
    registry.get_tool_names.return_value = ["get_kline", "get_realtime_quotes"]
    registry.execute.side_effect = _execute

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod, \
             patch("api.v1.endpoints.agent.chat._registry", registry), \
             patch("api.v1.endpoints.agent.chat._compact_tool_result", side_effect=lambda n, r: r), \
             patch("api.v1.endpoints.agent.chat._maybe_attach_search_fallback", side_effect=lambda n, a, r: r), \
             patch("api.v1.endpoints.agent.chat._format_result", return_value="{}"), \
             patch("api.v1.endpoints.agent.chat._flush_substreams", new=AsyncMock()):
            llm_mod.acompletion = fake_acompletion
            start = time.monotonic()
            text = await chat_mod._run_react_loop(
                controller, [{"role": "user", "content": "hi"}], fake_cfg
            )
            elapsed = time.monotonic() - start
            return text, elapsed

    result, elapsed = asyncio.run(run())
    assert result == "完成"
    # 两个工具都被执行
    executed_names = {call.args[0] for call in registry.execute.call_args_list}
    assert executed_names == {"get_kline", "get_realtime_quotes"}
    # 并发而非串行：串行需 2*sleep，并发约 sleep；0.5s 阈值居中
    assert elapsed < sleep_seconds * 1.5, f"工具疑似串行执行，耗时 {elapsed:.2f}s"


def test_slim_tool_content_strips_detail_arrays_keeps_summary():
    """_slim_tool_content 丢弃明细数组、保留摘要、打 _slimmed 标记。"""
    payload = {
        "symbol": "600519",
        "count": 60,
        "latest": {"date": "2026-07-07", "close": 1500.0},
        "recent": [{"date": f"2026-07-0{i}"} for i in range(1, 6)],
        "history": [{"date": "2026-06-01"}],
        "analysis": {"data_quality": {"ok": True}},
        "_tool_payload_meta": {"compacted": True},
    }
    raw = json.dumps(payload, ensure_ascii=False)
    slim = chat_mod._slim_tool_content(raw)
    parsed = json.loads(slim)

    assert parsed["_slimmed"] is True
    assert "recent" not in parsed
    assert "history" not in parsed
    assert parsed["latest"] == {"date": "2026-07-07", "close": 1500.0}
    assert parsed["symbol"] == "600519"
    assert parsed["analysis"] == {"data_quality": {"ok": True}}
    assert parsed["_tool_payload_meta"] == {"compacted": True}


def test_slim_tool_content_idempotent_and_safe_on_non_json():
    """已裁剪的不重复处理；非 JSON/非 dict 原样返回。"""
    already = json.dumps({"symbol": "X", "_slimmed": True})
    assert chat_mod._slim_tool_content(already) == already
    # 非对象 JSON（数组/字符串）
    assert chat_mod._slim_tool_content("[1,2,3]") == "[1,2,3]"
    assert chat_mod._slim_tool_content("工具执行失败: boom") == "工具执行失败: boom"
    assert chat_mod._slim_tool_content("") == ""


def test_run_react_loop_slims_history_tool_results_between_rounds():
    """多轮循环：历史轮的 tool 结果被裁剪（丢 recent/明细），本轮保留完整。

    构造 4 轮：
      轮1: 调 get_kline，结果含 recent 明细数组
      轮2: 调 get_realtime_quotes，结果含 items
      轮3: 调 get_financials，结果含 recent_periods
      轮4: 直接给最终答案（无 tool_call）

    裁剪语义：每轮结束时把「更早轮」的 tool 结果二次瘦身，本轮刚加的保留完整。
    验证：
      轮2 请求 → 轮1 已裁（_slimmed、丢 recent、留 latest）
      轮3 请求 → 轮1 仍裁 + 轮2 已裁（丢 items）
      轮4 请求 → 轮1/2/3 全裁（丢 recent_periods）
    """
    controller = _FakeController()
    round_n = {"n": 0}
    captured: list[dict] = []

    async def fake_acompletion(**kwargs):
        round_n["n"] += 1
        captured.append({"round": round_n["n"], "messages": list(kwargs["messages"])})
        if round_n["n"] == 1:
            tc = _mock_tool_call_delta(name="get_kline", arguments='{"symbol":"600519"}', tc_id="call_kline")
            return _AsyncChunkStream([_mock_llm_chunk(tool_calls=[tc])])
        if round_n["n"] == 2:
            tc = _mock_tool_call_delta(name="get_realtime_quotes", arguments='{"symbols":"600519"}', tc_id="call_q")
            return _AsyncChunkStream([_mock_llm_chunk(tool_calls=[tc])])
        if round_n["n"] == 3:
            tc = _mock_tool_call_delta(name="get_financials", arguments='{"symbol":"600519"}', tc_id="call_fin")
            return _AsyncChunkStream([_mock_llm_chunk(tool_calls=[tc])])
        return _AsyncChunkStream([_mock_llm_chunk(content="最终分析")])

    fake_cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}
    registry = MagicMock()
    registry.get_all_schemas.return_value = []
    registry.get_tool_names.return_value = ["get_kline", "get_realtime_quotes", "get_financials"]

    results = {
        "get_kline": {"symbol": "600519", "count": 60, "latest": {"date": "2026-07-07"},
                      "recent": [{"date": f"2026-07-0{i}"} for i in range(1, 6)]},
        "get_realtime_quotes": {"total": 1, "items": [{"symbol": "600519", "price": 1500.0}],
                                "latest": None},
        "get_financials": {"symbol": "600519", "periods": 6,
                           "recent_periods": [{"eps": 1.0}], "latest": {"eps": 1.2}},
    }
    registry.execute.side_effect = lambda name, args: results[name]

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod, \
             patch("api.v1.endpoints.agent.chat._registry", registry), \
             patch("api.v1.endpoints.agent.chat._compact_tool_result", side_effect=lambda n, r: r), \
             patch("api.v1.endpoints.agent.chat._maybe_attach_search_fallback", side_effect=lambda n, a, r: r), \
             patch("api.v1.endpoints.agent.chat._format_result",
                   side_effect=lambda r: json.dumps(r, ensure_ascii=False)), \
             patch("api.v1.endpoints.agent.chat._flush_substreams", new=AsyncMock()):
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._run_react_loop(
                controller, [{"role": "user", "content": "分析茅台"}], fake_cfg
            )

    result = asyncio.run(run())
    assert result == "最终分析"

    def _tool_payload(round_idx, tool_call_id):
        msgs = captured[round_idx]["messages"]
        msg = next(m for m in msgs if m.get("role") == "tool"
                   and m.get("tool_call_id") == tool_call_id)
        return json.loads(msg["content"])

    # 轮2 请求：轮1 (get_kline) 已裁 —— 丢 recent、留 latest、含 _slimmed
    kline_r2 = _tool_payload(1, "call_kline")
    assert kline_r2.get("_slimmed") is True
    assert "recent" not in kline_r2
    assert kline_r2["latest"] == {"date": "2026-07-07"}

    # 轮3 请求：轮1 仍裁，轮2 (get_realtime_quotes) 已裁 —— 丢 items、留 total
    kline_r3 = _tool_payload(2, "call_kline")
    assert kline_r3.get("_slimmed") is True
    assert "recent" not in kline_r3
    quote_r3 = _tool_payload(2, "call_q")
    assert quote_r3.get("_slimmed") is True
    assert "items" not in quote_r3
    assert quote_r3["total"] == 1

    # 轮4 请求：轮1/2 已裁；轮3 (get_financials) 是上一轮刚加的，对轮4而言模型正要用，
    # 必须保持完整（反证裁剪逻辑没有误伤上一轮）。裁剪只在「更早轮」+「下一轮请求前」发生。
    assert _tool_payload(3, "call_kline").get("_slimmed") is True
    assert _tool_payload(3, "call_q").get("_slimmed") is True
    fin_r4 = _tool_payload(3, "call_fin")
    assert fin_r4.get("_slimmed") is None  # 未裁
    assert "recent_periods" in fin_r4       # 明细保留
    assert fin_r4["latest"] == {"eps": 1.2}


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


# ---------------------------------------------------------------------------
# _normalize_incoming_messages — AI SDK v5 → OpenAI 格式适配
# ---------------------------------------------------------------------------

def test_normalize_passthrough_string_content():
    """字符串 content 的 user/system 透传，role 缺省补 user。"""
    out = chat_mod._normalize_incoming_messages([
        {"role": "user", "content": "你好"},
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi", "id": "u1"},
    ])
    assert out[0] == {"role": "user", "content": "你好"}
    assert out[1] == {"role": "system", "content": "sys"}
    # 保留非 role 字段
    assert out[2]["role"] == "user"
    assert out[2]["content"] == "hi"
    assert out[2]["id"] == "u1"


def test_normalize_user_aisdk_array_to_string():
    """user 数组 content → 拼 text 为字符串。"""
    out = chat_mod._normalize_incoming_messages([
        {"role": "user", "content": [{"type": "text", "text": "分析茅台"}, {"type": "text", "text": "行情"}]},
    ])
    assert out[0] == {"role": "user", "content": "分析茅台\n行情"}


def test_normalize_assistant_with_tool_calls():
    """assistant 含 text + 多个 tool-call → content 字符串 + tool_calls 数组。"""
    out = chat_mod._normalize_incoming_messages([
        {"role": "assistant", "content": [
            {"type": "text", "text": "正在查询"},
            {"type": "tool-call", "toolCallId": "call_a", "toolName": "get_kline", "input": {"symbol": "600519"}},
            {"type": "tool-call", "toolCallId": "call_b", "toolName": "get_realtime_quotes", "input": {}},
        ]},
    ])
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
    out = chat_mod._normalize_incoming_messages([
        {"role": "assistant", "content": [
            {"type": "reasoning", "text": "我需要先查行情"},
            {"type": "text", "text": "查询中"},
        ]},
    ])
    assert out[0] == {"role": "assistant", "content": "查询中"}


def test_normalize_tool_result_to_openai():
    """AI SDK tool-result(json) → OpenAI {tool_call_id, content:json字符串}。"""
    out = chat_mod._normalize_incoming_messages([
        {"role": "tool", "content": [
            {"type": "tool-result", "toolCallId": "call_x", "toolName": "get_kline",
             "output": {"type": "json", "value": {"symbol": "600519", "latest": {"close": 1500}}}},
        ]},
    ])
    msg = out[0]
    assert msg["role"] == "tool"
    assert msg["tool_call_id"] == "call_x"
    payload = json.loads(msg["content"])
    assert payload["symbol"] == "600519"
    assert payload["latest"] == {"close": 1500}


def test_normalize_tool_result_error_prefix():
    """error-json / isError → content 带 [工具执行错误] 前缀。"""
    # error-json
    out1 = chat_mod._normalize_incoming_messages([
        {"role": "tool", "content": [
            {"type": "tool-result", "toolCallId": "c1",
             "output": {"type": "error-json", "value": {"error": "boom"}}},
        ]},
    ])
    assert out1[0]["content"].startswith("[工具执行错误] ")
    # isError
    out2 = chat_mod._normalize_incoming_messages([
        {"role": "tool", "content": [
            {"type": "tool-result", "toolCallId": "c2", "isError": True,
             "output": {"type": "json", "value": {"msg": "fail"}}},
        ]},
    ])
    assert out2[0]["content"].startswith("[工具执行错误] ")


def test_normalize_history_tool_detail_arrays_slimmed():
    """从前端回传的历史 tool 结果明细数组被裁剪（_slim_tool_content 生效）。"""
    out = chat_mod._normalize_incoming_messages([
        {"role": "tool", "content": [
            {"type": "tool-result", "toolCallId": "call_hist", "toolName": "get_kline",
             "output": {"type": "json", "value": {
                 "symbol": "600519", "count": 60,
                 "latest": {"date": "2026-07-07", "close": 1500.0},
                 "recent": [{"date": f"2026-07-0{i}"} for i in range(1, 6)],
             }}},
        ]},
    ])
    payload = json.loads(out[0]["content"])
    assert payload.get("_slimmed") is True
    assert "recent" not in payload          # 明细被裁
    assert payload["latest"] == {"date": "2026-07-07", "close": 1500.0}  # 摘要保留


def test_normalize_passthrough_openai_format():
    """已是 OpenAI 格式（tool 有 tool_call_id、assistant 有 tool_calls）→ 透传不转。"""
    openai_tool = {"role": "tool", "tool_call_id": "call_z", "content": '{"a":1}'}
    openai_asst = {"role": "assistant", "content": None,
                   "tool_calls": [{"id": "call_z", "type": "function",
                                   "function": {"name": "f", "arguments": "{}"}}]}
    out = chat_mod._normalize_incoming_messages([openai_tool, openai_asst])
    assert out[0] is openai_tool
    assert out[1] is openai_asst


def test_normalize_robustness():
    """非 dict 跳过；未知 part type 跳过；缺 toolCallId 生成默认 id。"""
    out = chat_mod._normalize_incoming_messages([
        "not a dict",
        {"role": "assistant", "content": [{"type": "unknown-type", "foo": "bar"}]},
        {"role": "tool", "content": [{"type": "tool-result", "output": {"type": "json", "value": {"x": 1}}}]},
    ])
    # 非 dict 被跳过
    assert len(out) == 2
    # 未知 part：assistant 无 text 无 tool-call → content None
    assert out[0] == {"role": "assistant", "content": None}
    # 缺 toolCallId：生成 call_ 开头的 id；历史 tool 结果会被裁剪（加 _slimmed 标记）
    assert out[1]["tool_call_id"].startswith("call_")
    payload = json.loads(out[1]["content"])
    assert payload["x"] == 1
    assert payload.get("_slimmed") is True


# ---------------------------------------------------------------------------
# 端到端：前端 AI SDK 格式历史 → 发给 litellm 的是 OpenAI 格式 + 历史明细被裁
# ---------------------------------------------------------------------------

def test_run_react_loop_normalizes_aisdk_history_before_llm_call():
    """前端发 AI SDK v5 格式历史消息，首轮发给 litellm 的必须是 OpenAI 格式，
    且历史 tool 的明细数组被裁、本轮新加的 tool 结果完整。"""
    controller = _FakeController()
    captured: list[dict] = []
    round_n = {"n": 0}

    async def fake_acompletion(**kwargs):
        round_n["n"] += 1
        captured.append({"round": round_n["n"], "messages": list(kwargs["messages"])})
        if round_n["n"] == 1:
            # 本轮模型决定再查一次行情
            tc = _mock_tool_call_delta(name="get_realtime_quotes", arguments='{"symbols":"600519"}', tc_id="call_new")
            return _AsyncChunkStream([_mock_llm_chunk(tool_calls=[tc])])
        return _AsyncChunkStream([_mock_llm_chunk(content="最终分析")])

    # 前端发来的 AI SDK 格式历史：user → assistant(带 tool-call) → tool-result(带 recent 明细)
    aisdk_messages = [
        {"role": "user", "content": [{"type": "text", "text": "分析茅台行情"}]},
        {"role": "assistant", "content": [
            {"type": "text", "text": "我来查一下"},
            {"type": "tool-call", "toolCallId": "call_hist", "toolName": "get_kline", "input": {"symbol": "600519"}},
        ]},
        {"role": "tool", "content": [
            {"type": "tool-result", "toolCallId": "call_hist", "toolName": "get_kline",
             "output": {"type": "json", "value": {
                 "symbol": "600519", "count": 60,
                 "latest": {"date": "2026-07-07", "close": 1500.0},
                 "recent": [{"date": f"2026-07-0{i}"} for i in range(1, 6)],
             }}},
        ]},
    ]

    fake_cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}
    registry = MagicMock()
    registry.get_all_schemas.return_value = []
    registry.get_tool_names.return_value = ["get_realtime_quotes"]
    registry.execute.return_value = {"total": 1, "items": [{"symbol": "600519", "price": 1500.0}]}

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod, \
             patch("api.v1.endpoints.agent.chat._registry", registry), \
             patch("api.v1.endpoints.agent.chat._compact_tool_result", side_effect=lambda n, r: r), \
             patch("api.v1.endpoints.agent.chat._maybe_attach_search_fallback", side_effect=lambda n, a, r: r), \
             patch("api.v1.endpoints.agent.chat._format_result",
                   side_effect=lambda r: json.dumps(r, ensure_ascii=False)), \
             patch("api.v1.endpoints.agent.chat._flush_substreams", new=AsyncMock()):
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._run_react_loop(controller, aisdk_messages, fake_cfg)

    result = asyncio.run(run())
    assert result == "最终分析"

    # 首轮发给 litellm 的消息必须是 OpenAI 格式
    round1_msgs = captured[0]["messages"]
    # [system, user, assistant, tool]
    assert round1_msgs[0]["role"] == "system"
    assert round1_msgs[1] == {"role": "user", "content": "分析茅台行情"}

    asst = round1_msgs[2]
    assert asst["role"] == "assistant"
    assert asst["content"] == "我来查一下"
    assert asst["tool_calls"][0]["id"] == "call_hist"
    assert asst["tool_calls"][0]["function"]["name"] == "get_kline"
    assert json.loads(asst["tool_calls"][0]["function"]["arguments"]) == {"symbol": "600519"}

    # 历史 tool 消息：OpenAI 格式 + 明细被裁
    tool_msg = round1_msgs[3]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "call_hist"
    hist_payload = json.loads(tool_msg["content"])
    assert hist_payload.get("_slimmed") is True
    assert "recent" not in hist_payload
    assert hist_payload["latest"] == {"date": "2026-07-07", "close": 1500.0}

    # 第二轮：本轮新加的 tool 结果(call_new)保持完整（未裁，无 _slimmed）
    round2_msgs = captured[1]["messages"]
    new_tool = next(m for m in round2_msgs if m.get("role") == "tool" and m.get("tool_call_id") == "call_new")
    new_payload = json.loads(new_tool["content"])
    assert new_payload.get("_slimmed") is None
    assert new_payload["items"] == [{"symbol": "600519", "price": 1500.0}]
    # 而历史 call_hist 在第二轮仍是裁剪态
    hist_tool2 = next(m for m in round2_msgs if m.get("role") == "tool" and m.get("tool_call_id") == "call_hist")
    assert json.loads(hist_tool2["content"]).get("_slimmed") is True
