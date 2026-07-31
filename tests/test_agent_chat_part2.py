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



"""Focused test slice 2; shared fixtures remain local to this slice."""

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
def test_collection_financial_filter_renderer_fails_closed_on_missing_batch() -> None:
    answer = chat_mod._build_collection_financial_filter_answer(
        [
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
                            "debt_ratio_pct": 50.0,
                            "report_date": "2026-03-31",
                        }
                    ],
                    "source": "local",
                    "data_time": "2026-07-18T22:42:40",
                },
            },
            {
                "tool": "get_multi_stock_financials",
                "arguments": {
                    "symbols": "000003",
                    "metric": "debt_ratio",
                    "period_basis": "latest_report",
                },
                "result": {"success": False, "error": "timeout"},
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

    assert "筛选未完成" in answer
    assert "000002、000003" in answer
    assert "不能把已覆盖的部分结果当作完整名单" in answer

def test_collection_financial_filter_renderer_uses_annual_revenue_and_yi_threshold() -> None:
    answer = chat_mod._build_collection_financial_filter_answer(
        [
            {
                "tool": "get_multi_stock_financials",
                "arguments": {
                    "symbols": "000001,000002",
                    "metric": "revenue",
                    "period_basis": "previous_fiscal_year",
                },
                "result": {
                    "success": True,
                    "items": [
                        {
                            "symbol": "000001",
                            "name": "甲公司",
                            "metric": "revenue",
                            "period_basis": "previous_fiscal_year",
                            "financial_value": 499_000_000.0,
                            "value_unit": "cny",
                            "report_date": "2025-12-31",
                        },
                        {
                            "symbol": "000002",
                            "name": "乙公司",
                            "metric": "revenue",
                            "period_basis": "previous_fiscal_year",
                            "financial_value": 800_000_000.0,
                            "value_unit": "cny",
                            "report_date": "2025-12-31",
                        },
                    ],
                    "source": "内部财务数据源 2025-12-31 年度快照",
                    "data_time": "2026-07-21T13:00:00",
                },
            }
        ],
        chat_mod.CollectionFinancialFilterSpec(
            conditions=[
                {
                    "metric": "revenue",
                    "period_basis": "previous_fiscal_year",
                    "operator": "lt",
                    "threshold": 5,
                    "threshold_unit": "yi_cny",
                    "action": "exclude_matching",
                }
            ],
        ),
    )

    assert "2025 年报营业收入" in answer
    assert "低于 5 亿元" in answer
    assert "筛除 **1 只**，筛选后保留 **1 只**" in answer
    assert "甲公司 (000001)" in answer
    assert "乙公司 (000002)" in answer

def test_collection_financial_filter_renderer_formats_large_cny_threshold() -> None:
    answer = chat_mod._build_collection_financial_filter_answer(
        [
            {
                "tool": "get_multi_stock_financials",
                "arguments": {
                    "symbols": "000001",
                    "metric": "revenue",
                    "period_basis": "fiscal_year",
                    "fiscal_year": 2025,
                },
                "result": {
                    "success": True,
                    "items": [
                        {
                            "symbol": "000001",
                            "name": "甲公司",
                            "metric": "revenue",
                            "period_basis": "fiscal_year",
                            "financial_value": 800_000_000.0,
                            "value_unit": "cny",
                            "report_date": "2025-12-31",
                        }
                    ],
                    "source": "local",
                    "data_time": "2026-07-25T15:00:00",
                },
            }
        ],
        chat_mod.CollectionFinancialFilterSpec(
            conditions=[
                {
                    "metric": "revenue",
                    "period_basis": "fiscal_year",
                    "fiscal_year": 2025,
                    "operator": "lt",
                    "threshold": 500_000_000,
                    "threshold_unit": "cny",
                    "action": "exclude_matching",
                }
            ],
        ),
    )

    assert "低于 5 亿元" in answer
    assert "5e+08" not in answer

def test_ranked_domain_renderer_uses_the_same_structured_artifact_as_followups() -> None:
    answer = chat_mod._build_ranked_domain_answer(
        [
            {
                "processor": "ranked_domain_selection",
                "result": {
                    "success": True,
                    "items": [
                        {
                            "label": "灵巧手",
                            "tier": 1,
                            "rationale": "直接决定末端操作能力",
                            "support_quote": "灵巧手是精细操作核心部件",
                            "source_name": "测试财经",
                            "source_url": "https://example.com/domain",
                            "source_date": "2026-07-24",
                        }
                    ],
                },
            }
        ]
    )

    assert "第1梯队" in answer
    assert "**灵巧手**" in answer
    assert "Planner 读取的是本轮保存的结构化领域集合" in answer

def test_ranked_domain_renderer_does_not_show_partial_failed_tiers() -> None:
    answer = chat_mod._build_ranked_domain_answer(
        [
            {
                "processor": "ranked_domain_selection",
                "result": {
                    "success": False,
                    "partial": True,
                    "source_scope": "project_live_board_catalog",
                    "catalog_count": 495,
                    "batch_total": 5,
                    "batch_completed": 3,
                    "coverage_complete": False,
                    "ranking_complete": False,
                    "errors": ["实时板块语义筛选只完成 3/5 个批次。"],
                    "items": [
                        {
                            "label": "减速器",
                            "tier": 1,
                            "rationale": "这只是已完成批次中的局部候选",
                        }
                    ],
                    "resource_outputs": {},
                },
            }
        ]
    )

    assert "产业受益领域排序未完成" in answer
    assert "3/5" in answer
    assert "没有展示部分梯队" in answer
    assert "减速器" not in answer
    assert "公开来源兜底" not in answer

def test_ranked_domain_renderer_hides_internal_provider_payload_errors() -> None:
    answer = chat_mod._build_ranked_domain_answer(
        [
            {
                "processor": "ranked_domain_selection",
                "result": {
                    "success": False,
                    "partial": False,
                    "catalog_total": 504,
                    "catalog_supplied": 504,
                    "coverage_complete": False,
                    "ranking_complete": False,
                    "error_code": "planner_schema_invalid",
                    "errors": [
                        "submit_domain_catalog_selection_v2 remained invalid: "
                        "Unterminated string at line 1 column 664"
                    ],
                    "items": [],
                },
            }
        ]
    )

    assert "错误代码：`planner_schema_invalid`" in answer
    assert "单次定点修复仍未通过" in answer
    assert "submit_domain_catalog_selection_v2" not in answer
    assert "Unterminated string" not in answer

def test_ranked_domain_renderer_shows_only_one_direction_without_tiers() -> None:
    answer = chat_mod._build_ranked_domain_answer(
        [
            {
                "processor": "ranked_domain_selection",
                "result": {
                    "success": True,
                    "source_scope": "project_live_board_catalog",
                    "catalog_count": 495,
                    "result_selection": {
                        "mode": "best_one",
                        "max_items": 1,
                    },
                    "items": [
                        {
                            "label": "机器人执行器",
                            "board_code": "BK1145",
                            "tier": 1,
                            "rationale": "直接承接关节驱动价值量",
                        },
                        {
                            "label": "减速器",
                            "board_code": "BK1100",
                            "tier": 1,
                            "rationale": "关节传动核心部件",
                        },
                    ],
                },
            }
        ]
    )

    assert "项目实时板块最受益方向" in answer
    assert "**机器人执行器**" in answer
    assert "减速器" not in answer
    assert "第1梯队" not in answer
    assert "这个方向" in answer

def test_ranked_domain_renderer_shows_top_k_as_flat_ranking() -> None:
    answer = chat_mod._build_ranked_domain_answer(
        [
            {
                "processor": "ranked_domain_selection",
                "result": {
                    "success": True,
                    "source_scope": "project_live_board_catalog",
                    "catalog_count": 495,
                    "result_selection": {
                        "mode": "top_k",
                        "max_items": 2,
                    },
                    "items": [
                        {
                            "label": "机器人执行器",
                            "board_code": "BK1145",
                            "tier": 1,
                            "rationale": "直接承接关节驱动价值量",
                        },
                        {
                            "label": "减速器",
                            "board_code": "BK1100",
                            "tier": 1,
                            "rationale": "关节传动核心部件",
                        },
                    ],
                },
            }
        ]
    )

    assert "前 **2 个**方向" in answer
    assert "1. **机器人执行器**" in answer
    assert "2. **减速器**" in answer
    assert "第1梯队" not in answer

def test_theme_business_renderer_explains_project_candidate_boundary() -> None:
    answer = chat_mod._build_theme_business_evidence_answer(
        [
            {
                "processor": "company_evidence_binding",
                "result": {
                    "success": True,
                    "candidate_scope": "candidate_collection",
                    "candidate_count": 42,
                    "analyzed_candidate_count": 42,
                    "candidate_coverage_complete": True,
                    "screening_mode": "per_security_full_analysis",
                    "verdict_counts": {
                        "pass": 1,
                        "fail": 36,
                        "insufficient": 4,
                        "error": 1,
                    },
                    "items": [
                        {
                            "company_name": "汉威科技",
                            "symbol": "300007",
                            "matched_domains": ["六维力传感器"],
                            "development_level": "mass_production",
                            "reason": "目标产品已进入批量供货",
                            "evidence": [
                                {
                                    "support_quote": "汉威科技六维力传感器已向头部厂商批量供货",
                                    "source_name": "测试财经",
                                    "source_url": "https://example.com/company",
                                    "source_date": "2026-07-24",
                                }
                            ],
                        }
                    ],
                },
            }
        ]
    )

    assert "候选池共有 **42 家**" in answer
    assert "**42 家**分别建立" in answer
    assert "通过：**1 家**" in answer
    assert "不符合：**36 家**" in answer
    assert "证据不足：**4 家**" in answer
    assert "分析错误：**1 家**" in answer
    assert "本轮逐股覆盖完整" in answer
    assert "汉威科技 (300007)" in answer
    assert "量产/批量交付" in answer
    assert "逐股兜底" in answer
