"""Regressions for the real Team RAG budget, handoff and repair incident."""

from __future__ import annotations

import asyncio
import json

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from src.agent.langgraph_runtime.answer_contract import STRUCTURED_OUTPUT_TOOL_NAME
from src.agent.langgraph_runtime.reflection import reflection_eligibility
from src.agent.langgraph_runtime.runtime import LangGraphRuntimeManager
from src.agent.langgraph_runtime.team import graph as team_graph
from src.agent.langgraph_runtime.team.contracts import (
    AgentResult, CriticReview, DraftSection, TeamPlanDraft, TeamReviewIssue,
)
from src.agent.langgraph_runtime.team.criteria import validate_criteria_assessment
from src.tools.registry import ToolRegistry
from tests.test_langgraph_agent_runtime import FakeAtomicExecutor, ScriptedChatModel
from tests.test_langgraph_multi_agent_team import _bound_tool_names, _call, _probe, _team_plan


def _draft() -> dict:
    return {
        "goal": "分析知识库报告",
        "completion_criteria": ["完成基于报告的分析"],
        "tasks": [{
            "agent_id": "fundamental",
            "objective": "从报告取得财务事实并说明证据缺口",
            "success_criteria": ["已取得核心财务事实"],
        }],
        "synthesis_instructions": "依据证据回答，不编造未披露信息",
    }


def _page() -> dict:
    return {
        "evidence_id": "ev_kb_page_51",
        "action_id": "read-page-51",
        "tool_name": "search_knowledge_base",
        "success": True,
        "usable": True,
        "has_data": True,
        "effect": "read",
        "source_refs": ["knowledge-base:report/page/51"],
        "entities": {"query": "报告第51页"},
        "citation_item": True,
        "result": {"snippet": "营业收入100万元。", "page_start": 51, "page_end": 51},
    }


def test_team_draft_inherits_run_budget_instead_of_a_private_twelve_call_limit():
    registry = ToolRegistry.from_tools([_probe("fundamental_probe", "financials")])
    plan = team_graph._normalize_plan(
        _draft(), registry, state={"team_id": "rag", "tool_call_limit": 1000},
    )
    assert plan["tasks"][0]["max_tool_calls"] == 1000


def test_team_send_preserves_the_shared_run_budget_and_consumption():
    sends = team_graph._send_team_tasks({
        "team_plan": _team_plan(), "tool_call_limit": 1000, "tool_call_count": 17,
    }, ["market-task"])
    assert sends[0].arg["tool_call_limit"] == 1000
    assert sends[0].arg["tool_call_count"] == 17


def test_team_coverage_gap_does_not_manufacture_a_high_risk_conflict():
    state = {
        "team_evidence_catalog": [_page()], "evidence": [_page()],
        "team_evidence_merge": {
            "status": "partial", "evidence_ids": ["ev_kb_page_51"],
            "missing_task_ids": ["fundamental-task"],
        },
    }
    checked = team_graph._normalize_conflict({
        "status": "none", "reason": "没有事实冲突，只有展望正文尚未命中的覆盖缺口",
        "issues": [], "requires_adversarial_review": False,
    }, state)
    assert checked["status"] == "none"
    assert checked["requires_adversarial_review"] is False


def test_research_review_requires_structured_target_task_ids():
    with pytest.raises(ValidationError, match="task_ids"):
        TeamReviewIssue.model_validate({
            "reason": "展望正文尚未取证", "resolution": "research", "task_ids": [],
            "repair_instruction": "重跑 fundamental-task",
        })
    assert TeamReviewIssue.model_validate({
        "reason": "报告没有披露未来订单", "resolution": "qualify", "task_ids": [],
    }).resolution == "qualify"


def test_research_review_rejects_task_ids_outside_the_current_plan():
    with pytest.raises(ValueError, match="task"):
        team_graph._normalize_critic({
            "verdict": "revise", "issues": [{
                "reason": "补采正文", "resolution": "research", "task_ids": ["invented-task"],
            }],
        }, {"team_plan": _team_plan()})


def test_worker_internal_handoff_does_not_run_the_final_answer_reflector():
    eligible, detail = reflection_eligibility({
        "orchestrator_mode": "multi_agent_worker", "status": "completed", "evidence": [_page()],
    }, {
        "profile": "research", "blocks": [{
            "kind": "inference", "content": "基于收入，经营表现改善。", "evidence_ids": ["ev_kb_page_51"],
        }],
    })
    assert eligible is False
    assert detail["reason"] == "team_worker_handoff"


