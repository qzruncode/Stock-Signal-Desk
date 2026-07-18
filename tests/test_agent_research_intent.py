from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from src.agent.analysis_playbooks import (
    QUANTITATIVE_SCREENING,
    THEME_COMPANY_MAPPING,
    mandatory_tool_calls,
    select_playbook_for_intent,
)
from src.agent.research_intent import (
    ResearchIntent,
    _conversation_for_resolution,
    resolve_research_intent,
)


def _tool_response(payload: dict) -> SimpleNamespace:
    function = SimpleNamespace(
        name="resolve_research_intent",
        arguments=json.dumps(payload, ensure_ascii=False),
    )
    message = SimpleNamespace(
        tool_calls=[SimpleNamespace(function=function)],
        content=None,
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _quantitative_spec(**updates) -> dict:
    spec = {
        "version": "1.0",
        "universe": {
            "status": "active", "markets": ["sh", "sz", "bj"],
            "include_st": True, "min_listing_trading_days": 250,
            "price_adjustment": "qfq",
        },
        "technical_rule": {
            "strategy": "atr_relative_frequency", "atr_period": 14,
            "atr_average": "sma", "baseline_period": 60,
            "baseline_average": "sma", "threshold_operator": "divide",
            "threshold_value": 1.27, "daily_comparison": "gt",
            "lookback_days": 250, "min_qualified_days": 175,
            "min_qualified_ratio_pct": 70,
        },
        "financial_filters": [
            {"field": "revenue_ttm", "operator": "gt", "value": 500_000_000},
            {"field": "deducted_net_profit_ttm", "operator": "gt", "value": 0},
            {"field": "debt_ratio", "operator": "lt", "value": 70},
        ],
        "sort": {"field": "qualified_ratio_pct", "order": "desc"},
        "output_fields": [
            "current_atr_pct", "long_term_mean_pct", "dynamic_warning_pct",
            "qualified_days", "qualified_ratio_pct", "revenue_ttm",
            "deducted_net_profit_ttm", "debt_ratio", "financial_report_period",
            "financial_source", "latest_trade_date",
        ],
        "preview_limit": 10,
    }
    spec.update(updates)
    return spec


def test_semantic_intent_uses_latest_explicit_subtopic_and_structured_dimensions() -> None:
    captured = {}

    async def completion(**kwargs):
        captured.update(kwargs)
        return _tool_response({
            "kind": "theme_company_mapping",
            "topic": "AI芯片",
            "discovery_theme": "AI芯片",
            "selection_mode": "ranked_shortlist",
            "thesis_requirements": ["AI芯片", "量产或收入兑现"],
            "entity_scope": "none",
            "entities": [],
            "objective": "找出AI芯片核心受益的A股公司",
            "research_dimensions": ["GPU", "NPU", "训练芯片", "推理芯片", "订单", "收入"],
            "output_requirements": ["完整候选池", "直接业务证据分级"],
            "needs_clarification": False,
            "clarification_question": None,
            "confidence": 0.98,
        })

    intent = asyncio.run(resolve_research_intent(
        [
            {"role": "user", "content": "帮我分析AI产业链"},
            {"role": "assistant", "content": "上游包括AI芯片和服务器。"},
            {"role": "user", "content": "看下上面说的AI芯片，哪些公司核心受益"},
        ],
        {"model": "test-model"},
        completion=completion,
    ))

    assert intent.topic == "AI芯片"
    assert intent.kind == "theme_company_mapping"
    assert captured["stream"] is False
    assert captured["tool_choice"]["function"]["name"] == "resolve_research_intent"
    assert len(captured["messages"]) == 2

    playbook = select_playbook_for_intent(intent)
    assert playbook == THEME_COMPANY_MAPPING
    calls = mandatory_tool_calls(playbook, [], [], intent=intent)
    arguments = [json.loads(call["arguments"]) for call in calls]
    assert arguments[0]["theme"] == "AI芯片"
    assert "GPU" in arguments[2]["query"]
    assert "NPU" in arguments[2]["query"]
    assert "丝杠" not in arguments[2]["query"]


def test_semantic_intent_rejects_missing_clarification_question() -> None:
    async def completion(**_kwargs):
        return _tool_response({
            "kind": "investment_decision",
            "topic": None,
            "discovery_theme": None,
            "selection_mode": "none",
            "thesis_requirements": [],
            "entity_scope": "none",
            "entities": [],
            "objective": "判断能否买入",
            "research_dimensions": [],
            "output_requirements": [],
            "needs_clarification": True,
            "clarification_question": None,
            "confidence": 0.3,
        })

    with pytest.raises(ValueError, match="clarification_question"):
        asyncio.run(resolve_research_intent(
            [{"role": "user", "content": "现在能买吗"}],
            {"model": "test-model"},
            completion=completion,
        ))


def test_playbook_selection_is_enum_driven_not_wording_driven() -> None:
    intent = ResearchIntent(
        kind="industry_chain",
        topic="光通信",
        objective="解释价值如何传导",
        research_dimensions=["价值量", "供需", "订单"],
        output_requirements=["受益顺序"],
        confidence=0.9,
    )

    assert select_playbook_for_intent(intent).id == "industry_chain_research"


def test_quantitative_screening_routes_to_one_deterministic_tool() -> None:
    intent = ResearchIntent(
        kind="quantitative_screening",
        topic="ATR相对波动率全市场筛选",
        objective="按精确公式筛选全部A股",
        quantitative_screen_spec=_quantitative_spec(),
    )
    playbook = select_playbook_for_intent(intent)
    assert playbook == QUANTITATIVE_SCREENING
    calls = mandatory_tool_calls(playbook, [], [], intent=intent)
    assert [call["name"] for call in calls] == ["screen_atr_volatility_stocks"]
    arguments = json.loads(calls[0]["arguments"])
    assert arguments["refresh_if_stale"] is True
    assert arguments["screen_spec"]["technical_rule"]["atr_period"] == 14
    assert arguments["screen_spec"]["financial_filters"][0]["value"] == 500_000_000


def test_quantitative_intent_rejects_silent_defaults_and_dropped_unsupported_conditions() -> None:
    with pytest.raises(ValueError, match="完整 quantitative_screen_spec"):
        ResearchIntent(
            kind="quantitative_screening",
            topic="ATR筛选",
            objective="按ATR筛选",
        )
    with pytest.raises(ValueError, match="不能静默丢弃"):
        ResearchIntent(
            kind="quantitative_screening",
            topic="RSI与ATR筛选",
            objective="同时满足RSI与ATR",
            quantitative_screen_spec=_quantitative_spec(),
            unsupported_requirements=["RSI>70"],
            needs_clarification=False,
        )


def test_quantitative_intent_can_request_clarification_for_unsupported_metric() -> None:
    intent = ResearchIntent(
        kind="quantitative_screening",
        topic="RSI与ATR筛选",
        objective="同时满足RSI与ATR",
        quantitative_screen_spec=None,
        unsupported_requirements=["RSI>70"],
        needs_clarification=True,
        clarification_question="当前筛选器尚不支持RSI，是否仅保留ATR条件？",
    )
    assert intent.needs_clarification is True
    assert intent.unsupported_requirements == ["RSI>70"]


def test_semantic_quantitative_spec_preserves_changed_user_conditions_end_to_end() -> None:
    captured = {}
    changed_spec = copy.deepcopy(_quantitative_spec())
    changed_spec["technical_rule"].update({
        "atr_period": 20,
        "atr_average": "ema",
        "baseline_period": 90,
        "baseline_average": "ema",
        "threshold_operator": "multiply",
        "threshold_value": 1.1,
        "lookback_days": 120,
        "min_qualified_days": 72,
        "min_qualified_ratio_pct": 60,
    })
    changed_spec["financial_filters"][0]["value"] = 1_000_000_000

    async def completion(**kwargs):
        captured.update(kwargs)
        return _tool_response({
            "kind": "quantitative_screening",
            "topic": "可配置ATR相对波动率筛选",
            "discovery_theme": None,
            "selection_mode": "complete_inventory",
            "thesis_requirements": [],
            "entity_scope": "none",
            "entities": [],
            "objective": "使用修改后的条件筛选全部A股",
            "research_dimensions": ["ATR", "TTM财务"],
            "output_requirements": ["完整CSV"],
            "quantitative_screen_spec": changed_spec,
            "unsupported_requirements": [],
            "needs_clarification": False,
            "clarification_question": None,
            "confidence": 0.99,
        })

    intent = asyncio.run(resolve_research_intent(
        [
            {"role": "assistant", "content": "上一轮按14日SMA、60日均线、营收5亿元执行。"},
            {"role": "user", "content": "改成20日EMA、90日EMA、长期线乘1.1、近120日72天和60%，营收改成10亿元。"},
        ],
        {"model": "test-model"},
        completion=completion,
    ))

    rule = intent.quantitative_screen_spec.technical_rule
    assert rule.atr_period == 20 and rule.atr_average == "ema"
    assert rule.baseline_period == 90 and rule.threshold_operator == "multiply"
    assert rule.lookback_days == 120 and rule.min_qualified_days == 72
    calls = mandatory_tool_calls(QUANTITATIVE_SCREENING, [], [], intent=intent)
    arguments = json.loads(calls[0]["arguments"])
    assert arguments["screen_spec"]["technical_rule"]["atr_period"] == 20
    assert arguments["screen_spec"]["financial_filters"][0]["value"] == 1_000_000_000
    assert "不能只给本轮修改的字段" in captured["messages"][0]["content"]


def test_semantic_intent_normalizes_gateway_null_and_topic_only_scope() -> None:
    async def completion(**_kwargs):
        return _tool_response({
            "kind": "theme_company_mapping",
            "topic": "AI计算芯片（训练与推理）",
            "discovery_theme": "AI芯片",
            "selection_mode": "ranked_shortlist",
            "thesis_requirements": ["训练或推理芯片", "量产或收入兑现"],
            "entity_scope": "current_message",
            "entities": [],
            "objective": "找出核心受益公司",
            "research_dimensions": ["训练芯片", "推理芯片"],
            "output_requirements": ["公司名单"],
            "needs_clarification": False,
            "clarification_question": "null",
            "confidence": 0.95,
        })

    intent = asyncio.run(resolve_research_intent(
        [{"role": "user", "content": "AI芯片有哪些核心受益公司"}],
        {"model": "test-model"},
        completion=completion,
        current_entities=[],
    ))

    assert intent.clarification_question is None
    assert intent.entity_scope == "none"
    assert intent.normalized_discovery_theme == "AI芯片"
    assert intent.selection_mode == "ranked_shortlist"


def test_intent_context_keeps_conclusion_boundary_without_full_report_body() -> None:
    long_answer = "第一梯队：端侧AI SoC与推理芯片\n" + ("正文数据" * 2000) + "\n量产、订单或收入兑现"
    compact = _conversation_for_resolution([
        {"role": "user", "content": "按消费终端逻辑分析"},
        {"role": "assistant", "content": long_answer},
        {"role": "user", "content": "按上面第一梯队找最符合公司"},
    ])

    assert len(compact[1]["content"]) < 3500
    assert "第一梯队：端侧AI SoC与推理芯片" in compact[1]["content"]
    assert "量产、订单或收入兑现" in compact[1]["content"]


def test_intent_does_not_treat_output_shape_as_company_eligibility() -> None:
    intent = ResearchIntent(
        kind="theme_company_mapping",
        topic="消费级端侧AI SoC与推理芯片",
        discovery_theme="AI芯片",
        selection_mode="ranked_shortlist",
        thesis_requirements=[
            "消费级终端场景（手机、PC、可穿戴、IoT）",
            "端侧AI SoC或端侧推理芯片",
            "已有量产、订单或收入兑现",
            "不输出泛AI芯片概念名单，只给符合命题的排序短名单",
        ],
        objective="筛选最符合的A股公司",
    )

    assert intent.thesis_requirements == [
        "消费级终端场景（手机、PC、可穿戴、IoT）",
        "端侧AI SoC或端侧推理芯片",
        "已有量产、订单或收入兑现",
    ]
