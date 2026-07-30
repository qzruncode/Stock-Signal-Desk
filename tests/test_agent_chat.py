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


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture(autouse=True)
def disable_auth():
    auth._auth_enabled = None
    with patch("api.middlewares.auth.is_auth_enabled", return_value=False), \
         patch("src.auth.is_auth_enabled", return_value=False), \
         patch.object(
             chat_mod,
             "execute_tool_isolated",
             side_effect=lambda name, arguments, **_kwargs: chat_mod._registry.execute(name, arguments),
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


def test_pipeline_contract_failure_marks_the_run_failed() -> None:
    controller = _FakeController()
    state = {}
    error = chat_mod.OrchestratorV2Error(
        chat_mod.AgentErrorCode.PLANNER_SCHEMA_INVALID,
        "capability contract rejected result_selection",
        task_id="discover",
    )

    with patch.object(
        chat_mod,
        "plan_intent_graph_v2",
        new=AsyncMock(side_effect=error),
    ):
        result = asyncio.run(chat_mod._run_standard_task_pipeline(
            controller,
            [{"role": "user", "content": "人形机器人哪些领域最受益？"}],
            {"model": "test-model"},
            "",
            state=state,
            run_id="run-contract-failed",
        ))

    assert "内部规划契约错误" in result
    assert state["_run_status"] == "failed"
    assert state["_run_error_code"] == "planner_schema_invalid"
    assert chat_mod._terminal_run_status(state) == "failed"
    assert chat_mod._terminal_run_status({"_run_status": "partial"}) == "partial"
    assert chat_mod._terminal_run_status({}) == "completed"


def test_agent_execution_has_no_retry_classifier() -> None:
    assert not hasattr(chat_mod, "_structured_tool_failure_code")


def test_structured_completion_streams_provider_reasoning_and_rebuilds_tool_call():
    controller = _FakeController()
    received_kwargs = {}

    async def completion(**kwargs):
        received_kwargs.update(kwargs)
        return _AsyncChunkStream([
            _mock_llm_chunk(reasoning_content="先识别用户要比较的产业领域。"),
            _mock_llm_chunk(tool_calls=[
                _mock_tool_call_delta(
                    name="submit_industry_research_intent_v2",
                    arguments='{"themes":',
                ),
            ]),
            _mock_llm_chunk(tool_calls=[
                _mock_tool_call_delta(
                    name="",
                    arguments='["人形机器人"]}',
                    tc_id="",
                ),
            ]),
        ])

    response = asyncio.run(chat_mod._stream_structured_model_completion(
        controller,
        completion,
        stream=False,
        tool_choice={
            "type": "function",
            "function": {"name": "submit_industry_research_intent_v2"},
        },
    ))

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
        return _AsyncChunkStream([
            *[
                _mock_llm_chunk(reasoning_content="分析")
                for _ in range(300)
            ],
            _mock_llm_chunk(content="{}"),
        ])

    asyncio.run(chat_mod._stream_structured_model_completion(
        controller,
        completion,
        messages=[],
    ))

    reasoning = "".join(controller.reasoning)
    assert reasoning.endswith("分析" * 300 + "\n")
    assert len(controller.reasoning) < 10


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
        return _AsyncChunkStream([
            _mock_llm_chunk(reasoning_content="只修复结构化传输。"),
            _mock_llm_chunk(content='{"items":['),
            _mock_llm_chunk(content=(
                '{"board_id":"BK1100","role_id":"reducer","tier":1}]}'
            )),
        ])

    response = asyncio.run(chat_mod._stream_structured_model_completion(
        controller,
        completion,
        stream=False,
        messages=[],
    ))

    message = response["choices"][0]["message"]
    assert message["tool_calls"] == []
    assert message["content"] == (
        '{"items":[{"board_id":"BK1100",'
        '"role_id":"reducer","tier":1}]}'
    )
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
        asyncio.run(chat_mod._stream_structured_model_completion(
            controller,
            completion,
            tool_choice={
                "type": "function",
                "function": {"name": "submit_intent_outline_v2"},
            },
        ))

    assert "已等待" in "".join(controller.reasoning)


# ---------------------------------------------------------------------------
# _get_llm_config 的解析逻辑已迁移至 src.llm.anthropic_gateway，
# 相关测试见 tests/test_anthropic_gateway.py。
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 标准任务流水线及共享结果校验
# ---------------------------------------------------------------------------

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
                    "items": [{
                        "symbol": "000001", "name": "甲公司",
                        "metric": "debt_ratio", "period_basis": "latest_report",
                        "financial_value": 50.0, "value_unit": "percent",
                        "debt_ratio_pct": 50.0, "report_date": "2026-03-31",
                    }],
                    "source": "local", "data_time": "2026-07-18T22:42:40",
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
            conditions=[{
                "metric": "debt_ratio", "period_basis": "latest_report",
                "operator": "gt", "threshold": 70,
                "threshold_unit": "percent", "action": "exclude_matching",
            }],
        ),
    )

    assert "筛选未完成" in answer
    assert "000002、000003" in answer
    assert "不能把已覆盖的部分结果当作完整名单" in answer


