"""Focused checks for the bounded semantic Reflection contract."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.agent.langgraph_runtime.middleware import ReflectionMiddleware
from src.agent.langgraph_runtime.reflection import (
    ReflectionReview,
    apply_exact_low_severity_repairs,
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


def test_exact_low_severity_table_label_repair_preserves_values_and_citations():
    candidate = {
        "profile": "research",
        "blocks": [
            {
                "kind": "fact",
                "content": "营业收入 2,073,339,945.39 元，同比 -6.17%。",
                "evidence_ids": ["ev_income"],
            },
            {
                "kind": "answer",
                "content": (
                    "营业收入数据见“主要会计数据和财务指标”表（第7页）及“主要财务数据同比变动情况”表（第19页）；"
                    "归母净利润数据见第7页“主要会计数据和财务指标”表。"
                ),
                "evidence_ids": ["ev_income", "ev_report"],
            },
        ],
    }
    review = {
        "verdict": "revise",
        "issues": [
            {
                "block_index": 2,
                "category": "evidence_scope",
                "severity": "low",
                "reason": "区块2把第7页表格称为“主要会计数据和财务指标”表，但证据未显示该表名。",
                "repair_instruction": "将该处改为仅按页码指称（如“第7页表格”），或标注标题来源未说明。",
            }
        ],
    }

    repaired = apply_exact_low_severity_repairs(candidate, review)

    assert repaired is not None
    assert repaired["blocks"][0] == candidate["blocks"][0]
    assert repaired["blocks"][1]["content"] == (
        "营业收入数据见第7页表格及“主要财务数据同比变动情况”表（第19页）；"
        "归母净利润数据见第7页表格。"
    )
    assert repaired["blocks"][1]["evidence_ids"] == ["ev_income", "ev_report"]


def test_exact_low_severity_literal_replacement_preserves_other_answer_content():
    table = "| 指标 | 本报告期 | 同比 |\n|---|---:|---:|\n| 经营现金流 | 363,763,010.34 元 | +275.77% |"
    note = (
        "口径说明：第55/56页现金流量表中的母公司经营活动现金流量净额为 "
        "271,351,507.32 元，属母公司口径、未采用。"
    )
    candidate = {
        "profile": "research",
        "blocks": [
            {"kind": "fact", "content": table, "evidence_ids": ["ev_cashflow"]},
            {"kind": "answer", "content": note, "evidence_ids": ["ev_cashflow"]},
        ],
    }
    review = {
        "verdict": "revise",
        "issues": [
            {
                "block_index": 2,
                "category": "evidence_scope",
                "severity": "low",
                "reason": "第55页为合并表，第56页为母公司表。",
                "repair_instruction": (
                    "将口径说明中的“第55/56页现金流量表”改为“第56页母公司现金流量表”，"
                    "保留 271,351,507.32 元及母公司口径说明。"
                ),
            }
        ],
    }

    repaired = apply_exact_low_severity_repairs(candidate, review)

    assert repaired is not None
    assert repaired["blocks"][0] == candidate["blocks"][0]
    assert repaired["blocks"][1]["content"] == note.replace(
        "第55/56页现金流量表", "第56页母公司现金流量表"
    )
    assert "271,351,507.32 元" in repaired["blocks"][1]["content"]
    assert repaired["blocks"][1]["evidence_ids"] == ["ev_cashflow"]


def test_exact_low_severity_literal_replacement_rejects_ambiguous_old_phrase():
    candidate = {
        "profile": "research",
        "blocks": [
            {
                "kind": "answer",
                "content": "第55/56页现金流量表；另见第55/56页现金流量表。",
                "evidence_ids": ["ev_cashflow"],
            }
        ],
    }
    review = {
        "verdict": "revise",
        "issues": [
            {
                "block_index": 1,
                "category": "evidence_scope",
                "severity": "low",
                "reason": "页码不精确。",
                "repair_instruction": "将“第55/56页现金流量表”改为“第56页母公司现金流量表”。",
            }
        ],
    }

    assert apply_exact_low_severity_repairs(candidate, review) is None


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
    assert pdf_eligible is False
    assert pdf_details["reason"] == "no_judgment_blocks"
    assert pdf_details["material_block_indices"] == []

    pdf_judgment = {
        **pdf_answer["structured_answer"],
        "profile": "research",
        "blocks": [{
            "kind": "inference",
            "content": "该差异可能影响后续判断。",
            "evidence_ids": ["ev_secret"],
        }],
    }
    judgment_eligible, judgment_details = reflection_eligibility(pdf_answer, pdf_judgment)
    assert judgment_eligible is True
    assert judgment_details["material_block_indices"] == [1]

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


def test_kb_review_uses_exact_hit_identity_and_the_complete_table_text():
    content = "| 指标 | 本期 | 同期 |\n" * 200 + "| 净资产 | 7,291,847,177.43 | 6,999,574,574.88 |"
    state = _state()
    state["evidence"][0].update({
        "tool_name": "search_knowledge_base",
        "result": {"success": True, "results": [{
            "evidence_id": "ev_kb_page_7", "filename": "2026年半年度报告.pdf", "page_start": 7, "page_end": 7,
            "snippet": content, "text": content, "url": "/documents/report/content#page=7",
        }]},
    })
    state["structured_answer"]["blocks"] = [{"kind": "fact", "content": content, "evidence_ids": ["ev_kb_page_7"]}]
    packet = build_reflection_packet(state=state, answer=state["structured_answer"])
    assert packet["candidate"]["blocks"][0]["content"] == content
    assert packet["candidate"]["blocks"][0]["evidence_aliases"] == ["e1"]
    assert packet["evidence"][0]["observation"]["text"] == content
    assert packet["evidence"][0]["observation"]["page_start"] == 7
    assert "ev_kb_page_7" not in str(packet)


def test_review_does_not_omit_late_answer_blocks_or_their_cited_hits():
    state = _state()
    state["evidence"][0].update({"tool_name": "search_knowledge_base", "result": {"results": [
        {"evidence_id": f"ev_kb_p{index}", "page_start": index, "page_end": index,
         "snippet": f"第{index}项独立证据", "url": f"/documents/report/content#page={index}"}
        for index in range(1, 82)
    ]}})
    state["structured_answer"]["blocks"] = [
        {"kind": "fact", "content": f"第{index}项独立结论", "evidence_ids": [f"ev_kb_p{index}"]}
        for index in range(1, 82)
    ]
    packet = build_reflection_packet(state=state, answer=state["structured_answer"])
    assert len(packet["candidate"]["blocks"]) == 81
    assert len(packet["evidence"]) == 81
    assert packet["candidate"]["blocks"][-1]["evidence_aliases"] == [packet["evidence"][-1]["alias"]]
    review = normalize_reflection_review({
        "verdict": "revise", "issues": [{"block_index": 81, "reason": "核对最后一项结论。", "repair_instruction": "收窄最后一项结论。"}],
    }, block_count=81)
    assert review["issues"][0]["block_index"] == 81


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
    assert "不得删除已核验的事实、数字、表格数据行、页码或来源引用" in feedback
    assert "未被问题指出的 blocks 必须逐字保留" in feedback
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


@pytest.mark.parametrize("issue", [
    {"reason": "区块8称", "repair_instruction": "在区块8中补充"},
    {"block_index": 1, "reason": "缺少证据对应。", "repair_instruction": ""},
    {"block_index": 2, "reason": "引用错误。", "repair_instruction": "更正引用。"},
])
def test_reflection_rejects_unlocated_or_unactionable_revisions(issue):
    with pytest.raises(ValueError, match="revise"):
        normalize_reflection_review({"verdict": "revise", "issues": [issue]}, block_count=1)


def test_reflection_preserves_complete_review_and_revision_instructions():
    from src.agent.langgraph_runtime.reflection import reflection_review_projection

    reason = "完整核对该区块的来源与口径。" * 80 + "原因结束。"
    instruction = "将该区块的表述明确区分为两个来源口径。" * 80 + "修订要求结束。"
    summary = "完整复核意见。" * 130 + "意见结束。"
    review = normalize_reflection_review({
        "verdict": "revise", "summary": summary,
        "issues": [{"block_index": 1, "reason": reason, "repair_instruction": instruction}],
    }, block_count=1)
    projected = reflection_review_projection(review)
    assert projected["summary"] == summary
    assert projected["issues"][0]["reason"] == reason
    assert projected["issues"][0]["repair_instruction"] == instruction
    feedback = reflection_feedback(review)
    assert reason in feedback
    assert instruction in feedback


def test_selected_knowledge_base_without_pdf_evidence_does_not_change_reflection():
    state = _state(knowledge_base_ids=["kb-selected"])
    answer = {"profile": "general", "blocks": [{"kind": "answer", "content": "普通说明"}]}
    assert reflection_eligibility(state, answer)[1]["reason"] == "general_profile"


def test_fact_only_pdf_table_uses_evidence_gate_without_semantic_model_call():
    class EventCapture:
        def __init__(self):
            self.calls = []

        def stage(self, *args, **kwargs):
            self.calls.append((args, kwargs))

        def commit_model_answer(self, answer, **kwargs):
            self.answer = answer

    class Reviewer:
        def __init__(self):
            self.calls = []

        def with_structured_output(self, schema):
            raise AssertionError("Fact-only PDF output must not invoke a semantic reviewer")

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
    reviewer = Reviewer()
    runtime = SimpleNamespace(context=SimpleNamespace(events=events, model=reviewer))

    update = asyncio.run(ReflectionMiddleware().aafter_model(state, runtime))

    assert update["jump_to"] == "end"
    assert "reflection_revision_count" not in update
    assert "reflection_call_count" not in update
    assert update["reflection_status"] == "skipped"
    assert update["reflection_review"]["summary"].endswith("no_judgment_blocks")
    assert reviewer.calls == []
    assert "| 维度 | Level 0 | Level 1 |" in events.answer


def test_reflection_call_failure_is_not_presented_as_a_failed_semantic_verdict():
    class EventCapture:
        def __init__(self):
            self.calls = []

        def stage(self, *args, **kwargs):
            self.calls.append((args, kwargs))

        def commit_model_answer(self, answer, **kwargs):
            self.answer = answer

    class FailedReview:
        async def ainvoke(self, _messages):
            raise TimeoutError("test timeout")

    class Reviewer:
        stream = None

        def with_structured_output(self, schema, **kwargs):
            assert schema is ReflectionReview
            self.stream = kwargs.get("stream")
            return FailedReview()

    events = EventCapture()
    reviewer = Reviewer()
    runtime = SimpleNamespace(context=SimpleNamespace(events=events, model=reviewer))

    update = asyncio.run(ReflectionMiddleware().aafter_model(_state(), runtime))

    assert reviewer.stream is False
    assert update["status"] == "partial"
    assert update["error_code"] == "reflection_failed"
    failed_stage = events.calls[-1][1]
    assert events.calls[-1][0][2] == "语义复核调用失败，已发布带明确限制的结果"
    assert failed_stage["user_message"] == (
        "语义复核调用失败，未能取得判定结果；本轮回答会明确标出核验未完成。"
    )
