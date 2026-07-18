from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from src.agent.evidence_facts import bind_company_evidence
from src.agent.research_intent import ResearchIntent


def _response(facts: list[dict]) -> SimpleNamespace:
    function = SimpleNamespace(
        name="bind_company_evidence",
        arguments=json.dumps({"facts": facts}, ensure_ascii=False),
    )
    message = SimpleNamespace(tool_calls=[SimpleNamespace(function=function)], content=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _intent() -> ResearchIntent:
    return ResearchIntent(
        kind="theme_company_mapping",
        topic="AI芯片",
        objective="找出AI芯片核心受益A股公司",
        research_dimensions=["GPU", "NPU", "量产", "订单", "收入"],
        output_requirements=["直接证据分级"],
        confidence=0.98,
    )


def test_semantic_evidence_binding_keeps_grounded_topic_fact_and_drops_unrelated_company() -> None:
    captured = {}

    async def completion(**kwargs):
        captured.update(kwargs)
        return _response([
            {
                "company_name": "全志科技",
                "symbol": "300458",
                "stage": "L3",
                "theme_relevance": "direct",
                "thesis_fit": "exact",
                "commercialization_signal": "batch_delivery",
                "relationship": "算力芯片",
                "fact": "AI芯片已经量产",
                "support_quote": "全志科技A733 AI芯片已实现量产",
                "source_id": "s1",
                "confidence": 0.97,
            },
            {
                "company_name": "盟固利",
                "symbol": "301487",
                "stage": "L3",
                "theme_relevance": "unrelated",
                "thesis_fit": "outside",
                "commercialization_signal": "batch_delivery",
                "relationship": "人形机器人电池材料",
                "fact": "NCA材料批量供货",
                "support_quote": "盟固利NCA材料在人形机器人用电池领域实现批量供货",
                "source_id": "s1",
                "confidence": 0.99,
            },
        ])

    facts = asyncio.run(bind_company_evidence(
        [{
            "tool": "websearch",
            "result": {
                "success": True,
                "retrieved_at": "2026-07-18T12:00:00",
                "results": [{
                    "title": "多家公司业务进展",
                    "content_text": (
                        "全志科技A733 AI芯片已实现量产。"
                        "盟固利NCA材料在人形机器人用电池领域实现批量供货。"
                    ),
                    "url": "https://example.com/mixed",
                    "source": "测试财经",
                }],
            },
        }],
        _intent(),
        {"model": "test-model"},
        completion=completion,
    ))

    assert len(facts) == 1
    assert facts[0].company_name == "全志科技"
    assert facts[0].symbol == "300458"
    assert facts[0].stage == "L3"
    assert facts[0].fact == "全志科技A733 AI芯片已实现量产"
    assert captured["tool_choice"]["function"]["name"] == "bind_company_evidence"


def test_semantic_evidence_binding_rejects_quote_not_present_in_source() -> None:
    async def completion(**_kwargs):
        return _response([{
            "company_name": "寒武纪",
            "symbol": "688256",
            "stage": "L3",
            "theme_relevance": "direct",
            "thesis_fit": "exact",
            "commercialization_signal": "order",
            "relationship": "训练芯片",
            "fact": "取得大额订单",
            "support_quote": "寒武纪取得100亿元AI芯片订单",
            "source_id": "s1",
            "confidence": 0.99,
        }])

    facts = asyncio.run(bind_company_evidence(
        [{
            "tool": "search_financial_news",
            "result": {
                "success": True,
                "retrieved_at": "2026-07-18T12:00:00",
                "items": [{
                    "title": "寒武纪发布新产品",
                    "summary": "寒武纪展示新一代训练芯片，尚未披露订单。",
                    "link": "https://example.com/cambricon",
                    "source": "测试财经",
                }],
            },
        }],
        _intent(),
        {"model": "test-model"},
        completion=completion,
    ))

    assert facts == []


def test_commercial_application_without_delivery_is_not_l3() -> None:
    async def completion(**_kwargs):
        return _response([{
            "company_name": "云天励飞",
            "symbol": "688343",
            "stage": "L3",
            "theme_relevance": "supporting",
            "thesis_fit": "partial",
            "commercialization_signal": "commercial_application",
            "relationship": "边缘AI推理芯片",
            "fact": "已商业化应用",
            "support_quote": "云天励飞推理芯片已商业化应用于机器人、边缘网关和服务器",
            "source_id": "s1",
            "confidence": 0.95,
        }])

    facts = asyncio.run(bind_company_evidence(
        [{"tool": "websearch", "result": {
            "retrieved_at": "2026-07-18T12:00:00",
            "results": [{
                "title": "云天励飞推理芯片应用进展",
                "content_text": "云天励飞推理芯片已商业化应用于机器人、边缘网关和服务器",
                "url": "https://example.com/yuntianlifei",
                "published": "2026-07-18",
            }],
        }}],
        _intent(),
        {"model": "test-model"},
        completion=completion,
    ))

    assert len(facts) == 1
    assert facts[0].stage == "L1"
    assert facts[0].thesis_fit == "partial"


def test_strong_realization_excerpt_is_prioritized_and_wins_company_dedup() -> None:
    captured_request = {}

    async def completion(**kwargs):
        captured_request.update(json.loads(kwargs["messages"][1]["content"]))
        return _response([
            {
                "company_name": "安凯微",
                "symbol": "688620",
                "stage": "L2",
                "theme_relevance": "direct",
                "thesis_fit": "partial",
                "commercialization_signal": "customer_validation",
                "relationship": "机器视觉芯片",
                "fact": "收获客户订单",
                "support_quote": "安凯微AK2659机器视觉芯片收获客户订单",
                "source_id": "s1",
                "confidence": 0.9,
            },
            {
                "company_name": "安凯微",
                "symbol": "688620",
                "stage": "L3",
                "theme_relevance": "direct",
                "thesis_fit": "exact",
                "commercialization_signal": "batch_delivery",
                "relationship": "消费级AI眼镜SoC",
                "fact": "AI眼镜SoC批量交付",
                "support_quote": "安凯微AI眼镜SoC芯片已量产，2025年四季度实现批量交付",
                "source_id": "s1",
                "confidence": 0.98,
            },
        ])

    intent = ResearchIntent(
        kind="theme_company_mapping",
        topic="消费级终端端侧AI SoC与推理芯片",
        discovery_theme="AI芯片",
        selection_mode="ranked_shortlist",
        thesis_requirements=["消费级终端", "端侧AI SoC", "量产、订单或收入兑现"],
        objective="找出最符合命题的A股公司",
    )
    facts = asyncio.run(bind_company_evidence(
        [{"tool": "websearch", "result": {
            "retrieved_at": "2026-07-18T12:00:00",
            "results": [{
                "title": "安凯微端侧AI业务进展",
                "content_text": (
                    "安凯微介绍端侧AI产品布局。" + "普通业务介绍。" * 200
                    + "安凯微AK2659机器视觉芯片收获客户订单。"
                    + "安凯微AI眼镜SoC芯片已量产，2025年四季度实现批量交付。"
                ),
                "url": "https://example.com/ankermicro",
                "published": "2026-07-16",
            }],
        }}],
        intent,
        {"model": "test-model"},
        completion=completion,
    ))

    source_text = captured_request["sources"][0]["text"]
    assert source_text.index("AI眼镜SoC芯片已量产") < source_text.index("【来源正文节选】")
    assert len(facts) == 1
    assert facts[0].company_name == "安凯微"
    assert facts[0].stage == "L3"
    assert facts[0].thesis_fit == "exact"


def test_ranked_shortlist_recovers_explicit_single_company_batch_delivery() -> None:
    async def completion(**_kwargs):
        return _response([])

    intent = ResearchIntent(
        kind="theme_company_mapping",
        topic="消费级终端端侧AI SoC与推理芯片",
        discovery_theme="AI芯片",
        selection_mode="ranked_shortlist",
        thesis_requirements=["消费级终端", "端侧AI SoC", "量产、订单或收入兑现"],
        objective="找出最符合命题的A股公司",
    )
    facts = asyncio.run(bind_company_evidence(
        [{"tool": "websearch", "result": {
            "retrieved_at": "2026-07-18T12:00:00",
            "results": [{
                "title": "安凯微端侧AI业务进展",
                "content_text": "安凯微AI眼镜SoC芯片已量产，2025年四季度实现批量交付。",
                "url": "https://example.com/ankermicro",
                "published": "2026-07-16",
            }],
        }}],
        intent,
        {"model": "test-model"},
        completion=completion,
    ))

    assert len(facts) == 1
    assert facts[0].company_name == "安凯微"
    assert facts[0].stage == "L3"
    assert facts[0].thesis_fit == "exact"
    assert facts[0].commercialization_signal == "batch_delivery"
