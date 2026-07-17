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


def _mock_llm_chunk(content=None, tool_calls=None, finish_reason=None):
    """Build a litellm streaming chunk mock."""
    delta = MagicMock()
    delta.content = content
    delta.tool_calls = tool_calls
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
        self._stream_tasks = []
        self.tool_calls = []

    def append_text(self, text):
        self.texts.append(text)

    async def add_tool_call(self, name, tool_call_id=None):
        self.tool_calls.append((name, tool_call_id))
        tool = MagicMock()
        tool.append_args_text = MagicMock()
        tool.set_response = MagicMock()
        return tool


# ---------------------------------------------------------------------------
# _get_llm_config 的解析逻辑已迁移至 src.llm.anthropic_gateway，
# 相关测试见 tests/test_anthropic_gateway.py。
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _run_react_loop
# ---------------------------------------------------------------------------

def test_turn_policy_keeps_all_tools_available_for_arbitrary_research():
    policy = chat_mod._resolve_turn_policy([
        {"role": "user", "content": "分析人形机器人产业链，哪些领域最受益？"},
    ])
    assert policy["name"] == "general_agent"
    assert policy["max_tool_calls"] == chat_mod.MAX_TOOL_CALLS_PER_RUN
    assert policy["require_tools"] is False
    assert policy["allowed_tools"] is None


def test_turn_policy_disables_tools_only_for_exact_casual_messages():
    policy = chat_mod._resolve_turn_policy([
        {"role": "user", "content": "你好"},
    ])
    assert policy["name"] == "casual"
    assert policy["allowed_tools"] == set()

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
                controller, [{"role": "user", "content": "解释市盈率是什么"}], fake_cfg
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
                controller, [{"role": "user", "content": "做一个通用分析"}], fake_cfg
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
                controller, [{"role": "user", "content": "分析这些股票"}], fake_cfg
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
    model_requests = []
    sleep_seconds = 0.3

    def _execute(name, args):
        time.sleep(sleep_seconds)  # 同步阻塞取数
        return {"data": [name, args]}

    async def fake_acompletion(**kwargs):
        call_count["n"] += 1
        model_requests.append(list(kwargs["messages"]))
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
                controller, [{"role": "user", "content": "比较两个测试对象"}], fake_cfg
            )
            elapsed = time.monotonic() - start
            return text, elapsed

    result, elapsed = asyncio.run(run())
    assert result == "完成"
    # 两个工具都被执行
    executed_names = {call.args[0] for call in registry.execute.call_args_list}
    assert executed_names == {"get_kline", "get_realtime_quotes"}
    # Parallel calls must be represented by one assistant message owning both
    # tool_calls, followed by two tool results.  Splitting them into separate
    # assistant messages breaks Anthropic adapters.
    second_request = model_requests[1]
    tool_assistant_messages = [
        message for message in second_request
        if message.get("role") == "assistant" and message.get("tool_calls")
    ]
    assert len(tool_assistant_messages) == 1
    assert {call["id"] for call in tool_assistant_messages[0]["tool_calls"]} == {"call_a", "call_b"}
    # 并发而非串行：串行需 2*sleep，并发约 sleep；0.5s 阈值居中
    assert elapsed < sleep_seconds * 1.5, f"工具疑似串行执行，耗时 {elapsed:.2f}s"


def test_run_react_loop_hides_duplicate_and_over_budget_tool_requests():
    """Only genuinely executed research calls should appear in the tool UI."""
    controller = _FakeController()
    llm_calls = {"n": 0}

    async def fake_acompletion(**kwargs):
        llm_calls["n"] += 1
        if llm_calls["n"] == 1:
            symbols = ["000001", "000001", "000002", "000003", "000004", "000005"]
            calls = [
                _mock_tool_call_delta(
                    idx=index,
                    name="get_kline",
                    arguments=json.dumps({"symbol": symbol}),
                    tc_id=f"call_{index}",
                )
                for index, symbol in enumerate(symbols)
            ]
            return _AsyncChunkStream([_mock_llm_chunk(tool_calls=calls)])
        return _AsyncChunkStream([_mock_llm_chunk(content="done")])

    registry = MagicMock()
    registry.get_all_schemas.return_value = []
    registry.get_tool_names.return_value = ["get_kline"]
    registry.execute.return_value = {"success": True}
    fake_cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod, \
             patch("api.v1.endpoints.agent.chat._registry", registry), \
             patch("api.v1.endpoints.agent.chat._flush_substreams", new=AsyncMock()):
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._run_react_loop(
                controller, [{"role": "user", "content": "分析这些股票"}], fake_cfg
            )

    assert asyncio.run(run()) == "done"
    assert len(controller.tool_calls) == chat_mod.MAX_TOOL_CALLS_PER_ROUND
    assert registry.execute.call_count == chat_mod.MAX_TOOL_CALLS_PER_ROUND