def test_collection_financial_filter_renderer_uses_annual_revenue_and_yi_threshold() -> None:
    answer = chat_mod._build_collection_financial_filter_answer(
        [{
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
                        "symbol": "000001", "name": "甲公司",
                        "metric": "revenue", "period_basis": "previous_fiscal_year",
                        "financial_value": 499_000_000.0, "value_unit": "cny",
                        "report_date": "2025-12-31",
                    },
                    {
                        "symbol": "000002", "name": "乙公司",
                        "metric": "revenue", "period_basis": "previous_fiscal_year",
                        "financial_value": 800_000_000.0, "value_unit": "cny",
                        "report_date": "2025-12-31",
                    },
                ],
                "source": "内部财务数据源 2025-12-31 年度快照",
                "data_time": "2026-07-21T13:00:00",
            },
        }],
        chat_mod.CollectionFinancialFilterSpec(
            conditions=[{
                "metric": "revenue", "period_basis": "previous_fiscal_year",
                "operator": "lt", "threshold": 5,
                "threshold_unit": "yi_cny", "action": "exclude_matching",
            }],
        ),
    )

    assert "2025 年报营业收入" in answer
    assert "低于 5 亿元" in answer
    assert "筛除 **1 只**，筛选后保留 **1 只**" in answer
    assert "甲公司 (000001)" in answer
    assert "乙公司 (000002)" in answer


def test_collection_financial_filter_renderer_formats_large_cny_threshold() -> None:
    answer = chat_mod._build_collection_financial_filter_answer(
        [{
            "tool": "get_multi_stock_financials",
            "arguments": {
                "symbols": "000001",
                "metric": "revenue",
                "period_basis": "fiscal_year",
                "fiscal_year": 2025,
            },
            "result": {
                "success": True,
                "items": [{
                    "symbol": "000001", "name": "甲公司",
                    "metric": "revenue", "period_basis": "fiscal_year",
                    "financial_value": 800_000_000.0, "value_unit": "cny",
                    "report_date": "2025-12-31",
                }],
                "source": "local",
                "data_time": "2026-07-25T15:00:00",
            },
        }],
        chat_mod.CollectionFinancialFilterSpec(
            conditions=[{
                "metric": "revenue", "period_basis": "fiscal_year",
                "fiscal_year": 2025, "operator": "lt",
                "threshold": 500_000_000, "threshold_unit": "cny",
                "action": "exclude_matching",
            }],
        ),
    )

    assert "低于 5 亿元" in answer
    assert "5e+08" not in answer


def test_ranked_domain_renderer_uses_the_same_structured_artifact_as_followups() -> None:
    answer = chat_mod._build_ranked_domain_answer([{
        "processor": "ranked_domain_selection",
        "result": {
            "success": True,
            "items": [{
                "label": "灵巧手",
                "tier": 1,
                "rationale": "直接决定末端操作能力",
                "support_quote": "灵巧手是精细操作核心部件",
                "source_name": "测试财经",
                "source_url": "https://example.com/domain",
                "source_date": "2026-07-24",
            }],
        },
    }])

    assert "第1梯队" in answer
    assert "**灵巧手**" in answer
    assert "Planner 读取的是本轮保存的结构化领域集合" in answer


