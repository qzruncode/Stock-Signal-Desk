from __future__ import annotations

import json

from src.agent.analysis_playbooks import (
    COLLECTION_FINANCIAL_FILTER,
    INDUSTRY_CHAIN,
    INVESTMENT_DECISION,
    THEME_COMPANY_MAPPING,
    mandatory_tool_calls,
    select_playbook_for_intent,
)
from src.agent.research_intent import ResearchIntent


def _names(calls: list[dict]) -> list[str]:
    return [call["name"] for call in calls]


def test_playbook_selection_uses_only_the_intent_enum() -> None:
    industry = ResearchIntent(
        kind="industry_chain",
        topic="人形机器人",
        objective="研究价值传导",
        research_dimensions=["价值量", "竞争格局", "产能"],
    )
    mapping = ResearchIntent(
        kind="theme_company_mapping",
        topic="人形机器人关节执行器",
        discovery_theme="人形机器人",
        selection_mode="complete_inventory",
        company_mapping_mode="structured_candidates",
        resolved_domains=["行星滚柱丝杠", "减速器", "无框力矩电机"],
        objective="按领域映射A股公司",
    )
    decision = ResearchIntent(
        kind="investment_decision",
        topic="两家公司",
        entity_scope="previous_answer",
        objective="判断介入条件",
    )

    assert select_playbook_for_intent(industry) == INDUSTRY_CHAIN
    assert select_playbook_for_intent(mapping) == THEME_COMPANY_MAPPING
    assert select_playbook_for_intent(decision) == INVESTMENT_DECISION


def test_industry_calls_use_semantic_dimensions_without_topic_dictionary() -> None:
    intent = ResearchIntent(
        kind="industry_chain",
        topic="任意新兴产业",
        objective="研究产业链",
        research_dimensions=["独特部件甲", "独特部件乙"],
    )

    calls = mandatory_tool_calls(INDUSTRY_CHAIN, [], intent)

    assert _names(calls) == [
        "search_financial_news",
        "search_research_library",
        "search_research_library",
        "search_financial_news",
    ]
    assert "独特部件甲" in json.loads(calls[2]["arguments"])["query"]


def test_structured_company_mapping_uses_exact_resolved_domains_and_one_local_tool() -> None:
    intent = ResearchIntent(
        kind="theme_company_mapping",
        topic="人形机器人关节执行器（电机+减速器+丝杠）",
        discovery_theme="人形机器人",
        selection_mode="complete_inventory",
        company_mapping_mode="structured_candidates",
        resolved_domains=["行星滚柱丝杠", "减速器", "无框力矩电机"],
        objective="找出按这些方向发展的A股公司",
    )

    calls = mandatory_tool_calls(THEME_COMPANY_MAPPING, [], intent)

    assert _names(calls) == ["get_domain_stock_candidates"]
    assert json.loads(calls[0]["arguments"]) == {
        "domains": ["行星滚柱丝杠", "减速器", "无框力矩电机"],
        "context_theme": "人形机器人",
        "limit_per_domain": 300,
    }


def test_business_evidence_mapping_is_an_explicit_typed_mode() -> None:
    intent = ResearchIntent(
        kind="theme_company_mapping",
        topic="AI芯片",
        discovery_theme="AI芯片",
        selection_mode="ranked_shortlist",
        company_mapping_mode="business_evidence",
        resolved_domains=["训练芯片", "推理芯片"],
        thesis_requirements=["已有公司级订单或收入证据"],
        objective="只保留已兑现公司",
        research_dimensions=["订单", "收入"],
    )

    calls = mandatory_tool_calls(THEME_COMPANY_MAPPING, [], intent)

    assert _names(calls) == [
        "get_theme_stock_candidates",
        "search_financial_news",
        "search_research_library",
        "websearch",
    ]


def test_collection_financial_filter_preserves_all_entities_in_twelve_stock_batches() -> None:
    entities = [
        {"name": f"公司{index}", "symbol": f"{index:06d}"}
        for index in range(1, 48)
    ]
    intent = ResearchIntent(
        kind="collection_financial_filter",
        objective="筛选上文集合",
        entity_scope="previous_answer",
        collection_financial_filter_spec={
            "metric": "debt_ratio",
            "operator": "gt",
            "threshold": 70,
            "action": "exclude_matching",
        },
    )

    calls = mandatory_tool_calls(COLLECTION_FINANCIAL_FILTER, entities, intent)

    assert _names(calls) == ["get_multi_stock_financials"] * 4
    batches = [json.loads(call["arguments"])["symbols"].split(",") for call in calls]
    assert [len(batch) for batch in batches] == [12, 12, 12, 11]
    assert [code for batch in batches for code in batch] == [item["symbol"] for item in entities]


def test_collection_financial_filter_can_plan_more_than_four_batches() -> None:
    entities = [
        {"name": f"公司{index}", "symbol": f"{index:06d}"}
        for index in range(1, 80)
    ]
    intent = ResearchIntent(
        kind="collection_financial_filter",
        objective="筛选上文集合",
        entity_scope="previous_answer",
        collection_financial_filter_spec={
            "metric": "debt_ratio",
            "operator": "gt",
            "threshold": 70,
            "action": "exclude_matching",
        },
    )

    calls = mandatory_tool_calls(COLLECTION_FINANCIAL_FILTER, entities, intent)

    assert len(calls) == 7
    assert sum(
        len(json.loads(call["arguments"])["symbols"].split(","))
        for call in calls
    ) == 79


def test_mapping_contract_requires_complete_structured_candidate_inventory() -> None:
    assert any("全部公司/代码" in item for item in THEME_COMPANY_MAPPING.output_contract)
    assert any("不得固定截成 8 家或 12 家" in item for item in THEME_COMPANY_MAPPING.output_contract)
    assert any("覆盖状态" in item for item in THEME_COMPANY_MAPPING.output_contract)
    assert any("不得用 search_stocks" in item for item in THEME_COMPANY_MAPPING.evidence_standard)