def test_team_review_preserves_the_complete_retrieved_page_not_a_character_prefix():
    page = _page()
    page["result"]["snippet"] = "完整的表格正文。" * 500 + "负债总额100万元。"
    criteria = team_graph._criteria_evidence_packet([page])
    review = team_graph._team_evidence_packet({"team_evidence_catalog": [page], "evidence": [page]})
    assert criteria[0]["observation"] == page["result"]["snippet"]
    assert review[0]["summary"] == page["result"]["snippet"]


def test_worker_reuses_typed_answer_without_a_second_llm_handoff(monkeypatch):
    async def scenario():
        registry = ToolRegistry.from_tools([_probe("market_probe", "market")])
        manager = LangGraphRuntimeManager(registry=registry)
        await manager.start(testing=True)
        try:
            context = manager._context(
                llm_config={}, database=None, controller=None, run_id="typed-worker",
                conversation_id="typed-worker", run_attempt=1, tenant_id="tenant", owner_id="owner",
                model=ScriptedChatModel(), executor=FakeAtomicExecutor(),
            )
            page = _page()
            content = "逐项已核验事实。" * 500 + "最后一条事实不能截断。"
            limitation = "2026年下半年经营计划尚未取得，不是已确认的报告事实。"
            candidate = {
                "profile": "research", "title": "完整交接",
                "blocks": [
                    {"kind": "fact", "content": content, "evidence_ids": [page["evidence_id"]]},
                    {"kind": "disclaimer", "content": limitation, "evidence_ids": []},
                ],
            }
            captured = {}

            def build_child(**kwargs):
                captured["response_format"] = kwargs["response_format"]
                return object()

            async def child_result(_graph, state, **kwargs):
                captured["child_state"] = state
                return {
                    **state, "status": "completed", "structured_answer": candidate,
                    "answer_final": content + "\n" + limitation,
                    "tool_results": [{"action_id": page["action_id"], "tool_name": "search_knowledge_base", "success": True}],
                    "evidence": [page], "tool_call_count": 1, "model_turn_count": 2,
                }

            async def criteria_result(_context, schema, messages, **kwargs):
                assert schema.__name__ == "CriteriaAssessment", "a typed worker must not call WorkerAssessment again"
                packet = json.loads(messages[-1].content)
                captured["criteria_packet"] = packet
                assert content in json.dumps(packet, ensure_ascii=False)
                return {"checks": [{
                    "criterion_index": 1, "verdict": "pass", "explanation": "核心事实已取证",
                    "source_ids": [1],
                }]}, 1

            monkeypatch.setattr(team_graph, "build_agent_graph", build_child)
            monkeypatch.setattr(team_graph, "_invoke_streaming_subgraph", child_result)
            monkeypatch.setattr(team_graph, "_invoke_contract", criteria_result)
            task = {**_team_plan()["tasks"][0], "max_tool_calls": 1000}
            result = await team_graph._run_worker({
                "team_id": "typed-worker", "team_current_task": task, "user_text": "分析报告",
                "tool_call_limit": 1000, "tool_call_count": 5,
            }, context, response_format=None)
            report = result["team_results"][0]
            assert captured["response_format"] is not None
            assert captured["child_state"]["structured_output_required"] is True
            assert captured["child_state"]["tool_call_limit"] == 995
            assert report["findings"] == [content]
            assert report["finding_evidence_refs"] == [[page["evidence_id"]]]
            assert limitation in report["limitations"]
            assert report["status"] == "completed"
            assert report["criteria_checks"][0]["evidence_ids"] == [page["evidence_id"]]
            assert "worker_answer" not in captured["criteria_packet"]
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_worker_criteria_references_are_remapped_to_team_slots():
    first = {**_page(), "evidence_id": "ev_first"}
    second = {**_page(), "evidence_id": "ev_second"}
    report = AgentResult.model_validate({
        "id": "worker-2", "task_id": "worker-2", "agent_id": "fundamental", "status": "completed",
        "criteria_checks": [{
            "criterion_index": 1, "verdict": "pass", "explanation": "已取证",
            "source_ids": [1], "evidence_ids": [second["evidence_id"]],
        }],
    }).model_dump(mode="json")
    state = {"evidence": [first, second], "team_evidence_catalog": [first, second], "team_results": [report]}
    check = team_graph._team_results_packet(state)[0]["criteria_checks"][0]
    assert check["source_ids"] == [2]
    assert "evidence_ids" not in check