def test_ranked_domain_renderer_does_not_show_partial_failed_tiers() -> None:
    answer = chat_mod._build_ranked_domain_answer([{
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
            "items": [{
                "label": "减速器",
                "tier": 1,
                "rationale": "这只是已完成批次中的局部候选",
            }],
            "resource_outputs": {},
        },
    }])

    assert "产业受益领域排序未完成" in answer
    assert "3/5" in answer
    assert "没有展示部分梯队" in answer
    assert "减速器" not in answer
    assert "公开来源兜底" not in answer


def test_ranked_domain_renderer_hides_internal_provider_payload_errors() -> None:
    answer = chat_mod._build_ranked_domain_answer([{
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
    }])

    assert "错误代码：`planner_schema_invalid`" in answer
    assert "单次定点修复仍未通过" in answer
    assert "submit_domain_catalog_selection_v2" not in answer
    assert "Unterminated string" not in answer


def test_ranked_domain_renderer_shows_only_one_direction_without_tiers() -> None:
    answer = chat_mod._build_ranked_domain_answer([{
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
    }])

    assert "项目实时板块最受益方向" in answer
    assert "**机器人执行器**" in answer
    assert "减速器" not in answer
    assert "第1梯队" not in answer
    assert "这个方向" in answer


def test_ranked_domain_renderer_shows_top_k_as_flat_ranking() -> None:
    answer = chat_mod._build_ranked_domain_answer([{
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
    }])

    assert "前 **2 个**方向" in answer
    assert "1. **机器人执行器**" in answer
    assert "2. **减速器**" in answer
    assert "第1梯队" not in answer


def test_theme_business_renderer_explains_project_candidate_boundary() -> None:
    answer = chat_mod._build_theme_business_evidence_answer([{
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
            "items": [{
                "company_name": "汉威科技",
                "symbol": "300007",
                "matched_domains": ["六维力传感器"],
                "development_level": "mass_production",
                "reason": "目标产品已进入批量供货",
                "evidence": [{
                    "support_quote": "汉威科技六维力传感器已向头部厂商批量供货",
                    "source_name": "测试财经",
                    "source_url": "https://example.com/company",
                    "source_date": "2026-07-24",
                }],
            }],
        },
    }])

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
                            "symbol": "000001", "name": "甲公司",
                            "metric": "debt_ratio", "period_basis": "latest_report",
                            "financial_value": 50.0, "value_unit": "percent",
                            "report_date": "2026-03-31",
                        },
                        {
                            "symbol": "000002", "name": "乙公司",
                            "metric": "debt_ratio", "period_basis": "latest_report",
                            "financial_value": 80.0, "value_unit": "percent",
                            "report_date": "2026-03-31",
                        },
                    ],
                    "source": "local",
                    "data_time": "2026-07-25T15:00:00",
                },
            },
        ],
        chat_mod.CollectionFinancialFilterSpec(
            conditions=[{
                "metric": "debt_ratio", "period_basis": "latest_report",
                "operator": "gt", "threshold": 70,
                "threshold_unit": "percent", "action": "exclude_matching",
            }],
        ),
    )

    assert "候选集合来源" in answer
    assert "灵巧手" in answer
    assert "当前目录未解析" in answer
    assert "减速器" in answer
    assert "仅覆盖已解析领域" in answer
    assert "不证明公司正在大力发展该业务" in answer


def test_watchlist_theme_filter_renders_complete_intersection_without_model_rewrite():
    answer = chat_mod._build_workflow_evidence_fallback([{
        "tool": "filter_watchlist_by_theme",
        "result": {
            "success": True,
            "group": {"name": "我的自选股", "valid_security_count": 3},
            "requested_themes": ["人工智能", "机器人"],
            "items": [
                {"symbol": "002230", "name": "科大讯飞", "matched_themes": ["人工智能"], "boards": ["人工智能"]},
                {"symbol": "603662", "name": "柯力传感", "matched_themes": ["机器人"], "boards": ["机器人概念"]},
            ],
            "invalid_entries": ["未上市/无代码"],
        },
    }])

    assert "2 只" in answer
    assert "科大讯飞（002230）" in answer
    assert "柯力传感（603662）" in answer
    assert "L1 主题板块成员关系" in answer
    assert "未上市/无代码" in answer


