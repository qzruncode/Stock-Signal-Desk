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



"""Focused test slice 5; shared fixtures remain local to this slice."""

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
def test_mapping_answer_is_not_rewritten_by_row_keyword_rules():
    mixed = (
        "| 公司/代码 | 产业链环节 | 证据等级 | 已验证事实 | 证据缺口 | 来源日期 |\n"
        "|---|---|---|---|---|---|\n"
        "| 兆威机电 (003021) | 上游 | L2 | 已披露送样 | 收入待核验 | [公告](https://example.com/003021)，2026-06-01 |\n"
        "| 机器人 (300024) | 中游 | L1 | 概念关联 | 订单缺失 | 无 |\n\n"
        "本轮仅列代表公司，未覆盖环节需进一步核验。"
    )

    sanitized = chat_mod._sanitize_mapping_answer(mixed)

    assert "兆威机电 (003021)" in sanitized
    assert sanitized == mixed
    assert (
        chat_mod._playbook_answer_contract_issues(
            chat_mod.THEME_COMPANY_MAPPING,
            sanitized,
            [],
        )
        == []
    )

def test_professional_fallback_reports_semantic_synthesis_gap():
    text = chat_mod._build_professional_decision_fallback(
        {
            "success": True,
            "data_time": "2026-07-17",
            "quote_basis": "盘中快照",
            "items": [
                {
                    "symbol": "003021",
                    "name": "兆威机电",
                    "snapshot": {"technical": {"indicators": {}}},
                    "financials": {"items": [{"revenue_yoy": 10, "parent_net_profit_yoy": 5}]},
                    "valuation": {"pe_ttm": 80, "pb_mrq": 8},
                    "capital_flow": {"windows": {"10d": {}}},
                    "risk_events": {"items": [], "analysis": {}},
                    "announcements": {},
                    "evidence_coverage": {"complete": False, "missing": ["expectations"]},
                }
            ],
        }
    )

    assert text.startswith("## 深度研究证据已获取，但语义综合未完成")
    assert "expectations" in text
    assert "不输出买入或规避判断" in text

def test_professional_fallback_never_infers_a_research_conclusion():
    text = chat_mod._build_professional_decision_fallback(
        {
            "success": True,
            "thesis": "贵州茅台最新财务质量和估值如何",
            "items": [],
        }
    )

    assert text.startswith("## 深度研究证据已获取，但语义综合未完成")
    assert "不输出买入或规避判断" in text

def test_professional_fallback_does_not_turn_intent_into_a_program_decision():
    text = chat_mod._build_professional_decision_fallback(
        {
            "success": True,
            "thesis": "人形机器人",
            "items": [],
        },
        decision_requested=True,
    )

    assert text.startswith("## 深度研究证据已获取，但语义综合未完成")
    assert "不输出买入或规避判断" in text

def test_professional_fallback_reports_only_structured_coverage():
    text = chat_mod._build_professional_decision_fallback(
        {
            "success": True,
            "items": [
                {
                    "symbol": "301368",
                    "name": "丰立智能",
                    "snapshot": {"technical": {"indicators": {"return_20d_pct": -21}}},
                    "financials": {"items": [{"parent_net_profit": 1}]},
                    "valuation": {"pe_ttm": -10},
                    "consensus": {
                        "success": True,
                        "coverage_available": False,
                        "coverage_status": "no_sell_side_coverage",
                    },
                    "capital_flow": {"windows": {"10d": {"main_net_inflow": -1}}},
                    "risk_events": {"items": [], "analysis": {}},
                    "announcements": {},
                    "evidence_coverage": {"complete": True, "missing": []},
                }
            ],
        },
        decision_requested=True,
    )

    assert "丰立智能 (301368)" in text
    assert "| 完整 | 无 |" in text
    assert "暂不介入" not in text

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
        {
            "role": "assistant",
            "content": "先查资料",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search_financial_news", "arguments": "{}"},
                }
            ],
        },
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
    assert "当前回答服务暂时不可用" in result
    assert any("当前回答服务暂时不可用" in t for t in controller.texts)

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
    assert "当前回答服务暂时不可用" in result
    assert any("当前回答服务暂时不可用" in t for t in controller.texts)