def test_referential_multi_stock_calls_are_coalesced_with_verified_entities():
    calls = [
        {"id": "a", "name": "get_realtime_quotes", "arguments": '{"symbols":"003021,603662"}'},
        {"id": "b", "name": "get_technical_indicators", "arguments": '{"symbol":"300682"}'},
        {"id": "c", "name": "get_technical_indicators", "arguments": '{"symbol":"002520"}'},
    ]
    entities = [
        {"name": "兆威机电", "symbol": "003021"},
        {"name": "柯力传感", "symbol": "603662"},
        {"name": "汉威科技", "symbol": "300007"},
        {"name": "南方精工", "symbol": "002553"},
    ]
    coalesced = chat_mod._coalesce_multi_security_calls(
        calls,
        verified_entities=entities,
        referential_followup=True,
    )
    assert len(coalesced) == 1
    assert coalesced[0]["name"] == "get_multi_stock_snapshot"
    assert json.loads(coalesced[0]["arguments"])["symbols"] == "003021,603662,300007,002553"


def test_verified_entity_context_scopes_referential_followup_to_previous_table_rows():
    messages = [
        {
            "role": "assistant",
            "content": (
                "| 公司 | 证据 |\n"
                "|---|---|\n"
                "| **维宏股份** | 正文还提到绿的谐波和中大力德作为例子 |\n"
                "| **兆威机电 (003021)** | 灵巧手 |\n\n"
                "风险提示中再次提到机器人产业。"
            ),
        },
        {"role": "user", "content": "上面提到的这些公司现在能买吗"},
    ]

    context, entities = chat_mod._verified_entity_context(messages)

    assert entities == [
        {"name": "维宏股份", "symbol": "300508"},
        {"name": "兆威机电", "symbol": "003021"},
    ]
    assert "禁止扩展范围" in context


def test_multi_stock_round_keeps_symmetric_risk_pair_and_drops_redundant_valuation():
    calls = [
        {"id": "batch", "name": "get_multi_stock_snapshot", "arguments": '{"symbols":"600519,000858"}'},
        {"id": "v1", "name": "get_valuation_ratios", "arguments": '{"symbol":"600519"}'},
        {"id": "v2", "name": "get_valuation_ratios", "arguments": '{"symbol":"000858"}'},
        {"id": "b1", "name": "get_business_segments", "arguments": '{"symbol":"600519"}'},
        {"id": "b2", "name": "get_business_segments", "arguments": '{"symbol":"000858"}'},
        {"id": "r1", "name": "get_risk_events", "arguments": '{"symbol":"600519"}'},
        {"id": "r2", "name": "get_risk_events", "arguments": '{"symbol":"000858"}'},
    ]
    selected = chat_mod._select_balanced_multi_security_calls(
        calls,
        latest_user_text="比较两家公司基本面、估值与主要风险",
    )
    assert [call["id"] for call in selected] == ["batch", "r1", "r2"]


def test_multi_stock_round_preserves_detailed_valuation_pair_when_history_requested():
    calls = [
        {"id": "batch", "name": "get_multi_stock_snapshot", "arguments": '{"symbols":"600519,000858"}'},
        {"id": "v1", "name": "get_valuation_ratios", "arguments": '{"symbol":"600519"}'},
        {"id": "v2", "name": "get_valuation_ratios", "arguments": '{"symbol":"000858"}'},
    ]
    selected = chat_mod._select_balanced_multi_security_calls(
        calls,
        latest_user_text="比较两家公司五年估值历史分位",
    )
    assert [call["id"] for call in selected] == ["batch", "v1", "v2"]


def test_multi_stock_round_synthesizes_symmetric_risk_calls_when_plan_only_has_batch():
    calls = [
        {"id": "batch", "name": "get_multi_stock_snapshot", "arguments": '{"symbols":"600519,000858"}'},
    ]
    selected = chat_mod._select_balanced_multi_security_calls(
        calls,
        latest_user_text="比较两家公司基本面、估值与主要风险",
    )
    assert [call["name"] for call in selected] == [
        "get_multi_stock_snapshot",
        "get_risk_events",
        "get_risk_events",
    ]
    assert [json.loads(call["arguments"])["symbol"] for call in selected[1:]] == [
        "600519",
        "000858",
    ]


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
    # Final synthesis is buffered until completion so a length/timeout failure
    # can be atomically replaced with a complete evidence-backed fallback.
    assert controller.texts == ["总结内容"]


def test_industry_playbook_executes_runtime_owned_evidence_plan_before_model():
    controller = _FakeController()
    llm_calls = []

    async def fake_acompletion(**kwargs):
        llm_calls.append(kwargs)
        valid = (
            "## 受益优先级\n最受益环节按价值量排序。\n"
            "## 产业链地图\n上游核心部件、中游整机、下游应用。\n"
            "受益机制是价值量提升，兑现指标看订单和产能。\n"
            "反证与风险包括量产不及预期。\n"
            "持续跟踪量化指标。来源：研究资料；截至2026-07-17；"
            "置信度中等；证据缺口已列示。"
            "[来源一](https://example.com/a) [来源二](https://example.org/b)"
        )
        return _AsyncChunkStream([_mock_llm_chunk(content=valid, finish_reason="stop")])

    registry = MagicMock()
    registry.get_all_schemas.return_value = []
    registry.get_tool_names.return_value = ["search_financial_news", "search_research_library"]
    registry.execute.side_effect = lambda name, args: {
        "success": True,
        "items": [{"title": name, "published": "2026-07-01", "source": "测试源"}],
    }
    cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod, \
             patch("api.v1.endpoints.agent.chat._registry", registry), \
             patch("api.v1.endpoints.agent.chat._compact_tool_result", side_effect=lambda n, r: r), \
             patch("api.v1.endpoints.agent.chat._maybe_attach_search_fallback", side_effect=lambda n, a, r: r), \
             patch("api.v1.endpoints.agent.chat._flush_substreams", new=AsyncMock()):
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._run_react_loop(
                controller,
                [{"role": "user", "content": "帮我分析下人形机器人产业链，哪些领域最受益？"}],
                cfg,
            )

    result = asyncio.run(run())
    assert "受益优先级" in result
    assert registry.execute.call_count == 4
    assert {call.args[0] for call in registry.execute.call_args_list} == {
        "search_financial_news",
        "search_research_library",
    }
    # There is no model-planning turn before the mandatory evidence calls;
    # the only model request is the final synthesis.
    assert len(llm_calls) == 1
    assert "强制分析标准" in llm_calls[0]["messages"][0]["content"]


