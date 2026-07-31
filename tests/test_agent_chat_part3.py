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



"""Focused test slice 3; shared fixtures remain local to this slice."""

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
def test_collection_financial_filter_renderer_preserves_upstream_domain_boundary() -> None:
    answer = chat_mod._build_collection_financial_filter_answer(
        [
            {
                "tool": "get_domain_stock_candidates",
                "arguments": {},
                "result": {
                    "success": True,
                    "domain_results": [
                        {
                            "domain": "灵巧手",
                            "mapping_type": "unresolved",
                            "lookup_themes": [],
                            "candidate_count": 0,
                            "mapping_rationale": "没有严格窄板块",
                        },
                        {
                            "domain": "减速器",
                            "mapping_type": "catalog_binding",
                            "lookup_themes": ["减速器"],
                            "candidate_count": 2,
                        },
                    ],
                },
            },
            {
                "tool": "get_multi_stock_financials",
                "arguments": {
                    "symbols": "000001,000002",
                    "metric": "debt_ratio",
                    "period_basis": "latest_report",
                },
                "result": {
                    "success": True,
                    "items": [
                        {
                            "symbol": "000001",
                            "name": "甲公司",
                            "metric": "debt_ratio",
                            "period_basis": "latest_report",
                            "financial_value": 50.0,
                            "value_unit": "percent",
                            "report_date": "2026-03-31",
                        },
                        {
                            "symbol": "000002",
                            "name": "乙公司",
                            "metric": "debt_ratio",
                            "period_basis": "latest_report",
                            "financial_value": 80.0,
                            "value_unit": "percent",
                            "report_date": "2026-03-31",
                        },
                    ],
                    "source": "local",
                    "data_time": "2026-07-25T15:00:00",
                },
            },
        ],
        chat_mod.CollectionFinancialFilterSpec(
            conditions=[
                {
                    "metric": "debt_ratio",
                    "period_basis": "latest_report",
                    "operator": "gt",
                    "threshold": 70,
                    "threshold_unit": "percent",
                    "action": "exclude_matching",
                }
            ],
        ),
    )

    assert "候选集合来源" in answer
    assert "灵巧手" in answer
    assert "当前目录未解析" in answer
    assert "减速器" in answer
    assert "仅覆盖已解析领域" in answer
    assert "不证明公司正在大力发展该业务" in answer

def test_watchlist_theme_filter_renders_complete_intersection_without_model_rewrite():
    answer = chat_mod._build_workflow_evidence_fallback(
        [
            {
                "tool": "filter_watchlist_by_theme",
                "result": {
                    "success": True,
                    "group": {"name": "我的自选股", "valid_security_count": 3},
                    "requested_themes": ["人工智能", "机器人"],
                    "items": [
                        {
                            "symbol": "002230",
                            "name": "科大讯飞",
                            "matched_themes": ["人工智能"],
                            "boards": ["人工智能"],
                        },
                        {
                            "symbol": "603662",
                            "name": "柯力传感",
                            "matched_themes": ["机器人"],
                            "boards": ["机器人概念"],
                        },
                    ],
                    "invalid_entries": ["未上市/无代码"],
                },
            }
        ]
    )

    assert "2 只" in answer
    assert "科大讯飞（002230）" in answer
    assert "柯力传感（603662）" in answer
    assert "L1 主题板块成员关系" in answer
    assert "未上市/无代码" in answer

def test_staged_news_search_renders_numbered_deduplicated_choices() -> None:
    answer = chat_mod._build_staged_news_search_answer(
        [
            {
                "tool": "search_news",
                "result": {
                    "success": True,
                    "items": [
                        {
                            "title": "宁德时代签署储能合作协议",
                            "url": "https://example.com/a",
                            "source": "第一财经",
                            "published": "2026-07-17T08:33:04",
                            "summary": "计划部署储能系统。",
                            "importance": "medium",
                        }
                    ],
                },
            },
            {
                "tool": "search_financial_news",
                "result": {
                    "success": True,
                    "items": [
                        {
                            "title": "宁德时代签署储能合作协议",
                            "link": "https://example.com/a",
                            "source": "第一财经",
                        },
                        {
                            "title": "宁德时代将公布中期业绩",
                            "link": "https://example.com/b",
                            "source": "财联社",
                            "published": "2026-07-20",
                            "summary": "公司将公布业绩。",
                        },
                    ],
                },
            },
        ]
    )

    assert "2 条" in answer
    assert answer.count("https://example.com/a") == 1
    assert "| 1 |" in answer and "| 2 |" in answer
    assert "回复编号" in answer
    assert "不提前做投资分析" in answer

def test_final_claim_validator_does_not_classify_financial_prose():
    issues = chat_mod._unsupported_final_claims(
        "贵州茅台 PE(TTM) 为 14.43 倍。",
        [{"tool": "get_realtime_quotes", "result": {"success": True}}],
    )

    assert issues == []

