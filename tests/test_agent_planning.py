"""Focused checks for the bounded Planning coordinator."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from typing import Any, Mapping

from langchain_core.messages import AIMessage
from langchain_core.messages import HumanMessage, ToolMessage
from types import SimpleNamespace

from src.agent.langgraph_runtime.graph import DEFAULT_RESPONSE_FORMAT
from src.agent.langgraph_runtime.answer_contract import STRUCTURED_OUTPUT_TOOL_NAME
from src.agent.model_runtime import ModelProviderUnavailableError
from src.agent.langgraph_runtime.planning import (
    PlanningPlan,
    PlanningStep,
    PlanningStepReport,
    PlanningCoordinatorMiddleware,
    normalize_plan,
    planning_model_messages,
    planning_allowed_tools,
    _contract_retry_instruction,
    _validate_assessment,
    _assessment_evidence,
    _publish_model_progress,
    planning_trace,
    _planner_messages,
    planning_prompt,
    resolve_planning_mode,
)
from src.agent.langgraph_runtime.runtime import LangGraphRuntimeManager
from src.agent.run_registry import RunBroadcaster
from tests.test_langgraph_agent_runtime import (
    FakeAtomicExecutor,
    ScriptedChatModel,
    _named_tool_call,
    _registry,
    _search_operation,
    _structured_output_call,
)


def _planner_call(
    call_id: str,
    steps: list[Mapping[str, Any]],
    *,
    revision: int = 1,
    plan_summary: str = "",
) -> AIMessage:
    args = {
        "plan_id": "plan-test",
        "goal": "完成测试研究",
        "initial_state": "没有已取得观察",
        "revision": revision,
        "completion_criteria": ["完成测试研究"],
        "steps": [dict(step) for step in steps],
    }
    if plan_summary:
        args["plan_summary"] = plan_summary
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "PlanningPlan",
                "args": args,
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


def _route_call(call_id: str, mode: str, *, reason: str = "依据任务语义和工具需求") -> AIMessage:
    return _named_tool_call(
        call_id,
        "PlanningRoute",
        {"mode": mode, "reason": reason},
    )


def _step(step_id: str, objective: str, *, depends_on: list[str] | None = None) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "step_kind": "execute",
        "depends_on": depends_on or [],
        "objective": objective,
        "expected_observation": "获得可核验的真实工具观察",
        "allowed_tools": ["search_source"],
        "completion_criteria": ["工具调用成功"],
    }


def _model_step(step_id: str, objective: str, *, depends_on: list[str] | None = None) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "step_kind": "analyze",
        "depends_on": depends_on or [],
        "objective": objective,
        "expected_observation": "基于已完成步骤形成可核验的步骤摘要",
        "allowed_tools": [],
        "completion_criteria": ["返回基于既有观察的步骤摘要"],
    }


def test_step_report_retry_explains_how_to_repair_an_incomplete_completion() -> None:
    instruction = _contract_retry_instruction(
        PlanningStepReport,
        {'message': 'incomplete criteria cannot be reported as completed'},
    )

    assert '不能是 completed' in instruction
    assert 'continue' in instruction
    assert 'replan' in instruction
    assert 'blocked' in instruction


def _report(
    step_id,
    call_ids=(),
    *,
    last=False,
    outcome="completed",
    model_only=False,
    source_ids=None,
    goal_criterion_count=1,
):
    ids = source_ids if source_ids is not None else list(range(1, len(call_ids) + 1))
    met = outcome == "completed"
    summary = "来源和时间口径已经确认，可以据此继续分析。" if met else "该来源没有提供所需数据，需要补充观察。"
    return _named_tool_call(
        "report-call",
        "PlanningStepReport",
        {
            "step_id": step_id,
            "outcome": outcome,
            "completed_summary": summary,
            "observed_facts": [summary],
            "source_ids": ids,
            "criteria_checks": [{"criterion_index": 1, "satisfied": met, "explanation": summary, "source_ids": ids}],
            "expected_observation_met": met,
            "unmet_criteria": [] if met else ["缺少所需数据"],
            "remaining_plan_valid": True,
            "goal_satisfied": last and met,
            "goal_checks": (
                [
                    {"criterion_index": index, "satisfied": met, "explanation": summary, "source_ids": ids}
                    for index in range(1, goal_criterion_count + 1)
                ]
                if last
                else []
            ),
            "goal_missing_items": [],
            "progress_text": summary,
            "next_step_hint": "基于证据继续",
        },
    )


def test_auto_does_not_classify_by_words_length_or_language() -> None:
    for text in ("未来产业怎么选？", "解释研究报告这个词", "Compare two businesses", "长" * 120):
        assert resolve_planning_mode(text, "auto") == "auto"
    assert resolve_planning_mode("简单问题", "planned") == "planned"
    assert resolve_planning_mode("系统分析", "direct") == "direct"


def test_planning_coordinator_can_choose_pdf_research_and_assess_coverage() -> None:
    context = SimpleNamespace(
        catalog=SimpleNamespace(
            planner_catalog=lambda: [{
                "operation": "search_knowledge_base",
                "description": "检索当前对话已选择的知识库",
                "effect": "read",
                "category": "research",
            }]
        )
    )
    state = {
        "planning_mode": "planned",
        "knowledge_base_ids": ["kb-selected"],
        "user_text": "根据所选 PDF，核实报告对经营风险的说明。",
        "messages": [],
        "evidence": [],
        "tool_results": [],
        "planning_plan": None,
        "planning_step_reports": [],
    }

    planner_system = str(_planner_messages(state, context, phase="plan")[0].content)
    step_prompt = planning_prompt({
        **state,
        "planning_enabled": True,
        "planning_status": "executing",
        "planning_plan": {
            "goal": "核实经营风险",
            "initial_state": "尚无本轮文档证据",
            "steps": [{
                **_step("pdf-research", "检索所选报告中的经营风险"),
                "allowed_tools": ["search_knowledge_base"],
                "completion_criteria": ["命中内容足以支持所需结论并可定位页码"],
            }],
        },
        "planning_current_step_id": "pdf-research",
    })

    assert "首轮必须由你先作一次明确的来源决策" not in planner_system
    assert "skip_knowledge_base" not in planner_system
    assert "知识库检索是当前 Agent 工具循环中的可选取证能力" in planner_system
    assert "优先调用 search_knowledge_base" not in planner_system
    assert "计划级 completion_criteria 只写最终回答前必须满足的可观察事实" in planner_system
    assert "不要把‘用表格回答’‘简洁’" in planner_system
    assert "把这些要求写入计划级 completion_criteria" not in planner_system
    assert "query 应保留实体、报告期/版本、指标/概念和口径" in planner_system
    assert "允许工具：search_knowledge_base" in step_prompt
    # The worker gets the shared tool context from AgentPromptMiddleware;
    # its existing planning instructions must not inject it a second time.
    assert "本轮取证工具优先级" not in step_prompt
    assert "本轮取证工具优先级" not in str(_planner_messages(state, context, phase="route")[0].content)

    no_kb_prompt = str(
        _planner_messages(
            {**state, "knowledge_base_ids": []},
            context,
            phase="plan",
        )[0].content
    )
    assert "search_knowledge_base" not in no_kb_prompt
    assert "所选知识库" not in no_kb_prompt


def test_pdf_only_plan_excludes_non_kb_readers_and_uses_page_hits_as_evidence() -> None:
    context = SimpleNamespace(
        catalog=SimpleNamespace(
            planner_catalog=lambda: [
                {"operation": "search_knowledge_base", "description": "检索已选知识库", "effect": "read"},
                {"operation": "read_text_document", "description": "读取会话上传文件", "effect": "read"},
                {"operation": "search_web_source", "description": "搜索网页", "effect": "read"},
            ]
        )
    )
    state = {
        "planning_mode": "planned",
        "knowledge_base_ids": ["kb-selected"],
        "user_text": "只依据已选 PDF，核对第7页和第19页的营业收入及同比是否一致。",
        "messages": [],
        "evidence": [],
        "tool_results": [],
        "planning_plan": None,
        "planning_step_reports": [],
    }

    messages = _planner_messages(state, context, phase="plan")
    catalog = json.loads(messages[1].content)["registered_tools"]
    planner_system = str(messages[0].content)

    assert [item["operation"] for item in catalog] == ["search_knowledge_base"]
    assert "逐页原文片段和页码就是可引用证据" in planner_system
    assert "不要把知识库文档 ID 传给 read_text_document" in planner_system
    assert "不要为读取搜索结果中已经包含的数值另建步骤" in planner_system


def test_selected_kb_plan_context_skips_old_answers_and_bounds_tool_descriptions():
    state = {
        "planning_mode": "planned",
        "knowledge_base_ids": ["kb-selected"],
        "user_text": "查询新强联半年报营收",
        "messages": [
            HumanMessage(content="上一轮我们讨论了新强联。"),
            AIMessage(content="旧答案不应带入计划。" * 500),
            HumanMessage(content="查询新强联半年报营收"),
        ],
    }
    context = SimpleNamespace(
        catalog=SimpleNamespace(
            planner_catalog=lambda: [
                {
                    "operation": "search_knowledge_base",
                    "description": "检索已选择的知识库资料。" + "详细说明。" * 100,
                    "effect": "read",
                    "category": "research",
                }
            ]
        )
    )

    messages = _planner_messages(state, context, phase="plan")
    prompt_payload = json.loads(messages[1].content)

    assert "旧答案不应带入计划" not in messages[1].content
    assert any("上一轮我们讨论了新强联。" in item["content"] for item in prompt_payload["recent_conversation"])
    assert any(item["content"] == state["user_text"] for item in prompt_payload["recent_conversation"])
    assert len(prompt_payload["registered_tools"][0]["description"]) <= 180


def test_worker_context_keeps_only_current_step_pairs_without_mutating_history():
    old = _structured_output_call("old-answer", [{"kind": "answer", "content": "上轮回答"}], profile="general")
    call = _named_tool_call("current-read", "search_source", {"query": "本轮"})
    history = [
        HumanMessage(content="上轮问题"),
        old,
        ToolMessage(content="上轮回答协议", tool_call_id="old-answer"),
        HumanMessage(content="本轮问题"),
        call,
        ToolMessage(content="本轮观察", tool_call_id="current-read"),
    ]
    state = {
        "planning_enabled": True,
        "planning_status": "executing",
        "user_text": "本轮问题",
        "planning_step_tool_call_ids": ["current-read"],
    }
    projected = planning_model_messages(state, history)
    assert projected[0].content == "本轮问题"
    assert projected[1:] == history[4:]
    assert len(history) == 6 and history[2].content == "上轮回答协议"
    assert planning_model_messages({"planning_enabled": False}, history) == history


def test_selected_kb_is_shared_with_executing_plan_steps_but_not_unselected_or_analysis_steps():
    plan = {
        "steps": [
            {"step_id": "read", "allowed_tools": ["search_stocks"]},
            {"step_id": "analyze", "allowed_tools": []},
        ]
    }
    state = {
        "planning_enabled": True,
        "planning_status": "executing",
        "planning_plan": plan,
        "planning_current_step_id": "read",
        "knowledge_base_ids": ["kb-selected"],
    }

    assert planning_allowed_tools(state) == {"search_stocks", "search_knowledge_base"}
    assert planning_allowed_tools({**state, "knowledge_base_ids": []}) == {"search_stocks"}
    assert planning_allowed_tools({**state, "planning_current_step_id": "analyze"}) == set()

    prompt = planning_prompt({**state, "planning_current_step_id": "analyze"})
    assert "服务端会决定是否重规划并开放所需工具" in prompt


def test_plan_finalization_prompt_excludes_internal_step_reports():
    prompt = planning_prompt(
        {
            "planning_enabled": True,
            "planning_status": "finalizing",
            "user_text": "只给财务数据简洁表格，列出数值和页码。",
            "planning_plan": {
                "goal": "汇总报告指标",
                "completion_criteria": ["指标、同比和页码齐全"],
                "steps": [
                    {
                    "step_id": "step_1",
                    "objective": "检索所选半年报",
                    "status": "completed",
                }
            ],
            },
            "planning_step_reports": [
                {
                    "step_id": "step_1",
                    "completed_summary": "收入、归母净利润和经营现金流均已核实，页码为第7页。",
                    "observed_facts": ["营业收入 2,073,339,945.39 元，同比 -6.17%。"],
                    "source_ids": [2, 3],
                    "criteria_checks": [{
                        "criterion": "主要会计指标及页码齐全",
                        "satisfied": True,
                        "explanation": "第7页表格覆盖三项指标。",
                        "source_ids": [3],
                        "evidence_ids": ["ev_private"],
                        "internal_only": "must not enter synthesis",
                    }],
                    "goal_checks": [{
                        "criterion": "所有内部验收标准均已完成",
                        "satisfied": True,
                        "explanation": "这是流程状态，不应进入最终答案素材。",
                        "source_ids": [4],
                    }],
                    "debug_payload": "must not enter synthesis",
                }
            ],
        }
    )

    assert "汇总报告指标" not in prompt
    assert "只给财务数据简洁表格，列出数值和页码" not in prompt
    assert "营业收入 2,073,339,945.39 元" not in prompt
    assert "第7页表格覆盖三项指标" not in prompt
    assert "source_ids" not in prompt
    assert "完整表头、分隔行" in prompt
    assert "本轮原始提问、实际工具观察和来源目录" in prompt
    assert "内部执行状态不属于业务事实" in prompt
    assert "criteria_checks" not in prompt
    assert "goal_checks" not in prompt
    assert "completion_criteria" not in prompt
    assert "所有内部验收标准均已完成" not in prompt
    assert "检索所选半年报" not in prompt
    assert "debug_payload" not in prompt
    assert "must not enter synthesis" not in prompt
    assert "ev_private" not in prompt


def test_semantic_repair_replays_native_tool_error_not_a_success_receipt():
    answer = _structured_output_call(
        "candidate", [{"kind": "fact", "content": "待修订事实", "source_ids": [1]}], profile="research"
    )
    receipt = ToolMessage(
        content="Returning structured response: 已成功解析", tool_call_id="candidate", status="success"
    )
    state = {
        "planning_enabled": True,
        "planning_status": "finalizing",
        "user_text": "本轮问题",
        "evidence_feedback": "时间没有证据支持，请修订引用或内容",
    }
    projected = planning_model_messages(state, [HumanMessage(content="本轮问题"), answer, receipt])
    assert projected[-1].status == "error"
    assert "时间没有证据支持" in projected[-1].content
    assert receipt.status == "success" and "Returning structured response" in receipt.content


def test_replan_keeps_cross_revision_dependencies_and_completed_steps():
    completed = {**_step("first", "已核实事实"), "status": "completed"}
    raw = {
        "goal": "目标",
        "initial_state": "状态",
        "completion_criteria": ["完成目标"],
        "steps": [_step("second", "继续", depends_on=["first"])],
    }
    revised = normalize_plan(raw, known_tools=["search_source"], completed_steps=[completed], revision=2)
    assert revised["steps"][0] == completed
    assert revised["steps"][1]["depends_on"] == ["first"]
    with pytest.raises(ValueError):
        normalize_plan(
            {**raw, "steps": [_step(f"s{i}", "目标") for i in range(8)]},
            known_tools=["search_source"],
            completed_steps=[completed],
        )


def test_plan_requires_completion_criteria_and_migrates_legacy_omission() -> None:
    assert PlanningPlan.model_fields["completion_criteria"].is_required()

    normalized = normalize_plan(
        {
            "goal": "核对财报指标",
            "initial_state": "尚无本轮观察",
            "steps": [_step("read", "获取财报指标")],
        },
        known_tools=["search_source"],
        user_text="核对财报指标",
    )

    assert normalized["completion_criteria"] == ["完成原计划目标：核对财报指标"]
    with pytest.raises(ValueError):
        normalize_plan(
            {
                "goal": "核对财报指标",
                "initial_state": "尚无本轮观察",
                "completion_criteria": [],
                "steps": [_step("read", "获取财报指标")],
            },
            known_tools=["search_source"],
        )


def test_zero_replan_budget_stops_without_a_model_call():
    async def scenario():
        events = SimpleNamespace(
            stage=lambda stage, status, summary, **kw: {
                "stage": stage,
                "status": status,
                "summary": summary,
                **kw,
            }
        )
        state = {"planning_plan": {"steps": [_step("first", "目标")]}, "planning_replan_limit": 0}
        result = await PlanningCoordinatorMiddleware()._replan(
            state,
            SimpleNamespace(events=events),
            step=_step("first", "目标"),
            records=[],
            reason="缺数据",
        )
        assert result["planning_status"] == "blocked"
        assert "重规划预算已用尽" in result["planning_error"]

    asyncio.run(scenario())


async def _run_extra(
    responses, *, mode="planned", user_text="测试问题", registry=None, agent_mode=None
):
    manager = LangGraphRuntimeManager(
        registry=registry or _registry(_search_operation()), response_format=DEFAULT_RESPONSE_FORMAT
    )
    model = ScriptedChatModel(responses=responses)
    executor = FakeAtomicExecutor()
    await manager.start(testing=True)
    try:
        result = await manager.run_new(
            messages=[{"role": "user", "content": user_text}],
            user_text=user_text,
            system_prompt="",
            llm_config={},
            database=None,
            controller=None,
            run_id="run-extra",
            conversation_id="extra",
            run_attempt=1,
            tenant_id="tenant",
            owner_id="owner",
            model=model,
            executor=executor,
            planning_mode=mode,
            agent_mode=agent_mode or (
                "plan" if mode in {"planned", "auto"} else "direct"
            ),
        )
        return result, model, executor
    finally:
        await manager.close()


@pytest.mark.parametrize("mode,text", [("planned", "未来产业怎么选？"), ("direct", "解释研究报告这个词")])
def test_product_auto_routes_to_existing_direct_or_plan_mode(mode, text):
    async def scenario():
        plan = _planner_call("plan", [_step("first", "目标")]).tool_calls[0]["args"]
        responses = [
            _named_tool_call(
                "product-route",
                "ProductModeRoute",
                {
                    "mode": "plan" if mode == "planned" else "direct",
                    "reason": "依据任务语义和工具需求",
                },
            )
        ]
        if mode == "planned":
            responses.append(_planner_call("plan", plan["steps"]))
            responses += [
                _named_tool_call("read", "search_source", {"source_id": "primary", "query": "研究"}),
                _report("first", ["read"], last=True),
            ]
        responses += [
            _structured_output_call(
                "final",
                (
                    [{"kind": "fact", "content": "结论", "source_ids": [1]}]
                    if mode == "planned"
                    else [{"kind": "answer", "content": "概念解释"}]
                ),
                profile="research" if mode == "planned" else "general",
            )
        ]
        result, model, executor = await _run_extra(
            responses, mode="auto", user_text=text, agent_mode="auto"
        )
        assert result.status == "completed"
        assert result.state["resolved_agent_mode"] == ("plan" if mode == "planned" else "direct")
        assert result.state["planning_mode"] == ("planned" if mode == "planned" else "direct")
        assert result.state["orchestrator_route_reason"] == "依据任务语义和工具需求"
        assert text in model.calls[0][-1].content
        assert model.call_options[0]["tools"][0].__name__ == "ProductModeRoute"
        assert len(executor.calls) == (1 if mode == "planned" else 0)

    asyncio.run(scenario())


def test_planner_failure_does_not_fall_back_to_unplanned_execution():
    async def scenario():
        bad = {**_step("first", "目标"), "allowed_tools": ["invented_tool"]}
        result, model, executor = await _run_extra(
            [
                _planner_call("bad1", [bad]),
                _planner_call("bad2", [bad]),
            ]
        )
        assert result.status == "partial" and result.error_code == "planning_generation_failed"
        assert result.state["planning_status"] == "blocked" and executor.calls == []
        assert "unregistered tools" in model.calls[1][-1].content
        assert len(model.calls) == 2
        assert "尚未执行任何工具" in result.final_text

    asyncio.run(scenario())


def test_plan_recovers_stringified_steps_and_enforces_an_explicit_selected_kb_search():
    async def scenario():
        step = {
            **_model_step("research", "总结新强联的运营情况和风险"),
            "step_kind": "execute",
            "allowed_tools": ["search_knowledge_base"],
        }
        args = _planner_call("raw-plan", [step]).tool_calls[0]["args"]
        args["steps"] = json.dumps(args["steps"], ensure_ascii=False)
        raw_result = {
            "raw": AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "PlanningPlan",
                        "args": args,
                        "id": "raw-plan",
                        "type": "tool_call",
                    }
                ],
            ),
            "parsed": None,
            "parsing_error": ValueError("steps must be a list"),
        }

        class StructuredModel:
            supports_exact_structured_output = False

            def __init__(self):
                self.calls = 0

            def with_structured_output(self, *_args, **_kwargs):
                return self

            async def ainvoke(self, *_args, **_kwargs):
                self.calls += 1
                return raw_result

        model = StructuredModel()
        events = SimpleNamespace(stage=lambda *_args, **_kwargs: {})
        catalog = SimpleNamespace(
            planner_catalog=lambda: [
                {"operation": "search_knowledge_base", "description": "检索用户已选知识库"},
            ]
        )
        context = SimpleNamespace(
            model=model,
            events=events,
            catalog=catalog,
            searchable_knowledge_bases=(),
        )
        state = {
            "planning_mode": "planned",
            "knowledge_base_ids": ["kb-selected"],
            "user_text": "在已选知识库中检索新强联财报，基于命中内容总结运营情况和风险，并引用证据。",
            "messages": [],
            "evidence": [],
            "tool_results": [],
            "planning_plan": None,
            "planning_step_reports": [],
        }

        decision, calls = await PlanningCoordinatorMiddleware()._create_plan(state, context)

        assert calls == 1 and model.calls == 1
        first = decision["plan"]["steps"][0]
        assert first["allowed_tools"] == ["search_knowledge_base"]
        assert first["depends_on"] == []
        assert first["step_kind"] == "execute"
        assert first["objective"] == "总结新强联的运营情况和风险"

    asyncio.run(scenario())


def test_plan_keeps_selected_retrieval_as_a_normal_tool_step():
    async def scenario():
        step = {
            **_step("research", "检索新强联2026年半年报的营业收入与归母净利润"),
            "allowed_tools": ["search_knowledge_base"],
        }
        raw_result = {
            "raw": AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "PlanningPlan",
                        "args": _planner_call("implicit-kb-plan", [step]).tool_calls[0]["args"],
                        "id": "implicit-kb-plan",
                        "type": "tool_call",
                    }
                ],
            ),
            "parsed": None,
            "parsing_error": ValueError("steps must be a list"),
        }

        class StructuredModel:
            supports_exact_structured_output = False

            def __init__(self):
                self.calls = 0

            def with_structured_output(self, *_args, **_kwargs):
                return self

            async def ainvoke(self, *_args, **_kwargs):
                self.calls += 1
                return raw_result

        model = StructuredModel()
        events = SimpleNamespace(stage=lambda *_args, **_kwargs: {})
        catalog = SimpleNamespace(
            planner_catalog=lambda: [
                {"operation": "search_knowledge_base", "description": "检索用户已选知识库"},
            ]
        )
        context = SimpleNamespace(
            model=model,
            events=events,
            catalog=catalog,
            searchable_knowledge_bases=(),
        )
        state = {
            "planning_mode": "planned",
            "knowledge_base_ids": ["kb-selected"],
            "user_text": "查询新强联2026年半年报的营业收入与归母净利润",
            "messages": [],
            "evidence": [],
            "tool_results": [],
            "planning_plan": None,
            "planning_step_reports": [],
        }

        decision, calls = await PlanningCoordinatorMiddleware()._create_plan(state, context)

        assert calls == 1 and model.calls == 1
        first = decision["plan"]["steps"][0]
        assert first["step_kind"] == "execute"
        assert first["allowed_tools"] == ["search_knowledge_base"]
        assert first["depends_on"] == []
        assert "来源决策" not in first["objective"]

    asyncio.run(scenario())


def test_step_report_recovers_stringified_observed_facts_after_parsed_schema_failure() -> None:
    report = {
        "step_id": "step_1",
        "outcome": "completed",
        "completed_summary": "本轮检索已完成。",
        "observed_facts": json.dumps(["一次命中报告第7页指标表。"], ensure_ascii=False),
        "source_ids": json.dumps([1]),
        "criteria_checks": json.dumps([
            {
                "criterion_index": 1,
                "satisfied": True,
                "explanation": "已获得可核验的命中。",
                "source_ids": json.dumps([1]),
            }
        ], ensure_ascii=False),
        "expected_observation_met": True,
        "unmet_criteria": json.dumps([]),
        "remaining_plan_valid": True,
        "goal_satisfied": True,
        "goal_checks": json.dumps([]),
        "goal_missing_items": json.dumps([]),
        "progress_text": "检索已完成。",
    }
    raw_message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "PlanningStepReport",
                "args": report,
                "id": "report-call",
                "type": "tool_call",
            }
        ],
    )
    raw_result = {
        # Some OpenAI-compatible parsers return a partially parsed mapping
        # instead of None when a single list field arrives as JSON text.
        "parsed": dict(report),
        "raw": raw_message,
    }

    class StructuredModel:
        def __init__(self) -> None:
            self.calls = 0

        def with_structured_output(self, *_args: Any, **_kwargs: Any) -> "StructuredModel":
            return self

        async def ainvoke(self, *_args: Any, **_kwargs: Any) -> Mapping[str, Any]:
            self.calls += 1
            return raw_result

    model = StructuredModel()
    context = SimpleNamespace(
        model=model,
        events=SimpleNamespace(stage=lambda *_args, **_kwargs: {}),
    )

    recovered, calls = asyncio.run(
        PlanningCoordinatorMiddleware._invoke_contract(
            context,
            PlanningStepReport,
            [],
            lambda value: PlanningStepReport.model_validate(value).model_dump(mode="json"),
        )
    )

    assert calls == 1
    assert model.calls == 1
    assert recovered["observed_facts"] == ["一次命中报告第7页指标表。"]
    assert recovered["step_id"] == "step_1"


def test_plan_route_without_structured_tool_call_fails_closed_before_execution():
    async def scenario():
        result, model, executor = await _run_extra(
            [
                AIMessage(content="我先分析一下。"),
                AIMessage(content="目前还不能确定。"),
            ],
            mode="auto",
            agent_mode="plan",
        )
        assert result.status == "partial"
        assert result.state["planning_status"] == "blocked"
        assert result.state["planning_model_call_count"] == 2
        assert "structured_output_missing_tool_call" in result.state["planning_error"]
        assert executor.calls == []
        assert "尚未执行任何工具" in result.final_text
        assert len(model.calls) == 2

    asyncio.run(scenario())


def test_plan_provider_errors_propagate_without_contract_retry():
    class StructuredModel:
        def __init__(self) -> None:
            self.calls = 0

        def with_structured_output(self, *_args: Any, **_kwargs: Any) -> "StructuredModel":
            return self

        async def ainvoke(self, *_args: Any, **_kwargs: Any) -> Mapping[str, Any]:
            self.calls += 1
            raise ModelProviderUnavailableError("model provider is temporarily unavailable")

    model = StructuredModel()
    staged_events: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    context = SimpleNamespace(
        model=model,
        events=SimpleNamespace(stage=lambda *args, **kwargs: staged_events.append((args, kwargs))),
    )

    with pytest.raises(ModelProviderUnavailableError):
        asyncio.run(
            PlanningCoordinatorMiddleware._invoke_contract(
                context,
                PlanningPlan,
                [],
                lambda value: PlanningPlan.model_validate(value).model_dump(mode="json"),
            )
        )

    assert model.calls == 1
    assert staged_events == []


def test_successful_tool_with_unmet_criteria_continues_the_same_step():
    async def scenario():
        result, model, executor = await _run_extra(
            [
                _planner_call("plan", [_step("first", "补齐事实")]),
                _named_tool_call("read", "search_source", {"source_id": "primary", "query": "部分"}),
                _report("first", ["read"], outcome="continue"),
                _named_tool_call("supplement", "search_source", {"source_id": "primary", "query": "补充"}),
                _report("first", ["read", "supplement"], last=True),
                _structured_output_call(
                    "final", [{"kind": "fact", "content": "补齐后的结论", "source_ids": [1, 2]}], profile="research"
                ),
            ]
        )
        assert result.status == "completed" and len(executor.calls) == 2
        reports = result.state["planning_step_reports"]
        assert [r["status"] for r in reports] == ["running", "completed"]
        assert reports[-1]["observed_tool_count"] == 2

    asyncio.run(scenario())


def test_continue_hands_off_the_full_gap_and_requires_an_allowed_tool_even_after_a_plain_reply():
    async def scenario():
        continuation = _report("first", ["read"], outcome="continue")
        args = continuation.tool_calls[0]["args"]
        args["completed_summary"] = "已取得部分观察，但不足以完成当前取证。" * 150
        args["unmet_criteria"] = ["缺少分产品经营情况"]
        args["next_step_hint"] = "换用主营业务构成关键词补查；仍未命中则保留检索边界，不重复相同查询。"
        premature_reply = "我认为可以直接整理现有结果。"
        result, model, executor = await _run_extra(
            [
                _planner_call("plan", [_step("first", "补齐事实")]),
                _named_tool_call("read", "search_source", {"source_id": "primary", "query": "部分"}),
                continuation,
                AIMessage(content=premature_reply),
                _named_tool_call("supplement", "search_source", {"source_id": "primary", "query": "主营业务构成"}),
                _report("first", ["read", "supplement"], last=True),
                _structured_output_call(
                    "final", [{"kind": "fact", "content": "补齐后的结论", "source_ids": [1, 2]}], profile="research"
                ),
            ]
        )

        assert result.status == "completed"
        assert len(executor.calls) == 2
        for index in (3, 4):
            request_text = "\n".join(str(message.content) for message in model.calls[index])
            assert args["next_step_hint"] in request_text
            assert args["unmet_criteria"][0] in request_text
            assert model.call_options[index]["tool_choice"] == "required"
        assert any(message.content == premature_reply for message in model.calls[4])
        assert model.call_options[1]["tool_choice"] == "auto"
        assert result.state["planning_no_progress_attempts"] == 0

    asyncio.run(scenario())


def test_plain_reply_does_not_spend_the_next_tool_batch_attempt():
    async def scenario():
        result, _, executor = await _run_extra(
            [
                _planner_call("plan", [_step("first", "补齐事实")]),
                _named_tool_call("read", "search_source", {"source_id": "primary", "query": "部分"}),
                _report("first", ["read"], outcome="continue"),
                _named_tool_call("second", "search_source", {"source_id": "primary", "query": "补查"}),
                _report("first", ["read", "second"], outcome="continue"),
                AIMessage(content="我准备整理已有结果。"),
                _named_tool_call("third", "search_source", {"source_id": "primary", "query": "缺口"}),
                _report("first", ["read", "second", "third"], last=True),
                _structured_output_call(
                    "final", [{"kind": "fact", "content": "已取得全部观察", "source_ids": [1, 2, 3]}], profile="research"
                ),
            ]
        )

        assert result.status == "completed"
        assert len(executor.calls) == 3
        assert result.state["planning_step_reports"][-1]["observed_tool_count"] == 3

    asyncio.run(scenario())


def test_stalled_worker_replans_with_real_observations_instead_of_reporting_no_observation(monkeypatch):
    coordinator = PlanningCoordinatorMiddleware()
    step = _step("second", "补查缺口")
    records = [
        {"action_id": f"read-{i}", "tool_name": "search_source", "success": True, "result": {"value": i}}
        for i in range(3)
    ]
    state = {
        "planning_enabled": True,
        "planning_status": "executing",
        "planning_plan": {"steps": [step], "completion_criteria": ["已有可核验观察"]},
        "planning_current_step_id": "second",
        "planning_step_attempts": 2,
        "planning_no_progress_attempts": 2,
        "planning_step_tool_call_ids": [r["action_id"] for r in records],
        "planning_feedback": "需要继续补查缺口，而不是复述已有摘要。",
        "tool_results": records,
        "messages": [AIMessage(content="我仍认为可以整理已有结果。")],
    }
    received = {}

    async def replan(_state, _context, **kwargs):
        received.update(kwargs)
        return {"planning_status": "executing", "planning_replan_count": 1}

    monkeypatch.setattr(coordinator, "_replan", replan)
    context = SimpleNamespace(events=SimpleNamespace(commit_model_progress=lambda: None))
    result = asyncio.run(coordinator.aafter_model(state, SimpleNamespace(context=context)))

    assert result["planning_status"] == "executing"
    assert result["planning_replan_count"] == 1
    assert received["records"] == records
    assert state["planning_feedback"] in received["reason"]
    assert "没有产生可核验观察" not in received["reason"]


def test_step_assessment_receives_full_observations_and_checks_readiness_not_a_written_answer(monkeypatch):
    coordinator = PlanningCoordinatorMiddleware()
    step = _step("first", "确认事实和边界")
    body = "财报原文观察。" * 1600 + "末尾仍有本期主营构成数据"
    record = {"action_id": "read", "tool_name": "search_source", "success": True, "result": {"text": body}}
    evidence = {**record, "evidence_id": "ev_read"}
    state = {
        "user_text": "根据已有报告证据形成分析。",
        "planning_plan": {"steps": [step], "completion_criteria": ["分析覆盖已命中核心章节并注明缺口"]},
        "evidence": [evidence],
    }
    received = {}

    async def invoke(_context, _schema, messages, _validator):
        received["system"] = messages[0].content
        received["packet"] = json.loads(messages[1].content)
        return {}, 1

    monkeypatch.setattr(coordinator, "_invoke_contract", invoke)
    asyncio.run(coordinator._assess_step(state, SimpleNamespace(), step, [record]))

    packet = received["packet"]
    assert packet["operation_observations"][0]["result"] == record["result"]
    assert packet["eligible_evidence"][0]["observation"] == record["result"]
    assert packet["evaluation_phase"] == "pre_final_evidence_readiness"
    assert "分析尚未形成" in received["system"]
    assert "检索未命中不等于文档不存在" in received["system"]


def test_fabricated_evidence_cannot_complete_a_step():
    async def scenario():
        result, _, _ = await _run_extra(
            [
                _planner_call("plan", [_step("first", "目标")]),
                _named_tool_call("read", "search_source", {"source_id": "primary", "query": "真实"}),
                _report("first", last=True, source_ids=[999]),
                _report("first", last=True, source_ids=[999]),
                _structured_output_call(
                    "final", [{"kind": "fact", "content": "仅保留实际观察", "source_ids": [1]}], profile="research"
                ),
            ]
        )
        assert result.status == "partial"
        assert result.state["planning_status"] == "blocked"
        assert not result.state["planning_step_reports"]

    asyncio.run(scenario())


def test_goal_criteria_are_referenced_by_stable_index_not_retyped_text():
    step = _step("first", "目标")
    raw = _report("first", last=True, source_ids=[2]).tool_calls[0]["args"]
    evidence = {"evidence_id": "ev_read", "action_id": "read", "success": True, "result": {"value": 1}}
    state = {
        "planning_plan": {"steps": [step], "completion_criteria": ["用户的原始完成条件"]},
        "evidence": [{"evidence_id": "ev_failed", "success": False}, evidence],
    }
    records = [{"action_id": "read", "success": True, "effect": "read"}]
    checked = _validate_assessment(raw, state=state, step=step, records=records)
    assert checked["goal_checks"][0]["criterion"] == "用户的原始完成条件"
    assert checked["evidence_ids"] == ["ev_read"]
    with pytest.raises(ValueError, match="goal_checks"):
        _validate_assessment({**raw, "goal_checks": []}, state=state, step=step, records=records)


def test_replan_can_reuse_valid_evidence_from_a_partially_completed_step():
    valid = {"evidence_id": "ev_partial", "action_id": "partial", "success": True, "result": {"value": 1}}
    failed = {"evidence_id": "ev_failed", "action_id": "failed", "success": False, "result": {"error": "unavailable"}}
    unrelated = {**valid, "evidence_id": "ev_unrelated"}
    state = {
        "evidence": [valid, failed, unrelated],
        "planning_step_reports": [
            {"status": "blocked", "evidence_ids": ["ev_partial", "ev_failed"]},
        ],
    }
    assert _assessment_evidence(state, []) == [valid]


def test_finished_steps_with_goal_gap_replan_without_discarding_completed_work():
    async def scenario():
        incomplete_goal = _report("first", ["read"], last=True)
        args = incomplete_goal.tool_calls[0]["args"]
        args["goal_satisfied"] = False
        args["goal_checks"][0]["satisfied"] = False
        args["goal_missing_items"] = ["仍需交叉核验来源"]
        revised = _planner_call("replan", [_step("verify", "交叉核验", depends_on=["first"])])
        revised.tool_calls[0]["args"]["goal"] = "不能用这个较弱目标替换原目标"
        revised.tool_calls[0]["args"]["completion_criteria"] = ["不能降低原条件"]
        result, _, executor = await _run_extra(
            [
                _planner_call("plan", [_step("first", "取得数据")]),
                _named_tool_call("read", "search_source", {"source_id": "primary", "query": "数据"}),
                incomplete_goal,
                revised,
                _named_tool_call("verify", "search_source", {"source_id": "secondary", "query": "核验"}),
                _report("verify", last=True, source_ids=[2]),
                _structured_output_call(
                    "final", [{"kind": "fact", "content": "已交叉核验", "source_ids": [1, 2]}], profile="research"
                ),
            ]
        )
        assert result.status == "completed" and len(executor.calls) == 2
        plan = result.state["planning_plan"]
        assert plan["goal"] == "完成测试研究" and plan["completion_criteria"] == ["完成测试研究"]
        assert plan["revision"] == 2
        assert [s["step_id"] for s in plan["steps"]] == ["first", "verify"]
        assert all(s["status"] == "completed" for s in plan["steps"])
        assert [r["step_id"] for r in result.state["planning_step_reports"]] == ["first", "verify"]
        replans = [
            u
            for u in result.state["planning_updates"]
            if u.get("details", {}).get("planning_phase") == "replanned" and u["status"] == "completed"
        ]
        assert len(replans) == 1 and replans[0]["details"]["completed_step_ids"] == ["first"]

    asyncio.run(scenario())


def test_final_answer_failure_cannot_mark_planning_completed():
    async def scenario():
        events = SimpleNamespace(
            stage=lambda stage, status, summary, **kw: {
                "stage": stage,
                "status": status,
                "summary": summary,
                **kw,
            }
        )
        state = {
            "planning_enabled": True,
            "planning_status": "finalizing",
            "status": "partial",
            "error_code": "insufficient_evidence",
        }
        result = await PlanningCoordinatorMiddleware().aafter_agent(
            state, SimpleNamespace(context=SimpleNamespace(events=events))
        )
        assert result["planning_status"] == "partial"
        assert result["planning_updates"][-1]["status"] == "blocked"

    asyncio.run(scenario())


def test_registered_tool_outside_step_cannot_execute_or_request_approval():
    from tests.test_langgraph_approval import _side_effect_operation, _request_action

    async def scenario():
        result, _, executor = await _run_extra(
            [
                _planner_call("plan", [_step("first", "只读目标")]),
                _request_action(),
                _report("first", outcome="blocked"),
                _structured_output_call(
                    "final", [{"kind": "answer", "content": "未执行计划外操作。"}], profile="general"
                ),
            ],
            registry=_registry(_search_operation(), _side_effect_operation()),
        )
        assert executor.calls == []
        assert not result.interrupted
        assert result.status == "partial"

    asyncio.run(scenario())


def test_disallowed_tool_is_rejected_then_model_can_retry_with_allowed_step_tool():
    async def scenario():
        disallowed = replace(_search_operation(), name="search_stocks")
        result, model, executor = await _run_extra(
            [
                _planner_call("plan", [_step("first", "完成目标")]),
                _named_tool_call("wrong", "search_stocks", {"source_id": "primary", "query": "目标"}),
                _named_tool_call("correct", "search_source", {"source_id": "primary", "query": "目标"}),
                _report("first", ["correct"], last=True),
                _structured_output_call(
                    "final",
                    [{"kind": "fact", "content": "已基于观察完成目标。", "source_ids": [1]}],
                    profile="research",
                ),
            ],
            registry=_registry(_search_operation(), disallowed),
        )

        assert result.status == "completed"
        assert [call["tool_name"] for call in executor.calls] == ["search_source"]
        assert not model.responses
        retry_context = model.calls[2]
        assert any(
            isinstance(message, ToolMessage)
            and message.status == "error"
            and "不在当前计划步骤的允许范围内" in str(message.content)
            for message in retry_context
        )

    asyncio.run(scenario())


def test_planner_repairs_toolless_research_step_before_execution():
    async def scenario():
        result, model, executor = await _run_extra(
            [
                _planner_call("orphan-plan", [_model_step("orphan", "获取最新财务指标")]),
                _planner_call("repaired-plan", [_step("read", "获取最新财务指标")]),
                _named_tool_call("read", "search_source", {"source_id": "primary", "query": "财务指标"}),
                _report("read", ["read"], last=True),
                _structured_output_call(
                    "final",
                    [{"kind": "fact", "content": "已基于财务观察完成分析。", "source_ids": [1]}],
                    profile="research",
                ),
            ]
        )

        assert result.status == "completed"
        assert [call["tool_name"] for call in executor.calls] == ["search_source"]
        repair_messages = "\n".join(str(message.content) for message in model.calls[1])
        assert "step_kind=execute" in repair_messages
        assert not model.responses

    asyncio.run(scenario())


def test_invalid_plan_fails_closed_without_continuing_as_an_unplanned_agent():
    async def scenario():
        bad = {**_step("first", "目标"), "allowed_tools": ["unknown"]}
        result, model, executor = await _run_extra(
            [
                _planner_call("bad", [bad]),
                _planner_call("bad-retry", [bad]),
            ]
        )
        assert not executor.calls
        assert result.status == "partial" and result.error_code == "planning_generation_failed"
        assert result.state["planning_status"] == "blocked"
        assert result.state["response_repair_count"] == 0
        assert len(model.calls) == 2

    asyncio.run(scenario())


def test_planned_approval_resume_does_not_repeat_planning_or_execution():
    from tests.test_langgraph_approval import _side_effect_operation, _request_action, _resume

    async def scenario():
        manager = LangGraphRuntimeManager(
            registry=_registry(_side_effect_operation()), response_format=DEFAULT_RESPONSE_FORMAT
        )
        executor = FakeAtomicExecutor()
        await manager.start(testing=True)
        try:
            step = {**_step("first", "发送已授权测试通知"), "allowed_tools": ["send_message"]}
            result = await manager.run_new(
                messages=[{"role": "user", "content": "发送测试通知"}],
                user_text="发送测试通知",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="run-planning-approval",
                conversation_id="planning-approval",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                executor=executor,
                planning_mode="planned",
                agent_mode="plan",
                model=ScriptedChatModel(responses=[_planner_call("plan", [step]), _request_action()]),
            )
            assert result.interrupted and not executor.calls
            report = _report("first", last=True)
            report.tool_calls[0]["args"]["completed_summary"] = "通知执行器已返回成功回执。"
            resumed = await _resume(
                manager,
                pending=result.pending_interrupt,
                decision="approve",
                executor=executor,
                conversation_id="planning-approval",
                model=ScriptedChatModel(
                    responses=[
                        report,
                        _structured_output_call(
                            "final", [{"kind": "answer", "content": "通知已完成。"}], profile="general"
                        ),
                    ]
                ),
            )
            assert resumed.status == "completed"
            assert len(executor.calls) == 1 and executor.calls[0]["approved"] is True
            assert resumed.state["planning_revision"] == 1
            assert resumed.state["planning_status"] == "completed"
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_blocked_plan_publishes_one_partial_answer_not_success_then_duplicate(monkeypatch):
    from src.agent.langgraph_runtime.events import GraphEventBridge

    accepted = []
    original = GraphEventBridge.commit_model_answer

    def capture(self, answer, **kwargs):
        accepted.append(answer)
        return original(self, answer, **kwargs)

    monkeypatch.setattr(GraphEventBridge, "commit_model_answer", capture)

    async def scenario():
        bad = {**_step("first", "目标"), "allowed_tools": ["unknown"]}
        result, _, _ = await _run_extra(
            [
                _planner_call("bad", [bad]),
                _planner_call("bad-retry", [bad]),
                _structured_output_call("final", [{"kind": "answer", "content": "未能完成规划。"}], profile="general"),
            ]
        )
        assert result.status == "partial"
        assert len(accepted) == 1
        assert "未完成的核验" in accepted[0]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "steps",
    [
        [_step("same", "目标"), _step("same", "目标")],
        [_step("first", "目标", depends_on=["missing"])],
        [_step("first", "目标", depends_on=["second"]), _step("second", "目标", depends_on=["first"])],
        [{**_step("first", "目标"), "allowed_tools": ["invented_tool"]}],
        [{**_step("first", "目标"), "completion_criteria": []}],
        [_step(f"s{i}", "目标") for i in range(9)],
    ],
)
def test_invalid_plan_cannot_relax_dependencies_or_tool_authority(steps) -> None:
    with pytest.raises(ValueError):
        normalize_plan(
            {
                "plan_id": "p",
                "goal": "目标",
                "initial_state": "状态",
                "completion_criteria": ["完成目标"],
                "steps": steps,
            },
            known_tools=["search_source"],
        )


def test_model_only_plan_steps_require_a_dependency_path_to_tool_observations() -> None:
    common = {
        "goal": "基于取证结果完成分析",
        "initial_state": "尚无本轮工具观察",
        "completion_criteria": ["完成有证据支持的分析"],
    }
    with pytest.raises(ValueError, match="没有可追溯到工具步骤的依赖"):
        normalize_plan(
            {**common, "steps": [_model_step("orphan", "获取最新财务指标")]},
            known_tools=["search_source"],
        )

    normalized = normalize_plan(
        {
            **common,
            "steps": [
                _step("read", "读取最新财务指标"),
                _model_step("summarize", "基于财务指标整理观察", depends_on=["read"]),
                _model_step("conclude", "基于整理后的观察形成结论", depends_on=["summarize"]),
                _step("verify", "按前序结论补充核验", depends_on=["conclude"]),
            ],
        },
        known_tools=["search_source"],
    )

    assert normalized["steps"][1]["allowed_tools"] == []
    assert normalized["steps"][2]["allowed_tools"] == []
    assert normalized["steps"][3]["allowed_tools"] == ["search_source"]
    instruction = _contract_retry_instruction(
        PlanningPlan,
        {"message": "步骤 orphan 没有可追溯到工具步骤的依赖，无法产生新的可核验观察"},
    )
    assert "具体校验错误" in instruction
    assert "步骤 orphan" in instruction
    assert "只修正错误中指出的步骤" in instruction
    assert "要获取新数据，必须填写注册目录中的精确工具名" in instruction
    assert "depends_on" in instruction


def test_leading_model_only_preparation_is_folded_before_first_tool_step() -> None:
    normalized = normalize_plan(
        {
            "goal": "基于报告检索结果完成分析",
            "initial_state": "尚无本轮工具观察",
            "completion_criteria": ["结论有报告证据支持"],
            "steps": [
                _model_step("prepare", "先整理检索方向"),
                _step("search", "检索报告中的经营数据", depends_on=["prepare"]),
            ],
        },
        known_tools=["search_source"],
    )

    assert [step["step_id"] for step in normalized["steps"]] == ["search"]
    assert normalized["steps"][0]["depends_on"] == []
    assert normalized["completion_criteria"] == [
        "结论有报告证据支持",
        "返回基于既有观察的步骤摘要",
    ]


def test_intermediate_model_only_step_is_ordered_before_dependent_tool_step() -> None:
    normalized = normalize_plan(
        {
            "goal": "基于取证结果完成分析",
            "initial_state": "尚无本轮工具观察",
            "completion_criteria": ["完成有证据支持的分析"],
            "steps": [
                _step("identity", "核验公司身份"),
                _model_step("analysis", "基于已取证信息判断运营缺口"),
                _step("financials", "按分析结果补充获取核心财务指标", depends_on=["analysis"]),
            ],
        },
        known_tools=["search_source"],
    )

    analysis = normalized["steps"][1]
    assert analysis["allowed_tools"] == []
    assert analysis["depends_on"] == ["identity"]


def test_trailing_model_only_synthesis_steps_fold_into_goal_checks() -> None:
    normalized = normalize_plan(
        {
            "goal": "基于报告检索结果总结经营与风险",
            "initial_state": "尚无本轮观察",
            "completion_criteria": ["回答覆盖经营情况与风险"],
            "steps": [
                _step("search", "检索报告中的经营与风险信息"),
                _model_step("summarize-operations", "总结公司的经营表现", depends_on=["search"]),
                _model_step("summarize-risks", "总结公司的风险因素", depends_on=["summarize-operations"]),
            ],
        },
        known_tools=["search_source"],
    )

    assert [step["step_id"] for step in normalized["steps"]] == ["search"]
    assert normalized["completion_criteria"] == [
        "回答覆盖经营情况与风险",
        "返回基于既有观察的步骤摘要",
    ]


def test_planning_step_accepts_provider_id_alias() -> None:
    step = PlanningStep.model_validate(
        {
            "id": "step_1",
            "step_kind": "execute",
            "objective": "核验证券身份",
            "expected_observation": "获得证券身份观察",
            "allowed_tools": ["search_source"],
        }
    )

    assert step.step_id == "step_1"
    normalized = normalize_plan(
        {
            "goal": "目标",
            "initial_state": "初始状态",
            "completion_criteria": ["完成目标"],
            "steps": [
                {
                    "id": "step_1",
                    "objective": "核验证券身份",
                    "expected_observation": "获得证券身份观察",
                    "completion_criteria": ["确认身份"],
                    "allowed_tools": ["search_source"],
                }
            ],
        },
        known_tools=["search_source"],
    )
    assert normalized["steps"][0]["step_id"] == "step_1"

    step_alias = PlanningStep.model_validate(
        {
            "step": "step_2",
            "step_kind": "analyze",
            "objective": "补充核验",
            "expected_observation": "获得补充观察",
            "allowed_tools": [],
        }
    )
    assert step_alias.step_id == "step_2"


def test_planned_run_exposes_plan_step_goal_check_and_finalization_in_order() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(
            responses=[
                _planner_call(
                    "planner-call",
                    [_step("step_1", "取得第一组观察"), _step("step_2", "取得第二组观察", depends_on=["step_1"])],
                    plan_summary="先取得第一组观察，再根据结果补充第二组观察。",
                ),
                _named_tool_call(
                    "step-1-call",
                    "search_source",
                    {"source_id": "primary", "query": "第一步"},
                ),
                _report("step_1", ["step-1-call"]),
                _named_tool_call(
                    "step-2-call",
                    "search_source",
                    {"source_id": "secondary", "query": "第二步"},
                ),
                _report("step_2", last=True, source_ids=[2]),
                _structured_output_call(
                    "final-call",
                    [{"kind": "fact", "content": "测试结论", "source_ids": [1]}],
                    profile="research",
                ),
            ]
        )
        executor = FakeAtomicExecutor()
        broadcaster = RunBroadcaster(run_id="run-planned-test")
        manager = LangGraphRuntimeManager(
            registry=_registry(_search_operation()),
            response_format=DEFAULT_RESPONSE_FORMAT,
        )
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "测试问题"}],
                user_text="测试问题",
                system_prompt="",
                llm_config={},
                database=None,
                controller=broadcaster,
                run_id="run-planned-test",
                conversation_id="planned-test",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=model,
                executor=executor,
                planning_mode="planned",
                agent_mode="plan",
            )
        finally:
            await manager.close()

        assert result.status == "completed"
        assert result.error_code is None
        assert [call["action_id"] for call in executor.calls] == ["step-1-call", "step-2-call"]
        assert result.state["planning_status"] == "completed"
        assert result.state["planning_plan"]["plan_summary"] == "先取得第一组观察，再根据结果补充第二组观察。"
        assert result.state["planning_plan"]["steps"][0]["status"] == "completed"
        assert result.state["planning_plan"]["steps"][1]["status"] == "completed"
        final_tool_names = [
            str(
                tool.get("function", {}).get("name") or tool.get("name")
                if isinstance(tool, Mapping)
                else getattr(tool, "name", "")
            )
            for tool in model.call_options[-1].get("tools", [])
        ]
        assert STRUCTURED_OUTPUT_TOOL_NAME in final_tool_names
        assert "search_source" not in final_tool_names
        planning_events = [event for event in result.stage_history or [] if event.get("stage") == "planning"]
        assert not planning_events[0].get("details", {}).get("user_message")
        phases = [event.get("details", {}).get("planning_phase") for event in planning_events]
        assert phases == [
            "plan_created",
            "plan_created",
            "step_started",
            "step_completed",
            "goal_checked",
            "step_started",
            "step_completed",
            "goal_checked",
            "finalizing",
            "finalizing",
        ]
        assert not any(
            "先取得第一组观察，再根据结果补充第二组观察。" in str(part.get("text") or "")
            for part in broadcaster.display_parts_snapshot()
        )
        assert any(
            part.get("display_kind") == "answer" and "测试结论" in str(part.get("text") or "")
            for part in broadcaster.display_parts_snapshot()
        )
        assert all(
            "progress_text" not in event.get("details", {})
            for event in planning_events
            if event.get("details", {}).get("planning_phase") in {"plan_created", "replanned"}
        )
        assert result.state["planning_updates"][-1]["details"]["planning_phase"] == "finalizing"
        assert not any(
            event.get("details", {}).get("user_message")
            in {
                "前面的证据已经收集完成，我正在复核结论是否都能被现有来源支持。",
                "来源核对完成，接下来整理最终回答。",
                "语义复核通过，现有证据足以支持这份回答。",
            }
            for event in result.stage_history or []
        )

    asyncio.run(scenario())


def test_successful_plan_close_does_not_emit_progress_after_answer(monkeypatch) -> None:
    published: list[str] = []
    stages: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "src.agent.langgraph_runtime.planning._publish_user_progress",
        lambda _context, message: published.append(str(message)),
    )
    context = SimpleNamespace(
        events=SimpleNamespace(stage=lambda *args, **kwargs: stages.append({"args": args, **kwargs}) or {})
    )
    state = {
        "planning_enabled": True,
        "planning_status": "finalizing",
        "planning_plan": {"plan_id": "plan-test"},
        "planning_revision": 1,
        "planning_updates": [],
        "status": "completed",
        "error_code": None,
    }

    update = asyncio.run(
        PlanningCoordinatorMiddleware().aafter_agent(state, SimpleNamespace(context=context))
    )

    assert update["planning_status"] == "completed"
    assert stages[0]["details"]["planning_phase"] == "finalizing"
    assert published == []


def test_model_progress_projection_preserves_text_beyond_1800_characters() -> None:
    from src.agent.langgraph_runtime.events import GraphEventBridge

    broadcaster = RunBroadcaster(run_id="long-progress")
    events = GraphEventBridge(broadcaster, run_id="long-progress")
    context = SimpleNamespace(events=events)
    progress_text = "已核验本轮观察并确认，" * 250 + "后续结论将以本轮已获得的证据为准。"
    report = PlanningStepReport(
        step_id="long-progress",
        outcome="completed",
        completed_summary=progress_text,
        criteria_checks=[{"criterion_index": 1, "satisfied": True, "explanation": "符合。"}],
        expected_observation_met=True,
        remaining_plan_valid=True,
        goal_satisfied=True,
        progress_text=progress_text,
    )

    assert len(progress_text) > 1_800
    assert report.completed_summary == progress_text
    assert report.progress_text == progress_text
    _publish_model_progress(context, report.progress_text)

    projected_text = f"{progress_text}\n\n"
    assert broadcaster.assistant_text_snapshot == projected_text
    display_parts = broadcaster.display_parts_snapshot()
    assert "".join(part["text"] for part in display_parts if part.get("type") == "text") == projected_text

    plan = PlanningPlan(
        plan_id="long-summary",
        goal="完成测试研究",
        initial_state="没有已取得观察",
        plan_summary=progress_text,
        completion_criteria=["完成测试研究"],
        steps=[_step("step-1", "核验目标")],
    )
    trace = planning_trace({"planning_enabled": True, "planning_plan": plan.model_dump(mode="json")})
    assert trace is not None
    assert trace["plan"]["plan_summary"] == progress_text


def test_satisfied_goal_skips_remaining_read_only_plan_steps() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(
            responses=[
                _planner_call(
                    "planner-call",
                    [
                        _step("first", "检索目标事实"),
                        _step("optional-followup", "仅在事实缺失时补查", depends_on=["first"]),
                    ],
                ),
                _named_tool_call(
                    "first-search",
                    "search_source",
                    {"source_id": "primary", "query": "目标事实"},
                ),
                _report("first", ["first-search"], last=True),
                _structured_output_call(
                    "final-call",
                    [{"kind": "fact", "content": "目标事实已完成核验。", "source_ids": [1]}],
                    profile="research",
                ),
            ]
        )
        executor = FakeAtomicExecutor()
        manager = LangGraphRuntimeManager(
            registry=_registry(_search_operation()),
            response_format=DEFAULT_RESPONSE_FORMAT,
        )
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "核验目标事实"}],
                user_text="核验目标事实",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="run-satisfied-goal-skips-read-only-tail",
                conversation_id="satisfied-goal-skips-read-only-tail",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=model,
                executor=executor,
                planning_mode="planned",
                agent_mode="plan",
            )
        finally:
            await manager.close()

        assert result.status == "completed"
        assert [call["action_id"] for call in executor.calls] == ["first-search"]
        assert [step["step_id"] for step in result.state["planning_plan"]["steps"]] == ["first"]
        goal_event = next(
            event
            for event in result.state["planning_updates"]
            if event.get("planning_phase") == "goal_checked" and event.get("status") == "completed"
        )
        assert goal_event["details"]["skipped_step_ids"] == ["optional-followup"]
        assert len(model.calls) == 4
        assessor_instructions = [
            str(message.content)
            for call in model.calls
            for message in call
            if "你是当前计划步骤的执行报告者和目标检查者" in str(message.content)
        ]
        assert assessor_instructions
        assert all("不得因为这些呈现要求尚未显示" in prompt for prompt in assessor_instructions)

    asyncio.run(scenario())


def test_failed_step_replans_remaining_work_and_preserves_partial_observation() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(
            responses=[
                _planner_call("planner-call", [_step("step_1", "取得主来源")]),
                _named_tool_call(
                    "failed-call",
                    "search_source",
                    {"source_id": "primary", "query": "主来源"},
                ),
                _report("step_1", outcome="replan"),
                _planner_call(
                    "replan-call",
                    [_step("replacement", "取得备用来源")],
                    revision=2,
                    plan_summary="主来源未返回有效观察，改用备用来源补齐这一步。",
                ),
                _named_tool_call(
                    "success-call",
                    "search_source",
                    {"source_id": "secondary", "query": "备用来源"},
                ),
                _report("replacement", ["success-call"], last=True),
                _structured_output_call(
                    "final-call",
                    [{"kind": "fact", "content": "备用来源结论", "source_ids": [1]}],
                    profile="research",
                ),
            ]
        )
        executor = FakeAtomicExecutor(
            {"search_source": [{"success": False, "error_code": "provider_unavailable"}, {"success": True}]}
        )
        broadcaster = RunBroadcaster(run_id="replan-test")
        manager = LangGraphRuntimeManager(
            registry=_registry(_search_operation()),
            response_format=DEFAULT_RESPONSE_FORMAT,
        )
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "测试问题"}],
                user_text="测试问题",
                system_prompt="",
                llm_config={},
                database=None,
                controller=broadcaster,
                run_id="run-replan-test",
                conversation_id="replan-test",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=model,
                executor=executor,
                planning_mode="planned",
                agent_mode="plan",
            )
        finally:
            await manager.close()

        assert result.status == "completed"
        assert result.state["planning_replan_count"] == 1
        assert result.state["planning_plan"]["revision"] == 2
        assert result.state["planning_plan"]["plan_summary"] == "主来源未返回有效观察，改用备用来源补齐这一步。"
        assert result.state["planning_plan"]["steps"][0]["step_id"] == "replacement"
        assert [report["status"] for report in result.state["planning_step_reports"]] == [
            "blocked",
            "completed",
        ]
        planning_events = [event for event in result.stage_history or [] if event.get("stage") == "planning"]
        replanned_index = next(
            index
            for index, event in enumerate(planning_events)
            if event.get("details", {}).get("planning_phase") == "replanned" and event.get("status") == "completed"
        )
        replacement_started_index = next(
            index
            for index, event in enumerate(planning_events)
            if event.get("details", {}).get("planning_phase") == "step_started"
            and event.get("details", {}).get("step_id") == "replacement"
        )
        assert replanned_index < replacement_started_index
        assert "progress_text" not in planning_events[replanned_index].get("details", {})
        assert not any(
            "主来源未返回有效观察，改用备用来源补齐这一步。" in str(part.get("text") or "")
            for part in broadcaster.display_parts_snapshot()
        )

    asyncio.run(scenario())


def test_trailing_synthesis_step_is_folded_into_the_final_answer() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(
            responses=[
                _planner_call(
                    "planner-call",
                    [
                        _step("step_1", "取得行情观察"),
                        _model_step("step_2", "基于行情观察形成结论", depends_on=["step_1"]),
                    ],
                ),
                _named_tool_call(
                    "quote-call",
                    "search_source",
                    {"source_id": "primary", "query": "行情"},
                ),
                _report("step_1", ["quote-call"], last=True, goal_criterion_count=2),
                _structured_output_call(
                    "final-call",
                    [{"kind": "fact", "content": "测试结论", "source_ids": [1]}],
                    profile="research",
                ),
            ]
        )
        executor = FakeAtomicExecutor()
        manager = LangGraphRuntimeManager(
            registry=_registry(_search_operation()),
            response_format=DEFAULT_RESPONSE_FORMAT,
        )
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "测试问题"}],
                user_text="测试问题",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="run-model-only-step",
                conversation_id="model-only-step-test",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=model,
                executor=executor,
                planning_mode="planned",
                agent_mode="plan",
            )
        finally:
            await manager.close()

        assert result.status == "completed"
        assert [call["action_id"] for call in executor.calls] == ["quote-call"]
        assert [step["step_id"] for step in result.state["planning_plan"]["steps"]] == ["step_1"]
        assert result.state["planning_plan"]["completion_criteria"] == [
            "完成测试研究",
            "返回基于既有观察的步骤摘要",
        ]
        assert result.state["planning_step_reports"][-1]["model_only"] is False
        assert result.state["planning_step_reports"][-1]["observed_tool_count"] == 1
        assert len(model.calls) == 4

    asyncio.run(scenario())


def test_planning_does_not_publish_a_success_when_a_step_returns_no_observation() -> None:
    async def scenario() -> None:
        model = ScriptedChatModel(
            responses=[
                _planner_call("planner-call", [_step("step_1", "必须取得观察")]),
                AIMessage(content="我还没有取得可核验的观察。"),
                AIMessage(content="仍没有观察。"),
                AIMessage(content="无法取得观察。"),
                _structured_output_call(
                    "partial-final",
                    [{"kind": "answer", "content": "当前只能说明取证未完成。"}],
                    profile="general",
                ),
            ]
        )
        manager = LangGraphRuntimeManager(
            registry=_registry(_search_operation()),
            response_format=DEFAULT_RESPONSE_FORMAT,
        )
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "测试问题"}],
                user_text="测试问题",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="run-planning-blocked",
                conversation_id="planning-blocked-test",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=model,
                planning_mode="planned",
                agent_mode="plan",
            )
        finally:
            await manager.close()

        assert result.status == "partial"
        assert result.error_code == "planning_incomplete"
        assert result.state["planning_status"] == "blocked"
        assert "取证未完成" in result.final_text
        assert any(
            event.get("details", {}).get("planning_phase") == "goal_checked" and event.get("status") == "blocked"
            for event in result.stage_history or []
            if event.get("stage") == "planning"
        )

    asyncio.run(scenario())