def test_industry_playbook_repairs_an_answer_that_drops_required_sections():
    controller = _FakeController()
    call_count = {"value": 0}
    requests = []

    async def fake_acompletion(**kwargs):
        call_count["value"] += 1
        requests.append(kwargs)
        if call_count["value"] == 1:
            return _AsyncChunkStream([_mock_llm_chunk(content="机器人行业会受益。", finish_reason="stop")])
        repaired = (
            "优先级与最受益排序如下。上游、中游、下游构成产业链。"
            "受益机制看价值量，兑现指标看订单与产能。反证和风险是不及预期。"
            "后续跟踪量化指标。来源见工具证据，截至2026-07-17，置信度中等，证据缺口明确。"
            "[来源一](https://example.com/a) [来源二](https://example.org/b)"
        )
        return _AsyncChunkStream([_mock_llm_chunk(content=repaired, finish_reason="stop")])

    cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}
    evidence = [{"tool": "search_financial_news", "result": {"success": True, "items": []}}]

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod:
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._stream_final_answer_without_tools(
                controller,
                [{"role": "user", "content": "分析人形机器人产业链"}],
                cfg,
                evidence=evidence,
                playbook=chat_mod.INDUSTRY_CHAIN,
            )

    result = asyncio.run(run())
    assert result.startswith("优先级")
    assert "机器人行业会受益" not in result
    assert call_count["value"] == 2
    assert "运行时输出验收未通过" in requests[-1]["messages"][-1]["content"]


def test_playbook_timeout_keeps_buffered_text_only_when_contract_is_complete():
    controller = _FakeController()
    complete = (
        "优先级和最受益排序。上游、中游、下游产业链。"
        "受益机制看价值量，兑现指标看订单产能。反证与风险是不及预期。"
        "后续跟踪量化指标，来源截至2026-07-17，置信度中等，证据缺口明确。"
        "[来源一](https://example.com/a) [来源二](https://example.org/b)"
    )

    class _TimeoutAfterContent:
        def __aiter__(self):
            self.done = False
            return self

        async def __anext__(self):
            if not self.done:
                self.done = True
                return _mock_llm_chunk(content=complete)
            raise TimeoutError("terminal frame missing")

    async def fake_acompletion(**kwargs):
        return _TimeoutAfterContent()

    cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod:
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._stream_final_answer_without_tools(
                controller,
                [{"role": "user", "content": "分析产业链"}],
                cfg,
                evidence=[],
                playbook=chat_mod.INDUSTRY_CHAIN,
            )

    assert asyncio.run(run()) == complete
    assert controller.texts == [complete]


def test_playbook_timeout_retries_once_when_buffer_is_incomplete():
    controller = _FakeController()
    calls = {"value": 0}
    repaired = (
        "优先级和最受益排序。上游、中游、下游产业链。"
        "受益机制看价值量，兑现指标看订单产能。反证与风险是不及预期。"
        "后续跟踪量化指标，来源截至2026-07-17，置信度中等，证据缺口明确。"
        "[来源一](https://example.com/a) [来源二](https://example.org/b)"
    )

    class _ImmediateTimeout:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise TimeoutError("provider stalled")

    async def fake_acompletion(**kwargs):
        calls["value"] += 1
        if calls["value"] == 1:
            return _ImmediateTimeout()
        return _AsyncChunkStream([_mock_llm_chunk(content=repaired, finish_reason="stop")])

    cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod:
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._stream_final_answer_without_tools(
                controller,
                [{"role": "user", "content": "分析产业链"}],
                cfg,
                evidence=[],
                playbook=chat_mod.INDUSTRY_CHAIN,
            )

    assert asyncio.run(run()) == repaired
    assert calls["value"] == 2


def test_playbook_empty_completion_retries_once_before_failing_closed():
    controller = _FakeController()
    calls = {"value": 0}
    repaired = (
        "优先级和最受益排序。上游、中游、下游产业链。"
        "受益机制看价值量，兑现指标看订单产能。反证与风险是不及预期。"
        "后续跟踪量化指标，来源截至2026-07-17，置信度中等，证据缺口明确。"
        "[来源一](https://example.com/a) [来源二](https://example.org/b)"
    )

    async def fake_acompletion(**kwargs):
        calls["value"] += 1
        if calls["value"] == 1:
            return _AsyncChunkStream([_mock_llm_chunk(content=None, finish_reason="stop")])
        return _AsyncChunkStream([_mock_llm_chunk(content=repaired, finish_reason="stop")])

    cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod:
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._stream_final_answer_without_tools(
                controller,
                [{"role": "user", "content": "分析产业链"}],
                cfg,
                evidence=[],
                playbook=chat_mod.INDUSTRY_CHAIN,
            )

    assert asyncio.run(run()) == repaired
    assert calls["value"] == 2


