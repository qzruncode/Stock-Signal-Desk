"""Focused checks for the bounded semantic Reflection contract."""

from __future__ import annotations

import asyncio

import pytest

from src.agent.langgraph_runtime.middleware import ReflectionMiddleware
from src.agent.langgraph_runtime.reflection import (
    ReflectionReview,
    build_reflection_packet,
    normalize_reflection_review,
    reflection_eligibility,
    reflection_feedback,
    unrequested_knowledge_table_review,
)


def _state(**overrides):
    state = {
        "status": "completed",
        "user_text": "分析 600519 的后续风险",
        "evidence": [
            {
                "evidence_id": "ev_secret",
                "success": True,
                "effect": "read",
                "tool_name": "read_financials",
                "data_time": "2026-09-04",
                "data_time_provenance": "source",
                "entities": {"symbol": "600519"},
                "source_refs": ["https://secret.example/source"],
                "result": {
                    "symbol": "600519",
                    "revenue_growth": -0.062,
                    "path": "/Users/xiejiawei/private/source.json",
                },
            }
        ],
        "tool_results": [],
        "claim_evidence": [
            {
                "text": "增长仍有压力。",
                "kind": "inference",
                "evidence_ids": ["ev_secret"],
                "checks": {"source": True, "time": True},
            }
        ],
        "pending_content_reads": [],
        "content_access_feedback": "",
        "structured_answer": {
            "profile": "research",
            "title": "研究判断",
            "blocks": [
                {
                    "kind": "inference",
                    "section": "综合判断",
                    "content": "增长仍有压力，详情见 https://secret.example/source。",
                    "evidence_ids": ["ev_secret"],
                }
            ],
        },
    }
    state.update(overrides)
    return state


def test_reflection_only_targets_research_judgments_after_hard_checks():
    eligible, details = reflection_eligibility(_state(), _state()["structured_answer"])
    assert eligible is True
    assert details["material_block_indices"] == [1]

    general = _state()
    general["structured_answer"] = {**general["structured_answer"], "profile": "general"}
    assert reflection_eligibility(general, general["structured_answer"])[1]["reason"] == "general_profile"

    pdf_answer = _state(
        knowledge_base_ids=["kb-selected"],
        evidence=[
            {
                **_state()["evidence"][0],
                "tool_name": "search_knowledge_base",
                "result": {
                    "results": [
                        {"page_start": 14, "page_end": 14, "text": "Level 0 uses no memory."}
                    ]
                },
            }
        ],
        structured_answer={
            "profile": "general",
            "blocks": [
                {
                    "kind": "answer",
                    "content": "Level 0 has no memory; Level 1 memory is provided by tools.",
                    "evidence_ids": ["ev_secret"],
                }
            ],
        },
    )
    pdf_eligible, pdf_details = reflection_eligibility(
        pdf_answer,
        pdf_answer["structured_answer"],
    )
    assert pdf_eligible is True
    assert pdf_details["reason"] == "knowledge_base_answer_blocks_present"
    assert pdf_details["material_block_indices"] == [1]

    facts_only = _state()
    facts_only["structured_answer"] = {
        **facts_only["structured_answer"],
        "blocks": [{"kind": "fact", "content": "营收为负增长。", "evidence_ids": ["ev_secret"]}],
    }
    assert reflection_eligibility(facts_only, facts_only["structured_answer"])[1]["reason"] == "no_judgment_blocks"


def test_reflection_packet_redacts_urls_paths_and_real_evidence_ids():
    packet = build_reflection_packet(state=_state(), answer=_state()["structured_answer"])
    rendered = str(packet)
    assert "secret.example" not in rendered
    assert "/Users/xiejiawei" not in rendered
    assert "ev_secret" not in rendered
    assert packet["candidate"]["blocks"][0]["evidence_aliases"] == ["e1"]
    assert packet["evidence"][0]["alias"] == "e1"


def test_reflection_packet_keeps_a_bounded_pdf_body_for_semantic_review():
    content = "研报正文段落。" * 2_000
    state = _state()
    state["evidence"][0]["tool_name"] = "read_web_source"
    state["evidence"][0]["result"] = {
        "success": True,
        "content_type": "application/pdf",
        "document_extension": ".pdf",
        "content": content,
    }

    packet = build_reflection_packet(state=state, answer=state["structured_answer"])
    observation = packet["evidence"][0]["observation"]

    assert observation["content"] == content
    assert observation["content_preview_truncated"] is False
    assert observation["content_length"] == len(content)