def test_staged_news_search_renders_numbered_deduplicated_choices() -> None:
    answer = chat_mod._build_staged_news_search_answer([
        {
            "tool": "search_news",
            "result": {
                "success": True,
                "items": [{
                    "title": "宁德时代签署储能合作协议",
                    "url": "https://example.com/a",
                    "source": "第一财经",
                    "published": "2026-07-17T08:33:04",
                    "summary": "计划部署储能系统。",
                    "importance": "medium",
                }],
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
    ])

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
    answer = chat_mod._build_realtime_quote_answer([
        {
            "tool": "get_realtime_quotes",
            "result": {
                "success": True,
                "items": [{
                    "symbol": "600519", "name": "贵州茅台", "price": 1258.21,
                    "pct_chg": -0.06, "high": 1269.33, "low": 1238.98,
                    "amount": 6457100814,
                }],
                "data_time": "2026-07-17T14:29:11",
                "is_trading_session": False,
                "quote_mode": "latest_trading_day_snapshot",
                "quote_mode_label": "非交易时段的最近交易日快照，不是当前时刻实时成交",
                "source": ["eastmoney_push"],
            },
        },
        {"tool": "get_market_status", "result": {"success": True}}
    ], "market_snapshot")

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
        [{
            "tool": "get_multi_stock_decision_evidence",
            "result": {"items": [{"financials": {"items": [{
                "report_period": "2025Q4",
                "flow_basis": "single_quarter",
                "operating_cash_flow": 23325402834.08,
            }]}}]},
        }],
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
    context = chat_mod.ConversationContext.from_value({
        "version": "1",
        "turns": [{
            "request": "只看最核心公司",
            "tasks": [],
            "entities": [
                {"name": "绿的谐波", "symbol": "688017"},
                {"name": "汇川技术", "symbol": "300124"},
            ],
        }],
    })

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


# ---------------------------------------------------------------------------
# _stream_final_answer_without_tools
# ---------------------------------------------------------------------------

def test_stream_final_answer_normal_output():
    controller = _FakeController()
    received_kwargs = {}

    async def fake_acompletion(**kwargs):
        received_kwargs.update(kwargs)
        return _AsyncChunkStream([
            _mock_llm_chunk(reasoning_content="先核对证据。"),
            _mock_llm_chunk(content="总结"),
            _mock_llm_chunk(content="内容"),
        ])

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
    evidence = [{
        "tool": "get_multi_stock_snapshot",
        "result": {
            "success": True,
            "items": [
                {"name": "绿的谐波", "symbol": "688017"},
                {"name": "汇川技术", "symbol": "300124"},
            ],
        },
    }]
    header_only = (
        "| 公司/代码 | 判断 |\n"
        "|---|---|"
    )

    issues = chat_mod._generic_answer_contract_issues(header_only, evidence)

    assert "Markdown表格只有表头，没有任何数据行" in issues
    assert any("最终答案遗漏2家" in issue for issue in issues)


def test_generic_final_synthesis_repairs_header_only_table():
    controller = _FakeController()
    calls = {"value": 0}
    evidence = [{
        "tool": "get_multi_stock_snapshot",
        "result": {
            "success": True,
            "items": [{
                "name": "绿的谐波", "symbol": "688017",
                "quote": {"price": 330.12, "change_pct": -12.81, "pe_dynamic": 463.63, "pb_ratio": 17.14},
                "financial": {"net_profit": 32634147.52, "debt_ratio_pct": 9.44},
                "technical": {"is_stale": False},
            }],
            "data_time": "2026-07-18T13:39:02+08:00",
            "quote_basis": "非交易时段的最近市场快照",
        },
    }]

    async def fake_acompletion(**kwargs):
        calls["value"] += 1
        if calls["value"] == 1:
            return _AsyncChunkStream([_mock_llm_chunk(
                content="| 公司/代码 | 判断 |\n|---|---|",
                finish_reason="stop",
            )])
        return _AsyncChunkStream([_mock_llm_chunk(
            content="| 公司/代码 | 判断 |\n|---|---|\n| 绿的谐波 (688017) | 等业绩兑现 |",
            finish_reason="stop",
        )])

    cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod:
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._stream_final_answer_without_tools(
                controller,
                [{"role": "user", "content": "找出这些股票"}],
                cfg,
                evidence=evidence,
            )

    result = asyncio.run(run())
    assert "绿的谐波 (688017)" in result
    assert calls["value"] == 2


def test_industry_playbook_does_not_use_keyword_section_validator():
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
    assert result == "机器人行业会受益。"
    assert call_count["value"] == 1


def test_industry_contract_does_not_discard_safe_answer_for_link_format_only():
    safe_answer = (
        "优先级和最受益排序。上游、中游、下游构成产业链。"
        "受益机制看价值量，兑现指标看订单和产能。反证与风险是不及预期。"
        "后续持续跟踪量化指标，来源截至当前证据时间，整体置信度中等。"
        "[来源](https://example.com/a)"
    )
    assert chat_mod._playbook_answer_contract_issues(
        chat_mod.INDUSTRY_CHAIN,
        safe_answer,
        [],
    ) == []


def test_final_synthesis_does_not_install_a_local_timeout():
    controller = _FakeController()
    complete = (
        "优先级和最受益排序。上游、中游、下游产业链。"
        "受益机制看价值量，兑现指标看订单产能。反证与风险是不及预期。"
        "后续跟踪量化指标，来源截至2026-07-17，置信度中等，证据缺口明确。"
        "[来源一](https://example.com/a) [来源二](https://example.org/b)"
    )

    async def fake_acompletion(**kwargs):
        await asyncio.sleep(0.01)
        return _AsyncChunkStream([
            _mock_llm_chunk(content=complete, finish_reason="stop"),
        ])

    cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod, patch(
            "api.v1.endpoints.agent.chat.asyncio.timeout",
            side_effect=AssertionError("final synthesis must not install a local timeout"),
        ):
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


def test_final_synthesis_emits_heartbeat_while_waiting_for_a_delta():
    controller = _FakeController()

    class _DelayedAnswer:
        def __init__(self):
            self.sent = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.sent:
                raise StopAsyncIteration
            self.sent = True
            await asyncio.sleep(0.03)
            return _mock_llm_chunk(content="完成", finish_reason="stop")

    async def fake_acompletion(**kwargs):
        return _DelayedAnswer()

    cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod, patch.object(
            chat_mod,
            "MODEL_STREAM_HEARTBEAT_SECONDS",
            0.005,
        ):
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._stream_final_answer_without_tools(
                controller,
                [{"role": "user", "content": "解释一下"}],
                cfg,
            )

    assert asyncio.run(run()) == "完成"
    assert "模型仍在处理「最终答案综合」" in "".join(controller.reasoning)


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


def test_deep_research_answer_is_not_validated_by_keyword_axes():
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
        chat_mod.STOCK_DEEP_RESEARCH,
        incomplete,
        evidence,
    )
    assert issues == []

    complete = (
        "003021、688017：主营业务兑现；财务营收、净利和现金流；"
        "估值PE与一致预期；交易趋势与资金；公告催化和风险；"
        "结论等待验证，并给出成立条件与失效条件。"
    )
    assert chat_mod._playbook_answer_contract_issues(
        chat_mod.STOCK_DEEP_RESEARCH,
        complete,
        evidence,
    ) == []


def test_deep_research_contract_does_not_scan_buy_prose():
    issues = chat_mod._playbook_answer_contract_issues(
        chat_mod.STOCK_DEEP_RESEARCH,
        "我认为可以买入。",
        [{"tool": "get_multi_stock_decision_evidence", "result": {"success": False}}],
    )
    assert issues == []
    assert chat_mod._playbook_answer_contract_issues(
        chat_mod.STOCK_DEEP_RESEARCH,
        "本轮专业证据不足，因此暂不做买入判断。",
        [{"tool": "get_multi_stock_decision_evidence", "result": {"success": False}}],
    ) == []


def test_mapping_contract_does_not_parse_answer_tables_for_state():
    prose_only = (
        "兆威机电003021属于上游环节，L2证据为已披露送样；"
        "订单收入缺口待核验，来源为2026年公告。"
    )
    issues = chat_mod._playbook_answer_contract_issues(
        chat_mod.THEME_COMPANY_MAPPING,
        prose_only,
        [],
    )
    assert issues == []

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
    assert pending_issues == []


def test_mapping_contract_does_not_compare_rendered_prose_with_candidate_pool():
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

    assert issues == []


def test_mapping_contract_leaves_semantic_grounding_to_the_model():
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

    assert issues == []


def test_legacy_theme_mapping_fallback_is_removed():
    assert not hasattr(chat_mod, "_build_theme_mapping_fallback")

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
    assert chat_mod._playbook_answer_contract_issues(
        chat_mod.THEME_COMPANY_MAPPING,
        sanitized,
        [],
    ) == []


def test_professional_fallback_reports_semantic_synthesis_gap():
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
        }],
    })

    assert text.startswith("## 深度研究证据已获取，但语义综合未完成")
    assert "expectations" in text
    assert "不输出买入或规避判断" in text