def test_professional_contract_requires_every_company_and_decision_axis():
    evidence = [{
        "tool": "get_multi_stock_decision_evidence",
        "result": {
            "success": True,
            "resolved_entities": [
                {"name": "兆威机电", "symbol": "003021"},
                {"name": "绿的谐波", "symbol": "688017"},
            ],
        },
    }]
    incomplete = "003021：主营业务不错，财务和估值需要看。"
    issues = chat_mod._playbook_answer_contract_issues(
        chat_mod.INVESTMENT_DECISION,
        incomplete,
        evidence,
    )
    assert any("688017" in issue or "绿的谐波" in issue for issue in issues)
    assert any("交易状态" in issue for issue in issues)
    assert any("风险催化" in issue for issue in issues)

    complete = (
        "003021、688017：主营业务兑现；财务营收、净利和现金流；"
        "估值PE与一致预期；交易趋势与资金；公告催化和风险；"
        "结论等待验证，并给出成立条件与失效条件。"
    )
    assert chat_mod._playbook_answer_contract_issues(
        chat_mod.INVESTMENT_DECISION,
        complete,
        evidence,
    ) == []


def test_professional_contract_fails_closed_when_evidence_packet_is_unavailable():
    issues = chat_mod._playbook_answer_contract_issues(
        chat_mod.INVESTMENT_DECISION,
        "我认为可以买入。",
        [{"tool": "get_multi_stock_decision_evidence", "result": {"success": False}}],
    )
    assert issues == ["专业决策证据未成功取得，必须停止买入判断并说明证据缺口"]
    assert chat_mod._playbook_answer_contract_issues(
        chat_mod.INVESTMENT_DECISION,
        "本轮专业证据不足，因此暂不做买入判断。",
        [{"tool": "get_multi_stock_decision_evidence", "result": {"success": False}}],
    ) == []


def test_mapping_contract_requires_first_column_entities_for_next_turn_scope():
    prose_only = (
        "兆威机电003021属于上游环节，L2证据为已披露送样；"
        "订单收入缺口待核验，来源为2026年公告。"
    )
    issues = chat_mod._playbook_answer_contract_issues(
        chat_mod.THEME_COMPANY_MAPPING,
        prose_only,
        [],
    )
    assert any("表格第一列" in issue for issue in issues)

    table = (
        "| 公司/代码 | 产业链环节 | 证据等级 | 已验证事实 | 证据缺口 | 来源与日期 |\n"
        "|---|---|---|---|---|---|\n"
        "| 兆威机电 (003021) | 上游 | L2 | 已披露送样 | 订单收入待核验 | [公司公告](https://example.com/003021)，2026-06-01 |\n"
        "\n本轮仅列上述代表公司，其他环节未覆盖，需进一步核验；仅概念公司未列入。\n\n"
        "| 未覆盖环节 | 原因 |\n|---|---|\n| 减速器 | 本轮证据不足 |"
    )
    assert chat_mod._playbook_answer_contract_issues(
        chat_mod.THEME_COMPANY_MAPPING,
        table,
        [],
    ) == []

    pending_code = (
        "| 公司/代码 | 环节 | 证据等级 | 已验证事实 | 缺口 | 来源日期 |\n"
        "|---|---|---|---|---|---|\n"
        "| 雷迪克（代码待核验） | 上游 | L2 | 已披露送样 | 收入待核验 | [来源](https://example.com/a)，2026-07-01 |\n"
        "本轮仅列代表公司，未覆盖环节需进一步核验。"
    )
    pending_issues = chat_mod._playbook_answer_contract_issues(
        chat_mod.THEME_COMPANY_MAPPING,
        pending_code,
        [],
    )
    assert any("缺少六位代码" in issue for issue in pending_issues)
    assert "存在未核验证券代码" in pending_issues


def test_mapping_contract_rejects_answers_that_omit_returned_candidates():
    table = (
        "| 公司/代码 | 产业链环节 | 证据等级 | 已验证事实 | 证据缺口 | 来源与日期 |\n"
        "|---|---|---|---|---|---|\n"
        "| 兆威机电 (003021) | 上游 | L1 | 已验证概念关联 | 订单收入待核验 | "
        "[概念板块](https://example.com/theme)，2026-07-17 |\n\n"
        "本轮仅列代表公司，未覆盖环节需进一步核验。"
    )
    candidate_names = [
        ("兆威机电", "003021"), ("绿的谐波", "688017"), ("汇川技术", "300124"),
        ("机器人", "300024"), ("秦川机床", "000837"), ("巨轮智能", "002031"),
        ("新时达", "002527"), ("博实股份", "002698"),
    ]
    evidence = [{
        "tool": "get_theme_stock_candidates",
        "result": {
            "success": True,
            "items": [{"name": name, "symbol": symbol} for name, symbol in candidate_names],
        },
    }]

    issues = chat_mod._playbook_answer_contract_issues(
        chat_mod.THEME_COMPANY_MAPPING,
        table,
        evidence,
    )

    assert any("最终答案遗漏7家" in issue and "完整候选索引" in issue for issue in issues)


