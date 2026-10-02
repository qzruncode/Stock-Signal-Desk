"""RAG stays optional in existing modes and its scope stays server-owned."""

import json
from types import SimpleNamespace

import pytest

from src.agent.langgraph_runtime.knowledge_research import research_tool_names
from src.agent.langgraph_runtime.planning import (
    PlanningPlan,
    PlanningStep,
    _contract_retry_instruction,
    _planner_messages,
    normalize_plan,
)
from src.tools.base import tool_execution_context
from src.tools.registry import ToolRegistry


def test_knowledge_tool_accepts_only_a_query_and_has_no_skip_action():
    registry = ToolRegistry()
    search = registry.get_tool("search_knowledge_base")

    assert search is not None
    assert set(search.model_parameters()["properties"]) == {"query"}
    assert search.model_parameters()["required"] == ["query"]
    assert registry.get_tool("skip_knowledge_base") is None
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        registry.validate_model_arguments(
            "search_knowledge_base",
            {"query": "营业收入", "knowledge_base_ids": ["kb-a"]},
        )


def test_selected_search_is_optional_and_only_pdf_is_a_source_constraint():
    names = {"search_knowledge_base", "external_read"}
    selected = {"knowledge_base_ids": ["kb-a"], "user_text": "看下新强联的财报"}

    assert research_tool_names(names, selected) == names
    assert research_tool_names(names, {**selected, "user_text": "只依据已选 PDF 回答"}) == {
        "search_knowledge_base"
    }
    assert research_tool_names(
        names,
        {"knowledge_base_ids": [], "user_text": "看下新强联的财报"},
    ) == {"external_read"}
    assert research_tool_names(
        names,
        {"knowledge_base_ids": [], "user_text": "只依据已选 PDF 回答"},
    ) == set()


def test_plan_directory_exposes_selected_search_without_library_ids_or_gate():
    entries = [
        {"operation": name, "description": name, "effect": "read"}
        for name in ("search_knowledge_base", "search_stocks")
    ]
    context = SimpleNamespace(
        catalog=SimpleNamespace(planner_catalog=lambda: entries),
    )
    state = {"knowledge_base_ids": ["private-kb-id"], "user_text": "看下新强联的财报"}

    prompt, user_payload = _planner_messages(state, context, phase="plan")
    user = json.loads(user_payload.content)

    assert {item["operation"] for item in user["registered_tools"]} == {
        "search_knowledge_base",
        "search_stocks",
    }
    assert "private-kb-id" not in str(user)
    assert "private-kb-id" not in str(prompt.content)
    assert "首轮必须" not in prompt.content
    assert "skip_knowledge_base" not in prompt.content

    unselected_prompt, unselected_payload = _planner_messages(
        {"knowledge_base_ids": [], "user_text": "分析财报"},
        context,
        phase="plan",
    )
    unselected = json.loads(unselected_payload.content)
    assert "search_knowledge_base" not in {item["operation"] for item in unselected["registered_tools"]}
    assert "private-kb-id" not in str(unselected_prompt.content)


def test_plan_requires_tools_for_new_observations_and_evidence_dependency_for_analysis():
    required_fields = set(PlanningStep.model_json_schema()["required"])
    assert {"step_kind", "allowed_tools"}.issubset(required_fields)

    base = {
        "plan_id": "plan-rag-contract",
        "goal": "核实并总结报告数据",
        "initial_state": "用户选择了一份财报",
        "completion_criteria": ["报告事实已经核实"],
    }
    invalid = {
        **base,
        "steps": [
            {
                "step_id": "collect",
                "step_kind": "execute",
                "objective": "取得证券主数据",
                "expected_observation": "返回证券代码、市场和行业记录",
                "allowed_tools": [],
                "completion_criteria": ["证券记录已返回"],
            }
        ],
    }
    with pytest.raises(ValueError, match="execute step collect must allow") as captured:
        normalize_plan(invalid, known_tools=["search_stocks"])
    assert "objective: 取得证券主数据" in str(captured.value)
    repair = _contract_retry_instruction(PlanningPlan, {"message": str(captured.value)})
    assert "一次性找出并修正所有 execute 步骤" in repair
    assert "按每个 objective" in repair

    valid = {
        **base,
        "steps": [
            {
                "step_id": "retrieve",
                "step_kind": "execute",
                "objective": "检索报告中的营业收入",
                "expected_observation": "得到带页码的财报命中",
                "allowed_tools": ["search_knowledge_base"],
                "completion_criteria": ["返回报告命中"],
            },
            {
                "step_id": "analyze",
                "step_kind": "analyze",
                "depends_on": ["retrieve"],
                "objective": "核对两处披露是否一致",
                "expected_observation": "给出有证据支持的一致性判断",
                "allowed_tools": [],
                "completion_criteria": ["差异或一致性结论已说明"],
            },
        ],
    }
    normalized = normalize_plan(valid, known_tools=["search_knowledge_base"])
    assert [step["step_kind"] for step in normalized["steps"]] == ["execute"]
    assert "差异或一致性结论已说明" in normalized["completion_criteria"]


def test_search_tool_uses_only_server_injected_scope(monkeypatch):
    from src.tools import search_knowledge_base as search_module

    class SearchService:
        def __init__(self):
            self.calls = []

        def search(self, query, **kwargs):
            self.calls.append((query, kwargs))
            return {"success": True, "query": query, "results": [], "result_items": [], "no_evidence": True}

    service = SearchService()
    monkeypatch.setattr(search_module, "RagSearchService", lambda: service)

    with tool_execution_context(tenant_id="tenant-a", owner_id="user-a"):
        missing_scope = search_module.search_knowledge_base("营业收入")
    with tool_execution_context(
        tenant_id="tenant-a",
        owner_id="user-a",
        knowledge_base_ids=["kb-selected"],
    ):
        selected_scope = search_module.search_knowledge_base("新强联 2026 年半年度 营业收入")

    assert missing_scope["error_code"] == "knowledge_base_scope_missing"
    assert selected_scope["success"] is True
    assert service.calls == [
        (
            "新强联 2026 年半年度 营业收入",
            {
                "knowledge_base_ids": ["kb-selected"],
                "tenant_id": "tenant-a",
                "owner_id": "user-a",
                "top_k": 5,
            },
        )
    ]
