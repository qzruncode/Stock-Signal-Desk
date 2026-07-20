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
    SemanticIntentUnavailableError,
    _conversation_for_resolution,
    _conversation_for_recovery,
    _semantic_cache_key,
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


def _payload(**updates) -> dict:
    payload = {
        "kind": "general_question",
        "topic": None,
        "discovery_theme": None,
        "selection_mode": "none",
        "company_mapping_mode": "none",
        "resolved_domains": [],
        "thesis_requirements": [],
        "entity_scope": "none",
        "entities": [],
        "objective": "处理当前问题",
        "research_dimensions": [],
        "output_requirements": [],
        "quantitative_screen_spec": None,
        "collection_financial_filter_spec": None,
        "unsupported_requirements": [],
        "needs_clarification": False,
        "clarification_question": None,
        "confidence": 0.9,
    }
    payload.update(updates)
    return payload


def _quantitative_spec() -> dict:
    return {
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
        "output_fields": ["qualified_ratio_pct", "revenue_ttm", "debt_ratio"],
        "preview_limit": 10,
    }


@pytest.mark.parametrize(
    "followup",
    [
        "按照上面排第一的最受益方向，找出A股正在往这些方向大力发展的公司",
        "按刚才第一梯队找相关A股公司",
        "把首位方向对应的上市公司列出来",
        "沿用最优先那组环节，映射A股公司",
        "刚才价值量最高的环节有哪些公司在布局",
    ],
)
def test_referential_paraphrases_share_one_semantic_domain_contract(followup: str) -> None:
    captured = {}

    async def completion(**kwargs):
        captured.update(kwargs)
        return _tool_response(_payload(
            kind="theme_company_mapping",
            topic="人形机器人关节执行器（电机+减速器+丝杠）",
            discovery_theme="人形机器人",
            selection_mode="complete_inventory",
            company_mapping_mode="structured_candidates",
            resolved_domains=["行星滚柱丝杠", "减速器", "无框力矩电机"],
            objective="按引用领域找A股公司",
            research_dimensions=["行星滚柱丝杠", "减速器", "无框力矩电机"],
            output_requirements=["按领域列出完整候选"],
            confidence=0.98,
        ))

    intent = asyncio.run(resolve_research_intent(
        [
            {"role": "user", "content": "分析人形机器人产业链最受益方向"},
            {
                "role": "assistant",
                "content": (
                    "| 顺序 | 方向 |\n|---|---|\n"
                    "| 1 | 关节执行器（无框力矩电机、减速器、行星滚柱丝杠） |"
                ),
            },
            {"role": "user", "content": followup},
        ],
        {"model": "test-model"},
        completion=completion,
    ))

    assert intent.resolved_domains == ["行星滚柱丝杠", "减速器", "无框力矩电机"]
    assert intent.company_mapping_mode == "structured_candidates"
    calls = mandatory_tool_calls(THEME_COMPANY_MAPPING, [], intent)
    assert [call["name"] for call in calls] == ["get_domain_stock_candidates"]
    context = json.loads(captured["messages"][1]["content"])
    assert context["conversation"][-1]["content"] == followup


def test_business_evidence_is_selected_only_by_typed_semantic_result() -> None:
    async def completion(**_kwargs):
        return _tool_response(_payload(
            kind="theme_company_mapping",
            topic="AI芯片",
            discovery_theme="AI芯片",
            selection_mode="ranked_shortlist",
            company_mapping_mode="business_evidence",
            resolved_domains=["训练芯片", "推理芯片"],
            thesis_requirements=["公司级收入已兑现"],
            objective="只保留有收入证据的公司",
            research_dimensions=["收入"],
        ))

    intent = asyncio.run(resolve_research_intent(
        [{"role": "user", "content": "只保留有公司级收入证据的AI芯片公司"}],
        {"model": "test-model"},
        completion=completion,
    ))

    calls = mandatory_tool_calls(THEME_COMPANY_MAPPING, [], intent)
    assert [call["name"] for call in calls] == [
        "get_theme_stock_candidates",
        "search_financial_news",
        "search_research_library",
        "websearch",
    ]


def test_collection_filter_is_fully_executable_without_text_parsing() -> None:
    async def completion(**_kwargs):
        return _tool_response(_payload(
            kind="collection_financial_filter",
            topic="上文公司集合",
            selection_mode="complete_inventory",
            entity_scope="previous_answer",
            objective="筛选上文集合",
            collection_financial_filter_spec={
                "metric": "debt_ratio",
                "operator": "gt",
                "threshold": 70,
                "action": "exclude_matching",
            },
        ))

    intent = asyncio.run(resolve_research_intent(
        [{"role": "user", "content": "把上面这些股票中负债率高于70%的筛掉"}],
        {"model": "test-model"},
        completion=completion,
    ))

    spec = intent.collection_financial_filter_spec
    assert spec is not None
    assert (spec.operator, spec.threshold, spec.action) == ("gt", 70, "exclude_matching")


