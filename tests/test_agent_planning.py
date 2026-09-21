"""Focused checks for the bounded Planning coordinator."""

from __future__ import annotations

import asyncio

import pytest
from typing import Any, Mapping

from langchain_core.messages import AIMessage
from langchain_core.messages import HumanMessage, ToolMessage
from types import SimpleNamespace

from src.agent.langgraph_runtime.graph import DEFAULT_RESPONSE_FORMAT
from src.agent.langgraph_runtime.planning import (
    PlanningStep,
    PlanningStepReport,
    PlanningCoordinatorMiddleware,
    normalize_plan,
    planning_model_messages,
    _contract_retry_instruction,
    _validate_assessment,
    _assessment_evidence,
    resolve_planning_mode,
)
from src.agent.langgraph_runtime.runtime import LangGraphRuntimeManager
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
        "depends_on": depends_on or [],
        "objective": objective,
        "expected_observation": "获得可核验的真实工具观察",
        "allowed_tools": ["search_source"],
        "completion_criteria": ["工具调用成功"],
    }


def _model_step(step_id: str, objective: str, *, depends_on: list[str] | None = None) -> dict[str, Any]:
    return {
        "step_id": step_id,
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


def _report(step_id, call_ids=(), *, last=False, outcome="completed", model_only=False, source_ids=None):
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
                [{"criterion_index": 1, "satisfied": met, "explanation": summary, "source_ids": ids}] if last else []
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


async def _run_extra(responses, *, mode="planned", user_text="测试问题", registry=None):
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
            agent_mode="plan" if mode == "planned" else "direct" if mode == "direct" else "auto",
        )
        return result, model, executor
    finally:
        await manager.close()


@pytest.mark.parametrize("mode,text", [("planned", "未来产业怎么选？"), ("direct", "解释研究报告这个词")])
def test_auto_uses_semantic_decision_not_old_trigger(mode, text):
    async def scenario():
        plan = _planner_call("plan", [_step("first", "目标")]).tool_calls[0]["args"]
        responses = [_route_call("route", mode)]
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
        result, model, executor = await _run_extra(responses, mode="auto", user_text=text)
        assert result.status == "completed"
        assert result.state["planning_mode"] == mode
        assert result.state["planning_decision"]["reason"] == "依据任务语义和工具需求"
        assert text in model.calls[0][-1].content and "description" in model.calls[0][-1].content
        assert len(executor.calls) == (1 if mode == "planned" else 0)

    asyncio.run(scenario())


def test_planner_failure_does_not_fall_back_to_unplanned_execution():
    async def scenario():
        bad = {**_step("first", "目标"), "allowed_tools": ["invented_tool"]}
        result, model, executor = await _run_extra(
            [
                _planner_call("bad1", [bad]),
                _planner_call("bad2", [bad]),
                _structured_output_call("final", [{"kind": "answer", "content": "计划无法执行。"}], profile="general"),
            ]
        )
        assert result.status == "partial" and result.error_code == "planning_incomplete"
        assert result.state["planning_status"] == "blocked" and executor.calls == []
        assert "unregistered tools" in model.calls[1][-1].content

    asyncio.run(scenario())


def test_route_without_structured_tool_call_is_diagnosed_but_answer_can_continue():
    async def scenario():
        result, model, executor = await _run_extra(
            [
                AIMessage(content="我先分析一下。"),
                AIMessage(content="目前还不能确定。"),
                _structured_output_call(
                    "final",
                    [{"kind": "answer", "content": "规划路由未成功，但仍返回当前可用结论。"}],
                    profile="general",
                ),
            ],
            mode="auto",
        )
        assert result.status == "partial"
        assert result.state["planning_status"] == "blocked"
        assert result.state["planning_model_call_count"] == 2
        assert "structured_output_missing_tool_call" in result.state["planning_error"]
        assert executor.calls == []
        assert "规划路由未成功" in result.state["answer_final"]
        assert len(model.calls) == 3

    asyncio.run(scenario())


def test_successful_tool_with_unmet_criteria_continues_the_same_step():
    async def scenario():
        result, _, executor = await _run_extra(
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


def test_hallucinated_operation_after_blocking_enters_bounded_answer_repair():
    async def scenario():
        bad = {**_step("first", "目标"), "allowed_tools": ["unknown"]}
        result, _, executor = await _run_extra(
            [
                _planner_call("bad", [bad]),
                _planner_call("bad-retry", [bad]),
                _named_tool_call("hallucinated", "search_source", {"source_id": "primary", "query": "越界"}),
                _structured_output_call(
                    "final", [{"kind": "answer", "content": "规划受阻，未执行查询。"}], profile="general"
                ),
            ]
        )
        assert not executor.calls
        assert result.status == "partial" and result.error_code == "planning_incomplete"
        assert result.state["response_repair_count"] == 1
        assert "规划受阻" in result.state["answer_final"]

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


def test_planning_step_accepts_provider_id_alias() -> None:
    step = PlanningStep.model_validate(
        {
            "id": "step_1",
            "objective": "核验证券身份",
            "expected_observation": "获得证券身份观察",
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
                }
            ],
        }
    )
    assert normalized["steps"][0]["step_id"] == "step_1"

    step_alias = PlanningStep.model_validate(
        {
            "step": "step_2",
            "objective": "补充核验",
            "expected_observation": "获得补充观察",
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
        planning_events = [event for event in result.stage_history or [] if event.get("stage") == "planning"]
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
        assert planning_events[1]["details"]["progress_text"] == "先取得第一组观察，再根据结果补充第二组观察。"
        assert result.state["planning_updates"][-1]["details"]["planning_phase"] == "finalizing"

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
        assert planning_events[replanned_index]["details"]["progress_text"] == (
            "主来源未返回有效观察，改用备用来源补齐这一步。"
        )

    asyncio.run(scenario())


def test_model_only_step_uses_existing_observations_without_repeating_a_tool() -> None:
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
                _report("step_1", ["quote-call"]),
                _report("step_2", ["quote-call"], last=True, model_only=True),
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
        assert result.state["planning_plan"]["steps"][1]["allowed_tools"] == []
        assert result.state["planning_step_reports"][-1]["model_only"] is True
        assert result.state["planning_step_reports"][-1]["observed_tool_count"] == 0
        assert "纯分析步骤" in model.calls[3][0].content
        assert (
            result.state["planning_step_reports"][-1]["completed_summary"]
            == "来源和时间口径已经确认，可以据此继续分析。"
        )

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