def test_mapping_contract_rejects_business_claims_supported_only_by_concept_board():
    table = (
        "| 公司/代码 | 产业链环节 | 证据等级 | 已验证事实 | 证据缺口 | 来源与日期 |\n"
        "|---|---|---|---|---|---|\n"
        "| 汇川技术 (300124) | 伺服电机/驱动 | L1 | 属于机器人概念板块成分股，是工业自动化龙头 | "
        "订单收入待核验 | [新浪概念板块](http://vip.stock.finance.sina.com.cn/mkt/#gn_zjqrgn)，2026-07-17 |\n\n"
        "本轮仅列代表公司，未覆盖环节需进一步核验。"
    )

    issues = chat_mod._playbook_answer_contract_issues(
        chat_mod.THEME_COMPANY_MAPPING,
        table,
        [],
    )

    assert any("不能直接确定产业链环节" in issue for issue in issues)
    assert any("未经核验的公司业务事实" in issue for issue in issues)


def test_theme_mapping_fallback_returns_complete_local_verified_l1_inventory():
    candidate_names = [
        ("兆威机电", "003021"), ("绿的谐波", "688017"), ("汇川技术", "300124"),
        ("机器人", "300024"), ("秦川机床", "000837"), ("巨轮智能", "002031"),
        ("新时达", "002527"), ("博实股份", "002698"),
    ]
    result = {
        "success": True,
        "theme": "人形机器人",
        "local_universe_count": 5534,
        "candidate_count": 45,
        "data_time": "2026-07-17",
        "items": [
            {
                "name": name,
                "symbol": symbol,
                "sector": "专用设备制造业",
                "boards": ["机器人概念"],
                "sources": [{
                    "name": "新浪概念板块",
                    "url": "https://example.com/theme",
                    "date": "2026-07-17",
                }],
            }
            for name, symbol in candidate_names
        ],
    }

    fallback = chat_mod._build_verified_evidence_fallback([
        {"tool": "get_theme_stock_candidates", "result": result},
    ])

    assert "本地 **5534 只**证券" in fallback
    assert "完整候选池（L1，共 8 家）" in fallback
    assert all(f"{name} ({symbol})" in fallback for name, symbol in candidate_names)
    assert "不等同订单或收入兑现" in fallback
    assert chat_mod._playbook_answer_contract_issues(
        chat_mod.THEME_COMPANY_MAPPING,
        fallback,
        [{"tool": "get_theme_stock_candidates", "result": result}],
    ) == []


def test_theme_mapping_fallback_promotes_company_level_validation_evidence_to_l2():
    result = {
        "success": True,
        "local_universe_count": 5534,
        "candidate_count": 20,
        "data_time": "2026-07-17",
        "items": [
            {
                "name": name,
                "symbol": symbol,
                "boards": ["机器人概念"],
                "sources": [{
                    "name": "新浪概念板块",
                    "url": "https://example.com/theme",
                    "date": "2026-07-17",
                }],
            }
            for name, symbol in [
                ("兆丰股份", "300695"), ("机器人", "300024"), ("汇川技术", "300124"),
                ("秦川机床", "000837"), ("巨轮智能", "002031"), ("新时达", "002527"),
                ("博实股份", "002698"), ("兆威机电", "003021"),
            ]
        ],
    }
    evidence = [
        {"tool": "get_theme_stock_candidates", "result": result},
        {
            "tool": "search_financial_news",
            "result": {
                "success": True,
                "items": [{
                    "title": "东方财富机器人频道：兆丰股份丝杠送样进入性能测试阶段",
                    "summary": "部分产品进入小批量试制，具体客户和订单金额未披露。汇川技术出现在文章其他章节。",
                    "link": "https://example.com/300695",
                    "published": "2026-07-14T08:00:00",
                    "source": "测试财经",
                }],
            },
        },
    ]

    fallback = chat_mod._build_verified_evidence_fallback(evidence)

    assert "兆丰股份 (300695) | 丝杠 | L2" in fallback
    assert "送样进入性能测试阶段" in fallback
    assert "完整候选池（L1，共 8 家）" in fallback
    assert "东方财富 (300059)" not in fallback
    assert "机器人 (300024) | 丝杠 | L2" not in fallback


def test_theme_mapping_fallback_does_not_promote_unrelated_company_sampling_news():
    result = {
        "success": True,
        "theme": "人形机器人",
        "local_universe_count": 5534,
        "candidate_count": 8,
        "items": [
            {
                "name": name,
                "symbol": symbol,
                "boards": ["人形机器人"],
                "sources": [{
                    "name": "概念板块",
                    "url": "https://example.com/theme",
                    "date": "2026-07-17",
                }],
            }
            for name, symbol in [
                ("英力股份", "300956"), ("美的集团", "000333"),
                ("南钢股份", "600282"), ("许继电气", "000400"),
                ("金固股份", "002488"), ("美格智能", "002881"),
                ("美力科技", "300611"), ("盛通股份", "002599"),
            ]
        ],
    }
    evidence = [
        {"tool": "get_theme_stock_candidates", "result": result},
        {
            "tool": "search_financial_news",
            "result": {
                "success": True,
                "items": [{
                    "title": "英力股份AI眼镜结构件已向客户送样",
                    "summary": "公司同时被市场归入人形机器人概念板块。",
                    "link": "https://example.com/300956",
                    "published": "2026-07-13T08:00:00",
                    "source": "测试财经",
                }],
            },
        },
    ]

    fallback = chat_mod._build_verified_evidence_fallback(evidence)

    assert "英力股份 (300956)" in fallback
    assert "| L2 |" not in fallback
    assert "| L3 |" not in fallback