def test_reflection_review_is_strict_and_feedback_is_bounded():
    review = normalize_reflection_review(
        ReflectionReview(
            verdict="revise",
            summary="结论需要收窄。",
            issues=[
                {
                    "block_index": 1,
                    "category": "reasoning",
                    "severity": "high",
                    "reason": "证据不能支持确定性预测。",
                    "repair_instruction": "改为条件性表述。",
                }
            ],
        ),
        block_count=1,
    )
    feedback = reflection_feedback(review)
    assert "不要调用工具" in feedback
    assert "第 1 个区块" in feedback
    assert "条件性表述" in feedback

    with pytest.raises(ValueError, match="revise"):
        normalize_reflection_review({"verdict": "revise", "issues": []}, block_count=1)


def test_reflection_checks_comparisons_without_inventing_unstated_capabilities():
    from src.agent.langgraph_runtime.reflection import reflection_messages

    messages = reflection_messages(build_reflection_packet(state=_state(), answer=_state()["structured_answer"]))
    system_prompt = str(messages[0].content)

    assert "不得从一方未提及某项能力反推出另一方具备该能力" in system_prompt
    assert "逐行逐格核对" in system_prompt
    assert "来源未说明" in system_prompt


def test_pdf_comparison_table_requires_an_explicit_user_format_request():
    state = _state(
        user_text="只根据所选 PDF 比较 Level 0 与 Level 1 的差异",
        knowledge_base_ids=["kb-selected"],
        evidence=[
            {
                **_state()["evidence"][0],
                "tool_name": "search_knowledge_base",
                "result": {
                    "results": [
                        {"page_start": 14, "page_end": 14, "text": "Level 0 uses no memory."}
                    ]
                },
            }
        ],
    )
    answer = {
        "profile": "general",
        "blocks": [{
            "kind": "answer",
            "content": (
                "核心差异\n\n"
                "| 维度 | Level 0 | Level 1 |\n"
                "|---|---|---|\n"
                "| 记忆 | 无记忆 | 外部来源获取信息 |"
            ),
            "evidence_ids": ["ev_secret"],
        }],
    }

    review = unrequested_knowledge_table_review(state, answer)

    assert review is not None
    assert review["verdict"] == "revise"
    assert review["issues"][0]["block_index"] == 1
    assert "删除表格" in review["issues"][0]["repair_instruction"]

    requested_state = {**state, "user_text": "请用表格比较 Level 0 与 Level 1"}
    assert unrequested_knowledge_table_review(requested_state, answer) is None
    assert unrequested_knowledge_table_review(state, {"blocks": [{"content": "逐点说明"}]}) is None


def test_reflection_middleware_routes_an_unrequested_pdf_table_through_bounded_revision():
    class EventCapture:
        def __init__(self):
            self.calls = []

        def stage(self, *args, **kwargs):
            self.calls.append((args, kwargs))

    state = _state(
        user_text="只比较 Level 0 和 Level 1 的差异",
        knowledge_base_ids=["kb-selected"],
        evidence=[
            {
                **_state()["evidence"][0],
                "tool_name": "search_knowledge_base",
                "result": {
                    "results": [
                        {"page_start": 14, "page_end": 14, "text": "Level 0 uses no memory."}
                    ]
                },
            }
        ],
        structured_answer={
            "profile": "general",
            "blocks": [{
                "kind": "answer",
                "content": (
                    "| 维度 | Level 0 | Level 1 |\n"
                    "|---|---|---|\n"
                    "| 记忆 | 无记忆 | 外部来源获取信息 |"
                ),
                "evidence_ids": ["ev_secret"],
            }],
        },
    )
    events = EventCapture()
    runtime = type("Runtime", (), {"context": type("Context", (), {"events": events})()})()

    update = asyncio.run(ReflectionMiddleware().aafter_model(state, runtime))

    assert update["jump_to"] == "model"
    assert update["reflection_revision_count"] == 1
    assert update["reflection_call_count"] == 0
    assert "删除表格" in update["reflection_feedback"]
    assert events.calls[0][0][:2] == ("reflection", "completed")