def test_empty_final_answer_returns_persisted_report_markdown():
    controller = _FakeController()
    fake_acompletion = _async_completion([_mock_llm_chunk(content=None)])
    fake_cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}
    evidence = [
        {
            "tool": "read_analysis_report",
            "arguments": {"record_id": "report-1"},
            "result": {
                "success": True,
                "record_id": "report-1",
                "markdown": "# 宁德时代正式分析报告\n\n这是数据库中保存的完整报告。",
                "markdown_length": 30,
            },
        }
    ]

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod:
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._stream_final_answer_without_tools(
                controller,
                [{"role": "user", "content": "读取刚完成的报告"}],
                fake_cfg,
                evidence=evidence,
            )

    result = asyncio.run(run())
    assert result == evidence[0]["result"]["markdown"]
    assert "模型本次没有返回最终文本" not in result
    assert controller.texts == [result]

def test_persisted_report_fallback_reloads_full_markdown_after_llm_compaction():
    evidence = [
        {
            "tool": "read_analysis_report",
            "arguments": {"record_id": "report-2"},
            "result": {
                "success": True,
                "record_id": "report-2",
                "markdown": "# 报告节选",
                "markdown_excerpt": True,
                "markdown_length": 24000,
            },
        }
    ]
    complete_markdown = "# 完整正式报告\n\n" + "完整内容" * 5000

    with patch("src.services.history_service.HistoryService") as service_cls:
        service_cls.return_value.get_markdown_report.return_value = complete_markdown
        result = chat_mod._build_verified_evidence_fallback(evidence)

    assert result == complete_markdown
    service_cls.return_value.get_markdown_report.assert_called_once_with("report-2")

def test_empty_final_answer_renders_analysis_template_list():
    controller = _FakeController()
    fake_acompletion = _async_completion([_mock_llm_chunk(content=None)])
    fake_cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}
    evidence = [
        {
            "tool": "manage_analysis_templates",
            "arguments": {"action": "list"},
            "result": {
                "success": True,
                "action": "list",
                "item_count": 1,
                "items": [
                    {
                        "id": "template-1",
                        "name": "综合多维分析",
                        "is_default": True,
                        "content": "### 1. 技术面\n内容\n### 2. 基本面\n内容\n### 3. 资金面\n内容",
                    }
                ],
            },
        }
    ]

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod:
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._stream_final_answer_without_tools(
                controller,
                [{"role": "user", "content": "列出我的分析模板"}],
                fake_cfg,
                evidence=evidence,
            )

    result = asyncio.run(run())
    assert "当前共有 **1 个分析模板**" in result
    assert "综合多维分析" in result
    assert "当前默认模板" in result
    assert "技术面、基本面、资金面" in result
    assert "模型本次没有返回最终文本" not in result

def test_empty_final_answer_renders_complete_watchlist_group_inventory():
    evidence = [
        {
            "tool": "manage_watchlist_groups",
            "arguments": {"action": "list"},
            "result": {
                "success": True,
                "action": "list",
                "item_count": 2,
                "groups": [
                    {
                        "id": "default",
                        "name": "我的自选股",
                        "codes": ["600519"],
                        "count": 1,
                        "is_default": True,
                    },
                    {
                        "id": 2,
                        "name": "新能源",
                        "codes": ["300750", "002594"],
                        "count": 2,
                        "is_default": False,
                    },
                ],
            },
        }
    ]

    result = chat_mod._build_verified_evidence_fallback(evidence)

    assert "当前共有 **2 个自选分组**" in result
    assert "我的自选股" in result and "新能源" in result
    assert "300750、002594" in result
    assert "模型本次没有返回最终文本" not in result

def test_empty_final_answer_uses_and_persists_verified_multi_stock_fallback():
    controller = _FakeController()
    fake_acompletion = _async_completion([_mock_llm_chunk(content=None)])
    state = {"assistant_text": ""}
    evidence = [
        {
            "tool": "get_multi_stock_snapshot",
            "result": {
                "success": True,
                "data_time": "2026-07-17T14:49:21+08:00",
                "quote_basis": "盘中实时快照（不是收盘价）",
                "warnings": ["维宏股份技术指标陈旧"],
                "items": [
                    {
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
                state=state,
                evidence=evidence,
            )

    result = asyncio.run(run())
    assert "维宏股份 (300508)" in result
    assert "亏损，先观察" not in result
    assert "机械数据整理" in result
    assert "不是 PE(TTM)" in result
    assert state["assistant_text"] == result
