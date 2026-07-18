from __future__ import annotations

import json

from src.agent.analysis_playbooks import (
    INDUSTRY_CHAIN,
    INVESTMENT_DECISION,
    THEME_COMPANY_MAPPING,
    infer_research_topic,
    mandatory_tool_calls,
    select_analysis_playbook,
)


def _names(calls: list[dict]) -> list[str]:
    return [call["name"] for call in calls]


def test_three_turn_conversation_selects_deterministic_playbooks() -> None:
    first = [{"role": "user", "content": "帮我分析下人形机器人产业链，哪些领域最受益？"}]
    assert infer_research_topic(first) == "人形机器人"
    assert select_analysis_playbook(first, []) == INDUSTRY_CHAIN
    assert _names(mandatory_tool_calls(INDUSTRY_CHAIN, first, [])) == [
        "search_financial_news",
        "search_research_library",
        "search_research_library",
        "search_financial_news",
    ]

    second = [
        *first,
        {"role": "assistant", "content": "产业链分析"},
        {"role": "user", "content": "这些领域在A股有哪些公司？"},
    ]
    assert select_analysis_playbook(second, []) == THEME_COMPANY_MAPPING
    mapping_calls = mandatory_tool_calls(THEME_COMPANY_MAPPING, second, [])
    assert _names(mapping_calls) == [
        "get_theme_stock_candidates",
        "search_financial_news",
        "search_research_library",
        "websearch",
    ]
    assert json.loads(mapping_calls[0]["arguments"])["theme"] == "人形机器人"
    assert "人形机器人" in json.loads(mapping_calls[1]["arguments"])["query"]
    web_arguments = json.loads(mapping_calls[3]["arguments"])
    assert web_arguments["includeContent"] is True
    assert web_arguments["livecrawl"] == "preferred"
    assert "人形机器人" in web_arguments["query"]

    entities = [
        {"name": "兆威机电", "symbol": "003021"},
        {"name": "绿的谐波", "symbol": "688017"},
    ]
    third = [
        *second,
        {"role": "assistant", "content": "| 公司/代码 | 证据 |\n|---|---|\n| 兆威机电 (003021) | L2 |"},
        {"role": "user", "content": "上面提到的这些公司现在能买吗？"},
    ]
    assert select_analysis_playbook(third, entities) == INVESTMENT_DECISION
    decision_calls = mandatory_tool_calls(INVESTMENT_DECISION, third, entities)
    assert _names(decision_calls) == [
        "get_multi_stock_decision_evidence",
        "get_market_breadth",
    ]
    assert json.loads(decision_calls[0]["arguments"])["symbols"] == "003021,688017"


def test_mapping_contract_requires_complete_candidate_inventory() -> None:
    assert any("全部候选公司" in item for item in THEME_COMPANY_MAPPING.output_contract)
    assert any("禁止固定截成 8 家或 12 家" in item for item in THEME_COMPANY_MAPPING.output_contract)
    assert any("第一列" in item for item in THEME_COMPANY_MAPPING.output_contract)
    assert any("网页正文爬取必须全部执行" in item for item in THEME_COMPANY_MAPPING.evidence_standard)


def test_industry_chain_research_wins_over_incidental_stock_name_match() -> None:
    messages = [{
        "role": "user",
        "content": "请检索人形机器人产业链价值量、市场空间和竞争格局的研究资料，并注明来源。",
    }]
    # Entity extraction can validly find the A-share named ``机器人``
    # inside the topic, but that must not convert an industry question into a
    # single-stock deep-research workflow.
    verified_entities = [{"name": "机器人", "symbol": "300024"}]

    assert select_analysis_playbook(messages, verified_entities) == INDUSTRY_CHAIN
    assert _names(mandatory_tool_calls(INDUSTRY_CHAIN, messages, verified_entities)) == [
        "search_financial_news",
        "search_research_library",
        "search_research_library",
        "search_financial_news",
    ]


def test_research_topic_strips_repeated_mapping_directives() -> None:
    messages = [{
        "role": "user",
        "content": "请重新完整梳理人形机器人产业链A股公司，并给出数据源覆盖。",
    }]

    assert infer_research_topic(messages) == "人形机器人"
