"""One citation identity and full retrieved text across the existing modes."""

import json

import pytest

from src.agent.langgraph_runtime.goal.graph import (
    _evidence_summary, _goal_evidence, _link_action_evidence_to_criteria,
)
from src.agent.langgraph_runtime.middleware import _tool_message_content, _tool_result_observations
from src.agent.langgraph_runtime.planning import _assessment_evidence, _validate_assessment
from src.agent.langgraph_runtime.team.evidence import merge_worker_evidence
from tests.test_agent_planning import _report, _step


@pytest.fixture
def citation_envelope():
    hit = {
        "evidence_id": "ev_kb_table_page_51", "filename": "report.pdf",
        "page_start": 51, "page_end": 51,
        "text": "表格前部\n" * 400 + "| 归母净利润 | 123456 |",
        "snippet": "仅用于页面预览",
        "url": "/api/v1/knowledge-bases/documents/doc/content#page=51",
    }
    return {
        "evidence_id": "ev_search", "action_id": "read", "tool_name": "search_knowledge_base",
        "success": True, "effect": "read",
        "result": {
            "success": True,
            "results": [hit],
            "result_items": [{"summary": "预览"}],
            "retrieval": {
                "dense_sparse_fusion": "internal-marker",
                "query_variant_count": 4,
                "reranker_model": "internal-reranker",
            },
        },
    }


def test_native_tool_observation_keeps_the_retrieved_table_once(citation_envelope):
    payload = json.loads(_tool_message_content(citation_envelope, citation_envelope))
    hit = citation_envelope["result"]["results"][0]
    assert payload["results"][0]["text"] == hit["text"]
    assert payload["results"][0]["evidence_id"] == hit["evidence_id"]
    assert "result_items" not in payload
    assert "snippet" not in payload["results"][0]
    assert "retrieval" not in payload
    assert "internal-marker" not in json.dumps(payload, ensure_ascii=False)


def test_plan_observations_keep_unique_full_text_and_all_search_outcomes(citation_envelope):
    import copy

    repeated = copy.deepcopy(citation_envelope)
    repeated["action_id"] = "second-search"
    repeated["result"]["query"] = "第二个查询"
    distinct = copy.deepcopy(repeated)
    distinct["result"]["results"][0]["evidence_id"] = "ev_other_page"
    distinct["result"]["results"][0]["text"] += "独立段落的末尾证据"
    changed = copy.deepcopy(citation_envelope)
    changed["result"]["results"][0]["text"] += "同编号但不同内容不能丢失"
    miss = {"tool_name": "search_knowledge_base", "success": True,
            "result": {"success": True, "query": "针对缺口的查询", "no_evidence": True, "results": []}}
    observations = _tool_result_observations([citation_envelope, repeated, distinct, changed, miss])
    original_hit = citation_envelope["result"]["results"][0]
    assert observations[0]["results"][0]["text"] == original_hit["text"]
    assert observations[1]["results"] == [{
        "evidence_id": original_hit["evidence_id"], "same_evidence_as_previous_result": True,
    }]
    assert observations[1]["query"] == "第二个查询"
    assert observations[2]["results"][0]["text"].endswith("独立段落的末尾证据")
    assert observations[3]["results"][0]["text"].endswith("同编号但不同内容不能丢失")
    assert observations[4]["no_evidence"] is True
    assert observations[4]["query"] == "针对缺口的查询"
    assert citation_envelope["result"]["results"][0] == original_hit


def test_plan_assessment_includes_complete_kb_hit_once_across_queries(monkeypatch, citation_envelope):
    import asyncio
    import copy
    from types import SimpleNamespace
    from src.agent.langgraph_runtime.planning import PlanningCoordinatorMiddleware

    repeated = copy.deepcopy(citation_envelope)
    repeated["action_id"] = "second-search"
    repeated["evidence_id"] = "ev_second_search"
    repeated["result"]["query"] = "第二个查询"
    step = _step("first", "核对报告证据")
    state = {
        "planning_plan": {"steps": [step], "completion_criteria": ["核对事实"]},
        "evidence": [citation_envelope, repeated],
    }
    received = {}
    coordinator = PlanningCoordinatorMiddleware()
    citation_envelope["result"]["results"][0]["rrf_score"] = 0.1
    repeated["result"]["results"][0]["rrf_score"] = 0.8

    async def invoke(_context, _schema, messages, _validator):
        received["packet"] = json.loads(messages[1].content)
        return {}, 1

    monkeypatch.setattr(coordinator, "_invoke_contract", invoke)
    asyncio.run(coordinator._assess_step(state, SimpleNamespace(), step, [citation_envelope, repeated]))
    packet = received["packet"]
    assert len(packet["eligible_evidence"]) == 1
    assert packet["eligible_evidence"][0]["observation"] == citation_envelope["result"]["results"][0]
    slot = packet["eligible_evidence"][0]["source_id"]
    assert [item["result"]["source_ids"] for item in packet["operation_observations"]] == [[slot], [slot]]
    assert packet["operation_observations"][1]["result"]["query"] == "第二个查询"
    assert json.dumps(packet, ensure_ascii=False).count("| 归母净利润 | 123456 |") == 1


def test_plan_assessment_accepts_exact_chunk_citations(citation_envelope):
    step = _step("first", "目标")
    state = {
        "planning_plan": {"steps": [step], "completion_criteria": ["完成测试研究"]},
        "evidence": [citation_envelope],
    }
    records = [{"action_id": "read", "success": True, "effect": "read"}]
    assert [e["evidence_id"] for e in _assessment_evidence(state, records)] == ["ev_kb_table_page_51"]
    raw = _report("first", last=True, source_ids=[1]).tool_calls[0]["args"]
    checked = _validate_assessment(raw, state=state, step=step, records=records)
    assert checked["evidence_ids"] == ["ev_kb_table_page_51"]
    with pytest.raises(ValueError, match="unavailable"):
        _validate_assessment({**raw, "source_ids": [999]}, state=state, step=step, records=records)


def test_goal_observation_and_criteria_use_the_same_chunk_identity(citation_envelope):
    state = {"evidence": [citation_envelope]}
    assert set(_goal_evidence(state)) == {"ev_kb_table_page_51"}
    assert _evidence_summary(state)[0]["evidence_id"] == "ev_kb_table_page_51"
    linked = _link_action_evidence_to_criteria(
        [{"criterion_id": "criterion-1", "evidence_ids": []}],
        {"action_id": "read", "criterion_ids": ["criterion-1"]}, citation_envelope,
    )
    assert linked[0]["evidence_ids"] == ["ev_kb_table_page_51"]


def test_team_handoff_accepts_chunks_but_rejects_invented_citations(citation_envelope):
    merge, catalog = merge_worker_evidence([citation_envelope], [{
        "task_id": "research", "status": "completed",
        "evidence_ids": ["ev_kb_table_page_51", "ev_forged"],
    }])
    assert [item["evidence_id"] for item in catalog] == ["ev_kb_table_page_51"]
    assert merge.worker_evidence == {"research": ["ev_kb_table_page_51"]}
    assert merge.invalid_evidence_ids == ["ev_forged"]