def test_quote_only_answer_is_deterministic_and_marks_closed_session():
    answer = chat_mod._build_realtime_quote_answer(
        [
            {
                "tool": "get_realtime_quotes",
                "result": {
                    "success": True,
                    "items": [
                        {
                            "symbol": "600519",
                            "name": "贵州茅台",
                            "price": 1258.21,
                            "pct_chg": -0.06,
                            "high": 1269.33,
                            "low": 1238.98,
                            "amount": 6457100814,
                        }
                    ],
                    "data_time": "2026-07-17T14:29:11",
                    "is_trading_session": False,
                    "quote_mode": "latest_trading_day_snapshot",
                    "quote_mode_label": "非交易时段的最近交易日快照，不是当前时刻实时成交",
                    "source": ["eastmoney_push"],
                },
            },
            {"tool": "get_market_status", "result": {"success": True}},
        ],
        "market_snapshot",
    )

    assert "贵州茅台 (600519)" in answer
    assert "不是当前时刻的实时成交" in answer
    assert "不等同于收盘价" in answer
    assert "上证指数" not in answer

def test_final_claim_validator_does_not_keyword_match_market_prose():
    issues = chat_mod._unsupported_final_claims(
        "上证指数下跌 3.05%，贵州茅台表现出很强的抗跌性。",
        [{"tool": "get_realtime_quotes", "result": {"success": True}}],
    )

    assert issues == []

def test_answer_rejects_wrong_weekday_for_explicit_date():
    issues = chat_mod._unsupported_final_claims(
        "数据来自 2026-07-17（周四）收盘。",
        [{"tool": "get_kline", "result": {"data_time": "2026-07-17"}}],
    )

    assert any("应为周五" in issue for issue in issues)

def test_final_claim_validator_does_not_keyword_match_relative_time_prose():
    issues = chat_mod._unsupported_final_claims(
        "今日收盘价为 1252.60 元。",
        [{"tool": "get_kline", "result": {"data_time": "2026-07-17"}}],
    )

    assert issues == []

def test_final_claim_validator_does_not_keyword_match_accounting_prose():
    issues = chat_mod._unsupported_final_claims(
        "2025年全年经营现金流净额233.25亿元。",
        [
            {
                "tool": "get_multi_stock_decision_evidence",
                "result": {
                    "items": [
                        {
                            "financials": {
                                "items": [
                                    {
                                        "report_period": "2025Q4",
                                        "flow_basis": "single_quarter",
                                        "operating_cash_flow": 23325402834.08,
                                    }
                                ]
                            }
                        }
                    ]
                },
            }
        ],
    )

    assert issues == []

def test_final_claim_validator_does_not_keyword_match_channel_price_prose():
    issues = chat_mod._unsupported_final_claims(
        "成立条件是飞天茅台一批价企稳，失效条件是批价跌破2000元。",
        [{"tool": "get_multi_stock_decision_evidence", "result": {"items": []}}],
    )

    assert issues == []

def test_previous_answer_entities_are_not_recovered_from_rendered_prose():
    assert not hasattr(chat_mod, "_legacy_previous_answer_entities")

def test_structured_context_defines_reference_scope_without_answer_parsing():
    context = chat_mod.ConversationContext.from_value(
        {
            "version": "1",
            "turns": [
                {
                    "request": "只看最核心公司",
                    "tasks": [],
                    "entities": [
                        {"name": "绿的谐波", "symbol": "688017"},
                        {"name": "汇川技术", "symbol": "300124"},
                    ],
                }
            ],
        }
    )

    assert context.latest_entities() == [
        {"name": "绿的谐波", "symbol": "688017"},
        {"name": "汇川技术", "symbol": "300124"},
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

def test_stream_final_answer_normal_output():
    controller = _FakeController()
    received_kwargs = {}

    async def fake_acompletion(**kwargs):
        received_kwargs.update(kwargs)
        return _AsyncChunkStream(
            [
                _mock_llm_chunk(reasoning_content="先核对证据。"),
                _mock_llm_chunk(content="总结"),
                _mock_llm_chunk(content="内容"),
            ]
        )

    fake_cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod:
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._stream_final_answer_without_tools(
                controller, [{"role": "user", "content": "hi"}], fake_cfg
            )

    result = asyncio.run(run())
    assert result == "总结内容"
    assert "分析过程都必须使用简体中文" in received_kwargs["messages"][0]["content"]
    assert "模型可见分析 · 最终答案综合" in "".join(controller.reasoning)
    assert "先核对证据。" in "".join(controller.reasoning)
    # Final synthesis is buffered until completion so a contract failure can
    # be atomically replaced with a complete evidence-backed fallback.
    assert controller.texts == ["总结内容"]

def test_generic_contract_rejects_header_only_table_and_missing_batch_rows():
    evidence = [
        {
            "tool": "get_multi_stock_snapshot",
            "result": {
                "success": True,
                "items": [
                    {"name": "绿的谐波", "symbol": "688017"},
                    {"name": "汇川技术", "symbol": "300124"},
                ],
            },
        }
    ]
    header_only = "| 公司/代码 | 判断 |\n" "|---|---|"

    issues = chat_mod._generic_answer_contract_issues(header_only, evidence)

    assert "Markdown表格只有表头，没有任何数据行" in issues
    assert any("最终答案遗漏2家" in issue for issue in issues)