def test_professional_fallback_never_infers_a_research_conclusion():
    text = chat_mod._build_professional_decision_fallback({
        "success": True,
        "thesis": "贵州茅台最新财务质量和估值如何",
        "items": [],
    })

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
            "items": [{
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
            }],
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


def test_empty_final_answer_returns_persisted_report_markdown():
    controller = _FakeController()
    fake_acompletion = _async_completion([_mock_llm_chunk(content=None)])
    fake_cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}
    evidence = [{
        "tool": "read_analysis_report",
        "arguments": {"record_id": "report-1"},
        "result": {
            "success": True,
            "record_id": "report-1",
            "markdown": "# 宁德时代正式分析报告\n\n这是数据库中保存的完整报告。",
            "markdown_length": 30,
        },
    }]

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
    evidence = [{
        "tool": "read_analysis_report",
        "arguments": {"record_id": "report-2"},
        "result": {
            "success": True,
            "record_id": "report-2",
            "markdown": "# 报告节选",
            "markdown_excerpt": True,
            "markdown_length": 24000,
        },
    }]
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
    evidence = [{
        "tool": "manage_analysis_templates",
        "arguments": {"action": "list"},
        "result": {
            "success": True,
            "action": "list",
            "item_count": 1,
            "items": [{
                "id": "template-1",
                "name": "综合多维分析",
                "is_default": True,
                "content": "### 1. 技术面\n内容\n### 2. 基本面\n内容\n### 3. 资金面\n内容",
            }],
        },
    }]

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
    evidence = [{
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
    }]

    result = chat_mod._build_verified_evidence_fallback(evidence)

    assert "当前共有 **2 个自选分组**" in result
    assert "我的自选股" in result and "新能源" in result
    assert "300750、002594" in result
    assert "模型本次没有返回最终文本" not in result


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
    assert "亏损，先观察" not in result
    assert "机械数据整理" in result
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
    async def fake_acompletion(**kwargs):
        function_name = (
            kwargs.get("tool_choice", {})
            .get("function", {})
            .get("name")
        )
        if function_name == "submit_intent_outline_v2":
            payload = {
                "nodes": [{
                    "node_id": "answer",
                    "capability": "general_response",
                    "objective": "回应问候",
                    "input_refs": [],
                    "result_selection": None,
                }],
                "needs_clarification": False,
                "clarification_question": None,
            }
        elif function_name == "submit_general_response_intent_v2":
            payload = {}
        else:
            return _AsyncChunkStream([_mock_llm_chunk(content="你好")])
        return {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": function_name,
                            "arguments": json.dumps(payload),
                        },
                    }],
                },
            }],
        }

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
    """Planner failure still returns a 200 stream and never opens data tools."""
    async def fake_acompletion(**kwargs):
        raise RuntimeError("LLM down")

    marker = "规划模型请求被上游连接终止".encode(
        "unicode_escape"
    )
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


def test_agent_chat_rate_limit_returns_retry_after_before_model_call(client):
    with patch(
        "api.v1.endpoints.agent.chat.agent_request_rate_limiter.check_and_record",
        return_value=9,
    ), patch("api.v1.endpoints.agent.chat._get_llm_config") as get_config:
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

    with patch(
        "api.v1.endpoints.agent.chat._get_llm_config",
        return_value={"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None},
    ), patch(
        "api.v1.endpoints.agent.chat.active_run_registry.try_claim",
        new=AsyncMock(side_effect=RunCapacityExceeded("full")),
    ):
        response = client.post(
            "/api/v1/agent/chat",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert response.json()["error"] == "agent_busy"
