"""Focused checks for the bounded semantic Reflection contract."""

from __future__ import annotations

import pytest

from src.agent.langgraph_runtime.reflection import (
    ReflectionReview,
    build_reflection_packet,
    normalize_reflection_review,
    reflection_eligibility,
    reflection_feedback,
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
