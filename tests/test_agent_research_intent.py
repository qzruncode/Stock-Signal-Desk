from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from src.agent.analysis_playbooks import (
    THEME_COMPANY_MAPPING,
    mandatory_tool_calls,
    select_playbook_for_intent,
)
from src.agent.research_intent import ResearchIntent, resolve_research_intent


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


def test_semantic_intent_uses_latest_explicit_subtopic_and_structured_dimensions() -> None:
    captured = {}

    async def completion(**kwargs):
        captured.update(kwargs)
        return _tool_response({
            "kind": "theme_company_mapping",
            "topic": "AI芯片",
            "discovery_theme": "AI芯片",
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


def test_semantic_intent_normalizes_gateway_null_and_topic_only_scope() -> None:
    async def completion(**_kwargs):
        return _tool_response({
            "kind": "theme_company_mapping",
            "topic": "AI计算芯片（训练与推理）",
            "discovery_theme": "AI芯片",
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