def test_invalid_semantic_contract_retries_once_then_succeeds() -> None:
    calls = 0

    async def completion(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _tool_response(_payload(
                kind="theme_company_mapping",
                topic="人形机器人",
                discovery_theme="人形机器人",
                company_mapping_mode="structured_candidates",
                resolved_domains=[],
            ))
        assert "previous_validation_error" in json.loads(kwargs["messages"][1]["content"])
        return _tool_response(_payload(
            kind="theme_company_mapping",
            topic="人形机器人",
            discovery_theme="人形机器人",
            selection_mode="complete_inventory",
            company_mapping_mode="structured_candidates",
            resolved_domains=["减速器"],
        ))

    intent = asyncio.run(resolve_research_intent(
        [{"role": "user", "content": "找这个方向的公司"}],
        {"model": "test-model"},
        completion=completion,
    ))

    assert calls == 2
    assert intent.resolved_domains == ["减速器"]


def test_invalid_semantic_contract_fails_after_bounded_retry() -> None:
    calls = 0

    async def completion(**_kwargs):
        nonlocal calls
        calls += 1
        return _tool_response(_payload(
            kind="theme_company_mapping",
            topic="人形机器人",
            discovery_theme="人形机器人",
            company_mapping_mode="none",
            resolved_domains=[],
        ))

    with pytest.raises(ValueError, match="after retry"):
        asyncio.run(resolve_research_intent(
            [{"role": "user", "content": "找这个方向的公司"}],
            {"model": "test-model"},
            completion=completion,
        ))
    assert calls == 2


def test_semantic_intent_rejects_missing_clarification_question() -> None:
    async def completion(**_kwargs):
        return _tool_response(_payload(
            kind="investment_decision",
            objective="判断能否买入",
            needs_clarification=True,
            clarification_question=None,
            confidence=0.3,
        ))

    with pytest.raises(ValueError, match="after retry"):
        asyncio.run(resolve_research_intent(
            [{"role": "user", "content": "现在能买吗"}],
            {"model": "test-model"},
            completion=completion,
        ))


def test_typed_contract_rejects_incomplete_mapping_and_collection_filter() -> None:
    with pytest.raises(ValueError, match="resolved_domains"):
        ResearchIntent(
            kind="theme_company_mapping",
            topic="新主题",
            discovery_theme="新主题",
            company_mapping_mode="structured_candidates",
            objective="找公司",
        )
    with pytest.raises(ValueError, match="collection_financial_filter_spec"):
        ResearchIntent(
            kind="collection_financial_filter",
            entity_scope="previous_answer",
            objective="筛选集合",
        )


def test_quantitative_screening_routes_to_one_deterministic_tool() -> None:
    intent = ResearchIntent(
        kind="quantitative_screening",
        topic="ATR相对波动率全市场筛选",
        objective="按精确公式筛选全部A股",
        quantitative_screen_spec=_quantitative_spec(),
    )
    playbook = select_playbook_for_intent(intent)
    assert playbook == QUANTITATIVE_SCREENING
    calls = mandatory_tool_calls(playbook, [], intent)
    assert [call["name"] for call in calls] == ["screen_atr_volatility_stocks"]
    arguments = json.loads(calls[0]["arguments"])
    assert arguments["screen_spec"]["technical_rule"]["atr_period"] == 14


def test_quantitative_intent_rejects_silent_defaults() -> None:
    with pytest.raises(ValueError, match="完整 quantitative_screen_spec"):
        ResearchIntent(
            kind="quantitative_screening",
            topic="ATR筛选",
            objective="按ATR筛选",
        )


def test_semantic_quantitative_spec_preserves_changed_conditions() -> None:
    changed_spec = copy.deepcopy(_quantitative_spec())
    changed_spec["technical_rule"]["atr_period"] = 20
    changed_spec["financial_filters"][0]["value"] = 1_000_000_000

    async def completion(**_kwargs):
        return _tool_response(_payload(
            kind="quantitative_screening",
            topic="可配置ATR筛选",
            selection_mode="complete_inventory",
            objective="使用修改后的条件筛选全部A股",
            quantitative_screen_spec=changed_spec,
        ))

    intent = asyncio.run(resolve_research_intent(
        [{"role": "user", "content": "ATR改成20日，营收改成10亿元"}],
        {"model": "test-model"},
        completion=completion,
    ))

    assert intent.quantitative_screen_spec.technical_rule.atr_period == 20
    assert intent.quantitative_screen_spec.financial_filters[0].value == 1_000_000_000


def test_intent_context_keeps_adjacent_conclusion_boundary() -> None:
    long_answer = "首位：端侧AI SoC与推理芯片\n" + ("正文数据" * 2000) + "\n收入兑现条件"
    compact = _conversation_for_resolution([
        {"role": "user", "content": "分析消费终端逻辑"},
        {"role": "assistant", "content": long_answer},
        {"role": "user", "content": "沿用首位方向找公司"},
    ])

    assert len(compact[1]["content"]) < 3500
    assert "首位：端侧AI SoC与推理芯片" in compact[1]["content"]
    assert "收入兑现条件" in compact[1]["content"]


def test_timeout_uses_smaller_semantic_context_and_recovers_without_keyword_routing() -> None:
    calls: list[dict] = []
    long_answer = (
        "第一梯队：上游核心零部件（减速器、丝杠、电机）\n"
        + ("产业链正文" * 1200)
        + "\n持续跟踪量产进度"
    )

    async def completion(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise TimeoutError("gateway did not return headers")
        return _tool_response(_payload(
            kind="theme_company_mapping",
            topic="人形机器人上游核心零部件",
            discovery_theme="人形机器人",
            selection_mode="complete_inventory",
            company_mapping_mode="structured_candidates",
            resolved_domains=["减速器", "行星滚柱丝杠", "无框力矩电机"],
            objective="按上文最受益方向找A股公司",
        ))

    messages = [
        {"role": "user", "content": "分析人形机器人最受益方向"},
        {"role": "assistant", "content": long_answer},
        {"role": "user", "content": "按上面最受益的上游核心零部件找A股公司"},
    ]
    intent = asyncio.run(resolve_research_intent(
        messages,
        {"model": "test-model"},
        completion=completion,
    ))

    assert intent.kind == "theme_company_mapping"
    assert intent.resolved_domains == ["减速器", "行星滚柱丝杠", "无框力矩电机"]
    assert len(calls) == 2
    primary_context = json.loads(calls[0]["messages"][1]["content"])
    recovery_context = json.loads(calls[1]["messages"][1]["content"])
    assert len(recovery_context["conversation"][1]["content"]) < len(
        primary_context["conversation"][1]["content"]
    )
    assert "第一梯队：上游核心零部件" in recovery_context["conversation"][1]["content"]


def test_two_gateway_timeouts_report_availability_failure() -> None:
    async def completion(**_kwargs):
        raise TimeoutError("gateway unavailable")

    with pytest.raises(SemanticIntentUnavailableError, match="timed out after recovery"):
        asyncio.run(resolve_research_intent(
            [{"role": "user", "content": "解释任意一个问题"}],
            {"model": "test-model"},
            completion=completion,
        ))


def test_recovery_context_keeps_only_recent_semantic_boundary() -> None:
    messages = [
        {"role": "user", "content": "更早问题"},
        {"role": "assistant", "content": "更早回答"},
        {"role": "user", "content": "分析产业链"},
        {"role": "assistant", "content": "第一梯队：减速器、丝杠、电机\n" + ("正文" * 1000)},
        {"role": "user", "content": "沿用上面第一梯队找公司"},
    ]

    compact = _conversation_for_recovery(messages)

    assert len(compact) == 4
    assert compact[-1]["content"] == "沿用上面第一梯队找公司"
    assert "第一梯队：减速器、丝杠、电机" in compact[-2]["content"]


def test_semantic_cache_key_is_stable_but_changes_for_an_edited_turn() -> None:
    messages = [
        {"role": "assistant", "content": "第一梯队：减速器、丝杠、电机"},
        {"role": "user", "content": "按上面方向找公司"},
    ]
    config = {"model": "test-model", "api_base": "https://gateway.example"}

    first = _semantic_cache_key(messages, config, [], [])
    repeated = _semantic_cache_key(list(messages), dict(config), [], [])
    edited = _semantic_cache_key(
        [messages[0], {"role": "user", "content": "按上面方向查订单"}],
        config,
        [],
        [],
    )

    assert first == repeated
    assert first != edited


def test_unrelated_general_question_never_selects_stock_playbook() -> None:
    intent = ResearchIntent(
        kind="general_question",
        objective="解释如何规划一次家庭旅行",
    )
    assert select_playbook_for_intent(intent) is None