def test_theme_mapping_uses_company_revenue_in_title_not_generic_small_batch_language():
    result = {
        "success": True,
        "theme": "人形机器人",
        "local_universe_count": 5534,
        "candidate_count": 1,
        "items": [{
            "name": "贝斯特",
            "symbol": "300580",
            "boards": ["人形机器人"],
        }],
    }
    evidence = [
        {"tool": "get_theme_stock_candidates", "result": result},
        {
            "tool": "search_financial_news",
            "result": {
                "success": True,
                "items": [{
                    "title": "贝斯特回应：人形机器人相关业务半年营收22万元",
                    "summary": "产业链企业普遍需要经历研发、送样、客户验证、小批量供货再到规模量产。",
                    "link": "https://example.com/300580",
                    "published": "2026-07-06T08:00:00",
                    "source": "测试财经",
                }],
            },
        },
    ]

    fallback = chat_mod._build_theme_mapping_fallback(result, evidence)

    assert "贝斯特 (300580) | 人形机器人相关业务 | L3" in fallback
    assert "半年营收22万元" in fallback
    assert "本轮公司级资料明确出现批量供货" not in fallback


def test_theme_mapping_fallback_binds_validation_sentence_to_unique_title_company():
    result = {
        "success": True,
        "theme": "人形机器人",
        "local_universe_count": 5534,
        "candidate_count": 8,
        "items": [
            {
                "name": name,
                "symbol": symbol,
                "boards": ["机器人概念"],
                "sources": [{
                    "name": "概念板块",
                    "url": "https://example.com/theme",
                    "date": "2026-07-17",
                }],
            }
            for name, symbol in [
                ("雷迪克", "300652"), ("美的集团", "000333"),
                ("南钢股份", "600282"), ("许继电气", "000400"),
                ("金固股份", "002488"), ("美格智能", "002881"),
                ("美力科技", "300611"), ("盛通股份", "002599"),
            ]
        ],
    }
    evidence = [
        {"tool": "get_theme_stock_candidates", "result": result},
        {
            "tool": "search_financial_news",
            "result": {
                "success": True,
                "items": [{
                    "title": "雷迪克形成机器人丝杠及精密轴承产品矩阵",
                    "summary": "公司部分重点客户项目已处于定点、关键验证或送样阶段。",
                    "link": "https://example.com/300652",
                    "published": "2026-07-16T08:00:00",
                    "source": "测试财经",
                }],
            },
        },
    ]

    fallback = chat_mod._build_verified_evidence_fallback(evidence)

    assert "雷迪克 (300652) | 丝杠 | L2" in fallback
    assert "定点、关键验证或送样阶段" in fallback


def test_mapping_synthesis_receives_runtime_verified_candidate_codes():
    messages = chat_mod._build_synthesis_messages(
        [{"role": "user", "content": "这些领域有哪些公司"}],
        [{
            "tool": "search_financial_news",
            "result": {"success": True, "items": [{"title": "雷迪克推进机器人丝杠送样"}]},
        }],
        chat_mod.THEME_COMPANY_MAPPING,
    )
    evidence_text = messages[-1]["content"]
    assert "runtime_security_entity_map" in evidence_text
    assert "雷迪克" in evidence_text
    assert "300652" in evidence_text


def test_mapping_sanitizer_drops_only_rows_without_company_level_evidence():
    mixed = (
        "| 公司/代码 | 产业链环节 | 证据等级 | 已验证事实 | 证据缺口 | 来源日期 |\n"
        "|---|---|---|---|---|---|\n"
        "| 兆威机电 (003021) | 上游 | L2 | 已披露送样 | 收入待核验 | [公告](https://example.com/003021)，2026-06-01 |\n"
        "| 机器人 (300024) | 中游 | L1 | 概念关联 | 订单缺失 | 无 |\n\n"
        "本轮仅列代表公司，未覆盖环节需进一步核验。"
    )

    sanitized = chat_mod._sanitize_mapping_answer(mixed)

    assert "兆威机电 (003021)" in sanitized
    assert "| 机器人 (300024) |" not in sanitized
    assert "因证据不足未列入" in sanitized
    assert "机器人 (300024)" in sanitized
    assert "最终保留 **1 家**" in sanitized
    assert chat_mod._playbook_answer_contract_issues(
        chat_mod.THEME_COMPANY_MAPPING,
        sanitized,
        [],
    ) == []