def test_team_narrative_and_draft_do_not_have_model_text_length_caps():
    narrative = "模型生成的完整进展。" * 1000 + "最后一句必须保留。"
    assert TeamPlanDraft.model_validate({**_draft(), "progress_text": narrative}).progress_text == narrative
    review = CriticReview.model_validate({"verdict": "pass", "summary": narrative, "progress_text": narrative})
    assert team_graph._contract_projection_text(review.model_dump()) == narrative
    assert DraftSection.model_validate({
        "task_id": "fundamental", "agent_id": "fundamental", "title": "完整初稿", "content": narrative,
    }).content == narrative


def test_duplicate_page_handoff_uses_the_slot_exposed_to_the_criteria_judge():
    page = _page()
    evidence = [page, dict(page)]
    answer = {"profile": "research", "blocks": [{
        "kind": "fact", "content": "营业收入100万元。", "evidence_ids": [page["evidence_id"]],
    }]}
    assessment = team_graph._worker_assessment_from_answer({"structured_answer": answer}, evidence)
    exposed_slots = {str(item["source_id"]) for item in team_graph._criteria_evidence_packet(evidence)}
    assert len(exposed_slots) == 1
    assert set(assessment.finding_evidence_refs[0]) == exposed_slots


def test_criteria_keeps_the_complete_plan_condition_and_model_explanation():
    criterion = "核验已获得的报告事实。" * 100 + "最后一个完成条件。"
    explanation = "所有财务事实已有对应页码证据。" * 100 + "最后一句核验解释。"
    checked = validate_criteria_assessment({"checks": [{
        "criterion_index": 1, "verdict": "pass", "explanation": explanation, "source_ids": [1],
    }]}, criteria=[criterion], evidence=[_page()], records=[])
    assert checked["checks"][0]["criterion"] == criterion
    assert checked["checks"][0]["explanation"] == explanation


def test_critic_repairs_invalid_targets_through_the_existing_contract_loop():
    async def scenario():
        model = ScriptedChatModel(responses=[
            _call("CriticReview", "bad-target", {"verdict": "revise", "issues": [{
                "reason": "补采财务正文", "resolution": "research", "task_ids": ["invented-task"],
            }]}),
            _call("CriticReview", "correct-target", {"verdict": "revise", "issues": [{
                "reason": "补采财务正文", "resolution": "research", "task_ids": ["fundamental-task"],
            }]}),
        ])
        manager = LangGraphRuntimeManager(registry=ToolRegistry.from_tools([_probe("fundamental_probe", "financials")]))
        await manager.start(testing=True)
        try:
            context = manager._context(
                llm_config={}, database=None, controller=None, run_id="critic-repair",
                conversation_id="critic-repair", run_attempt=1, tenant_id="tenant", owner_id="owner",
                model=model, executor=FakeAtomicExecutor(),
            )
            value, calls = await team_graph._invoke_contract(
                context, CriticReview, [SystemMessage(content="独立复核"), HumanMessage(content="补采正文")],
                action_prefix="critic-repair", stage="reflection",
                validator=lambda value: team_graph._normalize_critic(value, {"team_plan": _team_plan()}),
            )
            assert calls == 2
            assert value["issues"][0]["task_ids"] == ["fundamental-task"]
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_exhausted_budget_removes_research_tools_before_the_next_model_call(monkeypatch):
    monkeypatch.setenv("AGENT_MAX_TOOL_CALLS", "1")

    async def scenario():
        model = ScriptedChatModel(responses=[
            _call("market_probe", "read-once", {"query": "观察"}),
            _call(STRUCTURED_OUTPUT_TOOL_NAME, "answer", {
                "profile": "general", "blocks": [{"kind": "answer", "content": "已取得观察。", "source_ids": [1]}],
            }),
        ])
        manager = LangGraphRuntimeManager(registry=ToolRegistry.from_tools([_probe("market_probe", "market")]))
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "取得观察"}], user_text="取得观察",
                system_prompt="", llm_config={}, database=None, controller=None,
                run_id="budget-bind", conversation_id="budget-bind", run_attempt=1,
                tenant_id="tenant", owner_id="owner", model=model, executor=FakeAtomicExecutor(),
                agent_mode="direct",
            )
            assert result.status == "completed"
            assert "market_probe" not in _bound_tool_names(model.call_options[-1].get("tools"))
            assert STRUCTURED_OUTPUT_TOOL_NAME in _bound_tool_names(model.call_options[-1].get("tools"))
        finally:
            await manager.close()

    asyncio.run(scenario())