def test_professional_fallback_preserves_missing_amounts_instead_of_zero_filling():
    text = chat_mod._build_professional_decision_fallback({
        "success": True,
        "data_time": "2026-07-17",
        "quote_basis": "盘中快照",
        "items": [{
            "symbol": "003021",
            "name": "兆威机电",
            "snapshot": {"technical": {"indicators": {}}},
            "financials": {"items": [{"revenue_yoy": 10, "parent_net_profit_yoy": 5}]},
            "valuation": {"pe_ttm": 80, "pb_mrq": 8},
            "capital_flow": {"windows": {"10d": {}}},
            "risk_events": {"items": [], "analysis": {}},
            "announcements": {},
            "evidence_coverage": {"complete": False, "missing": ["expectations"]},
            "screening_flags": {"positive": [], "negative": []},
        }],
    })

    assert "OCF 缺失" in text
    assert "10日资金 缺失" in text
    assert "OCF 0.00亿" not in text


def test_stream_final_answer_converts_tool_history_to_text_evidence():
    """Final synthesis sends no tool messages or tools schema to Anthropic."""
    controller = _FakeController()
    captured = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _AsyncChunkStream([_mock_llm_chunk(content="最终总结")])

    history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "分析机器人产业链"},
        {"role": "assistant", "content": "先查资料", "tool_calls": [{
            "id": "call_1", "type": "function",
            "function": {"name": "search_financial_news", "arguments": "{}"},
        }]},
        {"role": "tool", "tool_call_id": "call_1", "content": '{"success":true,"items":[1]}'},
    ]
    cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod:
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._stream_final_answer_without_tools(controller, history, cfg)

    assert asyncio.run(run()) == "最终总结"
    assert "tools" not in captured
    assert all(message["role"] != "tool" for message in captured["messages"])
    assert all(not message.get("tool_calls") for message in captured["messages"])
    evidence_message = captured["messages"][-1]["content"]
    assert "本轮已核验的工具证据" in evidence_message
    assert "search_financial_news" in evidence_message


def test_react_loop_hides_planning_prose_when_tools_are_called():
    """Model planning prose stays internal; users see progress + final answer only."""
    controller = _FakeController()
    round_n = {"value": 0}

    async def fake_acompletion(**kwargs):
        round_n["value"] += 1
        if round_n["value"] == 1:
            tc = _mock_tool_call_delta(name="get_kline", arguments='{"symbol":"600519"}')
            return _AsyncChunkStream([
                _mock_llm_chunk(content="第一步：我先查行情"),
                _mock_llm_chunk(tool_calls=[tc]),
            ])
        return _AsyncChunkStream([_mock_llm_chunk(content="这是最终答案")])

    cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}
    registry = MagicMock()
    registry.get_all_schemas.return_value = []
    registry.get_tool_names.return_value = ["get_kline"]
    registry.execute.return_value = {"success": True}

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod, \
             patch("api.v1.endpoints.agent.chat._registry", registry), \
             patch("api.v1.endpoints.agent.chat._compact_tool_result", side_effect=lambda n, r: r), \
             patch("api.v1.endpoints.agent.chat._maybe_attach_search_fallback", side_effect=lambda n, a, r: r), \
             patch("api.v1.endpoints.agent.chat._format_result", return_value='{"success":true}'), \
             patch("api.v1.endpoints.agent.chat._flush_substreams", new=AsyncMock()):
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._run_react_loop(
                controller, [{"role": "user", "content": "分析"}], cfg
            )

    assert asyncio.run(run()) == "这是最终答案"
    assert all("第一步：我先查行情" not in text for text in controller.texts)
    assert any("这是最终答案" in text for text in controller.texts)


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
    assert "模型本次没有返回最终文本" in result
    assert any("模型本次没有返回最终文本" in t for t in controller.texts)


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
    assert "模型本次没有返回最终文本" in result
    assert any("模型本次没有返回最终文本" in t for t in controller.texts)


def test_empty_final_answer_uses_and_persists_verified_multi_stock_fallback():
    controller = _FakeController()
    fake_acompletion = _async_completion([_mock_llm_chunk(content=None)])
    state = {"assistant_text": ""}
    evidence = [{
        "tool": "get_multi_stock_snapshot",
        "result": {
            "success": True,
            "data_time": "2026-07-17T14:49:21+08:00",
            "quote_basis": "盘中实时快照（不是收盘价）",
            "warnings": ["维宏股份技术指标陈旧"],
            "items": [{
                "symbol": "300508",
                "name": "维宏股份",
                "quote": {
                    "price": 38.03,
                    "change_pct": -10.41,
                    "pe_dynamic": -57.72,
                    "pb_ratio": 4.97,
                },
                "financial": {"net_profit": -17928308.77, "debt_ratio_pct": 25.9},
                "technical": {"is_stale": True},
            }],
        },
    }]
    fake_cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod:
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._stream_final_answer_without_tools(
                controller,
                [{"role": "user", "content": "这些公司能买吗"}],
                fake_cfg,
                state=state,
                evidence=evidence,
            )

    result = asyncio.run(run())
    assert "维宏股份 (300508)" in result
    assert "亏损，先观察" in result
    assert "不是 PE(TTM)" in result
    assert state["assistant_text"] == result


def test_truncated_final_answer_discards_partial_text_and_uses_complete_fallback():
    controller = _FakeController()
    fake_acompletion = _async_completion([
        _mock_llm_chunk(content="写到一半的残稿", finish_reason="length"),
    ])
    evidence = [{
        "tool": "get_multi_stock_snapshot",
        "result": {
            "success": True,
            "data_time": "2026-07-17T14:49:21+08:00",
            "quote_basis": "盘中实时快照（不是收盘价）",
            "items": [{
                "symbol": "300508",
                "name": "维宏股份",
                "quote": {"price": 38.03, "change_pct": -10.41, "pe_dynamic": -57.72, "pb_ratio": 4.97},
                "financial": {"net_profit": -1.0, "debt_ratio_pct": 25.9},
                "technical": {"is_stale": False},
            }],
        },
    }]
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
    fake_acompletion = _async_completion([
        _mock_llm_chunk(content="盘中价格下跌，说明主力资金仍在流出。", finish_reason="stop"),
    ])
    evidence = [{
        "tool": "get_multi_stock_snapshot",
        "result": {
            "success": True,
            "data_time": "2026-07-17T14:49:21+08:00",
            "quote_basis": "盘中实时快照（不是收盘价）",
            "quote_is_intraday": True,
            "items": [{
                "symbol": "300508",
                "name": "维宏股份",
                "quote": {"price": 38.03, "change_pct": -10.41, "pe_dynamic": -57.72, "pb_ratio": 4.97},
                "financial": {"net_profit": -1.0, "debt_ratio_pct": 25.9},
                "technical": {"is_stale": False},
            }],
        },
    }]
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


# ---------------------------------------------------------------------------
# 上下文窗口解析 + token 估算 + 自动压缩
# ---------------------------------------------------------------------------

def test_estimate_messages_tokens_fallback_on_error():
    """litellm.token_counter 抛错时回退字符粗估，返回正整数。"""
    with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod:
        llm_mod.token_counter.side_effect = RuntimeError("model not mapped")
        n = chat_mod._estimate_messages_tokens(
            [{"role": "user", "content": "你好世界" * 100}], "openai/glm-5.2"
        )
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
    msgs = [{"role": "system", "content": "sys"}] + [
        {"role": "user", "content": f"msg{i}"} for i in range(10)
    ]
    cfg = {"model": "m", "context_window": 200000, "api_key": None, "api_base": None,
           "custom_llm_provider": None, "extra_headers": None}
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
    cfg = {"model": "m", "context_window": 200000, "api_key": None, "api_base": None,
           "custom_llm_provider": None, "extra_headers": None}
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
    cfg = {"model": "m", "context_window": 200000, "api_key": None, "api_base": None,
           "custom_llm_provider": None, "extra_headers": None}

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
    msgs = [{"role": "system", "content": "s"}] + [
        {"role": "user", "content": f"m{i}"} for i in range(12)
    ]
    cfg = {"model": "m", "context_window": 200000, "api_key": None, "api_base": None,
           "custom_llm_provider": None, "extra_headers": None}
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
    cfg = {"model": "m", "context_window": 200000, "api_key": None, "api_base": None,
           "custom_llm_provider": None, "extra_headers": None}
    with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod:
        llm_mod.token_counter.return_value = 999999  # 仍超阈值
        llm_mod.acompletion = AsyncMock()  # 不应被调用
        out = asyncio.run(chat_mod._compact_history_if_needed(msgs, cfg))
    assert out is msgs  # 不压缩
    llm_mod.acompletion.assert_not_called()  # 没调摘要 LLM


def test_run_react_loop_compacts_overlong_history_before_first_llm_call():
    """端到端：前端回传超长历史，首轮发给 litellm 前自动压缩。"""
    controller = _FakeController()
    main_calls: list[dict] = []

    async def fake_acompletion(**kwargs):
        # 区分摘要调用（stream=False 无 tools）vs 主调用（stream=True 有 tools）
        if kwargs.get("stream") is False:
            resp = MagicMock()
            resp.choices = [MagicMock(message=MagicMock(content="早期摘要内容"))]
            return resp
        # 主调用
        main_calls.append({"messages": list(kwargs["messages"]), "has_tools": "tools" in kwargs})
        return _AsyncChunkStream([_mock_llm_chunk(content="最终回答")])

    # 构造超长历史：system + 20 条消息
    aisdk_messages = [
        {"role": "user", "content": [{"type": "text", "text": "问题1"}]},
    ] + [
        {"role": "assistant" if i % 2 else "user", "content": [{"type": "text", "text": f"历史{i}"}]}
        for i in range(1, 20)
    ]

    cfg = {"model": "m", "api_key": None, "api_base": None, "extra_headers": None,
           "custom_llm_provider": None, "context_window": 200000}
    registry = MagicMock()
    registry.get_all_schemas.return_value = []
    registry.get_tool_names.return_value = []

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod, \
             patch("api.v1.endpoints.agent.chat._registry", registry), \
             patch("api.v1.endpoints.agent.chat._flush_substreams", new=AsyncMock()):
            llm_mod.acompletion = fake_acompletion
            # token_counter：压缩前超阈值，压缩后低于阈值
            llm_mod.token_counter.side_effect = [999999, 1000]
            return await chat_mod._run_react_loop(controller, aisdk_messages, cfg)

    result = asyncio.run(run())
    assert result == "最终回答"

    # 主调用收到的 messages 含摘要消息（user 角色带"早期对话摘要"）
    first_main = main_calls[0]
    summary_msgs = [m for m in first_main["messages"]
                    if m.get("role") == "user" and "早期对话摘要" in str(m.get("content", ""))]
    assert len(summary_msgs) == 1
    # 压缩不向前端推提示，避免污染对话流
    assert not any("已自动压缩" in t for t in controller.texts)
