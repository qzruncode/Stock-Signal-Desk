# -*- coding: utf-8 -*-
"""Focused contracts for the independent Goal product mode."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver

from src.agent.langgraph_runtime.events import GraphEventBridge
from src.agent.langgraph_runtime.goal.contracts import (
    GoalAction,
    GoalAssessment,
    GoalContract,
    GoalCriterion,
    GoalFinalAnswer,
)
from src.agent.langgraph_runtime.goal.graph import (
    _apply_assessment,
    _clarification_question,
    _contract_needs_confirmation,
    _goal_action_select,
    _goal_execute,
    _structured_call,
    build_goal_graph,
    goal_runtime_limits,
    goal_turn_defaults,
)
from src.agent.langgraph_runtime.goal.state import GoalState
from src.agent.langgraph_runtime.goal.trace import goal_trace
from src.agent.langgraph_runtime.mode_dispatch import normalize_product_mode
from src.agent.langgraph_runtime.mode_dispatch import ProductModeRoute, resolve_product_mode
from src.agent.langgraph_runtime.runtime import LangGraphRuntimeManager
from tests.test_langgraph_agent_runtime import FakeAtomicExecutor, _registry, _search_operation


class _SequenceRunnable:
    def __init__(self, responses):
        self.responses = responses

    async def ainvoke(self, _messages):
        if not self.responses:
            raise AssertionError("Goal model requested an unscripted response")
        return {"parsed": self.responses.pop(0), "raw": None, "parsing_error": None}


class _SequenceStructuredModel:
    def __init__(self, responses):
        self.responses = list(responses)

    def with_structured_output(self, _schema, *, include_raw=True):
        assert include_raw is True
        return _SequenceRunnable(self.responses)


class _Events:
    def __init__(self):
        self.stage_history = []
        self.projections = []

    def stage(self, *_args, **_kwargs):
        return None

    def progress(self, *_args, **_kwargs):
        return None

    def publish_model_projection(self, text, **kwargs):
        self.projections.append({"text": text, **kwargs})


def _goal_state(*, evidence=None, status="pending"):
    defaults = goal_turn_defaults(tool_call_limit=5)
    # LangGraph resolves Overwrite reducers before nodes receive state.  The
    # unit helper supplies the equivalent materialized values directly.
    defaults["goal_evidence_ids"] = []
    defaults["goal_history"] = []
    defaults["tool_call_count"] = 0
    defaults["model_turn_count"] = 0
    return {
        **defaults,
        "run_id": "run-goal-test",
        "agent_mode": "goal",
        "resolved_agent_mode": "goal",
        "goal_status": status,
        "goal_criteria": [
            {
                "criterion_id": "criterion-1",
                "description": "工具结果必须证明目标已完成",
                "required": True,
                "verification_method": "tool_result",
                "status": "pending",
                "evidence_ids": [],
                "explanation": "",
            }
        ],
        "evidence": list(evidence or []),
    }


def test_goal_projects_the_model_progress_text_without_stage_copy() -> None:
    async def scenario() -> None:
        class Controller:
            def __init__(self):
                self.data = []

            def add_data(self, value):
                self.data.append(value)

        controller = Controller()
        events = GraphEventBridge(controller, run_id="run-goal-projection")
        model = _SequenceStructuredModel(
            [
                GoalAssessment(
                    status="continue",
                    progress_text="工具结果只覆盖了第一个条件，我会继续补齐第二个条件的证据。",
                )
            ]
        )
        context = type("Context", (), {"model": model, "events": events})()

        result = await _structured_call(
            context,
            GoalAssessment,
            [],
            projection_phase="goal_monitor",
            projection_kind="GoalAssessment",
            projection_id="run-goal-projection:goal:monitor:1",
        )

        assert result.progress_text == "工具结果只覆盖了第一个条件，我会继续补齐第二个条件的证据。"
        part = controller.data[0]["part"]
        assert part["name"] == "agent-model-projection"
        assert part["data"]["text"] == result.progress_text
        assert part["data"]["scope"] == "goal"
        assert part["data"]["phase"] == "goal_monitor"
        assert part["data"]["projection_id"] == "run-goal-projection:goal:monitor:1"
        assert part["data"]["occurred_at"]

    asyncio.run(scenario())


def test_goal_is_a_fifth_product_mode_with_its_own_checkpoint_namespace() -> None:
    assert normalize_product_mode("goal") == "goal"
    assert LangGraphRuntimeManager.goal_thread_id("conversation-1") == "goal-v1:conversation-1"
    assert LangGraphRuntimeManager.thread_id("conversation-1") == "agent-v2:conversation-1"


def test_goal_graph_has_only_goal_nodes_and_no_plan_or_team_state_channels() -> None:
    graph = build_goal_graph(checkpointer=InMemorySaver())
    assert set(graph.get_graph().nodes) >= {
        "__start__",
        "goal_intake",
        "goal_confirm",
        "goal_action_select",
        "goal_execute",
        "goal_observe",
        "goal_monitor",
        "goal_finalize",
        "__end__",
    }
    annotations = set(GoalState.__annotations__)
    assert not any(name.startswith("planning_") for name in annotations)
    assert not any(name.startswith("team_") for name in annotations)


def test_goal_model_self_assessment_without_eligible_evidence_cannot_complete() -> None:
    state = _goal_state(
        evidence=[
            {
                "evidence_id": "real-evidence",
                "success": True,
                "has_data": True,
                "usable": True,
                "evidence_eligible": True,
            }
        ]
    )
    assessment = GoalAssessment(
        status="completed",
        progress_text="模型认为完成",
        criteria=[
            {
                "criterion_id": "criterion-1",
                "status": "satisfied",
                "evidence_ids": ["model-invented-evidence"],
                "explanation": "模型自评完成",
            }
        ],
    )

    update = _apply_assessment(state, assessment)

    assert update["goal_status"] == "running"
    assert update["status"] == "running"
    assert update["goal_criteria"][0]["status"] == "pending"
    assert update["goal_criteria"][0]["evidence_ids"] == []


def test_goal_completes_only_when_required_condition_references_eligible_evidence() -> None:
    state = _goal_state(
        evidence=[
            {
                "evidence_id": "real-evidence",
                "success": True,
                "has_data": True,
                "usable": True,
                "evidence_eligible": True,
            }
        ]
    )
    state["goal_criteria"][0]["evidence_ids"] = ["real-evidence"]
    assessment = GoalAssessment(
        status="completed",
        progress_text="已完成",
        criteria=[
            {
                "criterion_id": "criterion-1",
                "status": "satisfied",
                "evidence_ids": ["real-evidence"],
                "explanation": "工具结果证明完成",
            }
        ],
    )

    update = _apply_assessment(state, assessment)

    assert update["goal_status"] == "completed"
    assert update["status"] == "completed"
    assert update["goal_criteria"][0]["status"] == "satisfied"
    assert update["goal_criteria"][0]["evidence_ids"] == ["real-evidence"]


def test_unlinked_eligible_evidence_cannot_satisfy_a_criterion() -> None:
    state = _goal_state(
        evidence=[
            {
                "evidence_id": "unrelated-evidence",
                "success": True,
                "has_data": True,
                "usable": True,
                "evidence_eligible": True,
            }
        ]
    )
    assessment = GoalAssessment(
        status="completed",
        criteria=[
            {
                "criterion_id": "criterion-1",
                "status": "satisfied",
                "evidence_ids": ["unrelated-evidence"],
                "explanation": "模型引用了一条并未关联到此条件的证据",
            }
        ],
    )

    update = _apply_assessment(state, assessment)

    assert update["goal_status"] == "running"
    assert update["goal_criteria"][0]["status"] == "pending"
    assert update["goal_criteria"][0]["evidence_ids"] == []


def test_user_confirmation_criterion_cannot_be_satisfied_by_model_assessment() -> None:
    state = _goal_state(
        evidence=[
            {
                "evidence_id": "tool-evidence",
                "success": True,
                "has_data": True,
                "usable": True,
                "evidence_eligible": True,
            }
        ]
    )
    state["goal_criteria"][0]["verification_method"] = "user_confirmation"
    state["goal_criteria"][0]["evidence_ids"] = ["tool-evidence"]
    assessment = GoalAssessment(
        status="completed",
        criteria=[
            {
                "criterion_id": "criterion-1",
                "status": "satisfied",
                "evidence_ids": ["tool-evidence"],
            }
        ],
    )

    update = _apply_assessment(state, assessment)

    assert update["goal_status"] == "waiting_for_user"
    assert update["goal_pending_confirmation_criteria"] == ["criterion-1"]
    assert update["goal_criteria"][0]["status"] == "pending"
    assert update["goal_criteria"][0]["evidence_ids"] == []


def test_goal_trace_is_bounded_and_user_verifiable() -> None:
    state = {
        **_goal_state(status="blocked"),
        "goal_contract": {
            "schema_version": "goal.v1",
            "objective": "完成一个可验证目标",
            "scope": "当前会话",
            "revision": 2,
        },
        "goal_progress": "已停止",
        "goal_blocker": "达到迭代上限",
        "goal_terminal_reason": "预算耗尽",
        "goal_action": {
            "action_id": "action-1",
            "kind": "tool",
            "tool_name": "read_source",
            "arguments": {"secret": "should-not-be-projected"},
            "criterion_ids": ["criterion-1"],
            "rationale": "收集可验证证据",
        },
    }

    trace = goal_trace(state)

    assert trace is not None
    assert trace["schema_version"] == "goal.v1"
    assert trace["status"] == "blocked"
    assert trace["current_action"] is None
    assert trace["last_action"]["tool_name"] == "read_source"
    assert "arguments" not in trace["last_action"]
    assert trace["blocker"] == "达到迭代上限"


def test_goal_action_schema_failure_is_repaired_before_progress_or_execution() -> None:
    async def scenario() -> None:
        events = _Events()
        model = _SequenceStructuredModel([
            GoalAction(
                kind="tool", action_id="bad-action", tool_name="search_source",
                arguments={"query": "公开记录"},
                criterion_ids=["criterion-1"],
                progress_text="我已经开始查公开记录了。",
            ),
            GoalAction(
                kind="tool", action_id="good-action", tool_name="search_source",
                arguments={"source_id": "primary", "query": "公开记录"},
                criterion_ids=["criterion-1"],
                progress_text="我先从原始发布渠道核对这条公开记录。",
            ),
        ])
        context = type("Context", (), {
            "model": model,
            "events": events,
            "registry": _registry(_search_operation()),
            "catalog": type("Catalog", (), {"compact_catalog": lambda _self: []})(),
            "run_id": "goal-action-repair",
            "conversation_id": "goal-action-repair",
        })()

        update = await _goal_action_select(
            _goal_state(),
            type("Runtime", (), {"context": context})(),
        )

        assert update["goal_action"]["action_id"] == "good-action"
        assert update["goal_action"]["arguments"] == {"source_id": "primary", "query": "公开记录"}
        assert update["goal_action_status"] == "selected"
        assert update["model_turn_count"] == 2
        assert update["goal_action_validation_repairs"] == 1
        assert [projection["text"] for projection in events.projections] == [
            "我先从原始发布渠道核对这条公开记录。"
        ]

    asyncio.run(scenario())


def test_goal_action_validation_exhaustion_is_a_terminal_runtime_error() -> None:
    async def scenario() -> None:
        events = _Events()
        model = _SequenceStructuredModel([
            GoalAction(kind="tool", action_id="unknown-one", tool_name="eastmoney_search", criterion_ids=["criterion-1"]),
            GoalAction(kind="tool", action_id="unknown-two", tool_name="eastmoney_search", criterion_ids=["criterion-1"]),
        ])
        context = type("Context", (), {
            "model": model,
            "events": events,
            "registry": _registry(_search_operation()),
            "catalog": type("Catalog", (), {"compact_catalog": lambda _self: []})(),
            "run_id": "goal-action-invalid",
            "conversation_id": "goal-action-invalid",
        })()

        update = await _goal_action_select(
            _goal_state(),
            type("Runtime", (), {"context": context})(),
        )

        assert update["goal_status"] == "failed"
        assert update["goal_action_status"] == "failed"
        assert update["runtime_errors"][0]["failure_kind"] == "tool_validation"
        assert update["runtime_errors"][0]["tool_name"] == "eastmoney_search"
        assert "eastmoney_search" not in [item["text"] for item in events.projections]

    asyncio.run(scenario())


def test_goal_execute_revalidates_checkpointed_tool_action_before_dispatch() -> None:
    async def scenario() -> None:
        events = _Events()
        executor = FakeAtomicExecutor()
        context = type("Context", (), {
            "events": events,
            "registry": _registry(_search_operation()),
            "executor": executor,
            "run_id": "goal-checkpoint-invalid",
            "conversation_id": "goal-checkpoint-invalid",
        })()
        state = {
            **_goal_state(),
            "goal_action": {
                "kind": "tool", "action_id": "checkpoint-action",
                "tool_name": "search_source", "arguments": {"query": "公开记录"},
                "criterion_ids": ["criterion-1"],
            },
        }

        update = await _goal_execute(state, type("Runtime", (), {"context": context})())

        assert update["goal_status"] == "failed"
        assert update["goal_action_status"] == "failed"
        assert update["runtime_errors"][0]["error_code"] == "goal_action_invalid"
        assert executor.calls == []

    asyncio.run(scenario())


def test_goal_limits_are_server_owned_and_not_fixed_to_pdf_example() -> None:
    limits = goal_runtime_limits(tool_call_limit=7)
    defaults = goal_turn_defaults(tool_call_limit=7)

    assert limits["goal_iteration_limit"] >= 1
    assert limits["goal_tool_call_limit"] == 7
    assert defaults["goal_iteration_limit"] == limits["goal_iteration_limit"]
    assert defaults["goal_model_call_limit"] == limits["goal_model_call_limit"]


def test_goal_contract_without_criteria_or_constraints_requires_confirmation() -> None:
    assert _contract_needs_confirmation(
        {
            "objective": "完成一个目标",
            "scope": "",
            "constraints": [],
            "success_criteria": [],
        }
    ) is True


def test_intake_question_requires_confirmation_even_when_contract_is_complete() -> None:
    contract = {
        "objective": "分析目标",
        "scope": "按公司基本面、估值和行业情况综合分析",
        "constraints": ["依据可验证的公开资料"],
        "success_criteria": [
            {"criterion_id": "analysis", "verification_method": "tool_result"}
        ],
        "progress_text": "你更关心短期走势还是长期基本面？",
    }

    question = _clarification_question(contract)

    assert question == contract["progress_text"]
    assert _contract_needs_confirmation({**contract, "clarification_question": question}) is True


def test_goal_contract_without_criteria_interrupts_before_any_action() -> None:
    async def scenario() -> None:
        manager = LangGraphRuntimeManager(registry=_registry(_search_operation()))
        model = _SequenceStructuredModel(
            [
                GoalContract(
                    objective="等待用户补充完成条件",
                    scope="当前测试会话",
                    constraints=["不执行未确认的动作"],
                )
            ]
        )
        executor = FakeAtomicExecutor()
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "帮我完成一个还没有验收标准的目标"}],
                user_text="帮我完成一个还没有验收标准的目标",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="run-goal-missing-criteria",
                conversation_id="conversation-goal-missing-criteria",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=model,
                executor=executor,
                agent_mode="goal",
            )
            assert result.interrupted is True
            assert result.pending_interrupt is not None
            assert result.pending_interrupt["kind"] == "goal_contract_confirmation"
            assert executor.calls == []
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_goal_intake_clarification_interrupts_before_tools_and_uses_natural_question() -> None:
    async def scenario() -> None:
        manager = LangGraphRuntimeManager(registry=_registry(_search_operation()))
        question = "你希望我重点核对哪一项具体条件？"
        model = _SequenceStructuredModel(
            [
                GoalContract(
                    objective="完成测试目标",
                    scope="当前测试会话",
                    constraints=["只使用已注册的测试工具"],
                    clarification_question=question,
                    success_criteria=[
                        GoalCriterion(
                            criterion_id="criterion-1",
                            description="工具结果证明目标完成",
                            verification_method="tool_result",
                        )
                    ],
                )
            ]
        )
        executor = FakeAtomicExecutor()
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "帮我完成这个目标"}],
                user_text="帮我完成这个目标",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="run-goal-clarification",
                conversation_id="conversation-goal-clarification",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=model,
                executor=executor,
                agent_mode="goal",
            )
            assert result.interrupted is True
            assert result.pending_interrupt["kind"] == "goal_contract_confirmation"
            assert result.pending_interrupt["summary"] == question
            assert result.state["goal_status"] == "pending"
            assert executor.calls == []
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_explicit_goal_runs_only_the_goal_graph_and_persists_goal_state() -> None:
    async def scenario() -> None:
        manager = LangGraphRuntimeManager(registry=_registry(_search_operation()))
        model = _SequenceStructuredModel(
            [
                GoalContract(
                    objective="完成测试目标",
                    scope="当前测试会话",
                    constraints=["只使用已注册的测试工具"],
                    success_criteria=[
                        GoalCriterion(
                            criterion_id="criterion-1",
                            description="工具结果证明目标完成",
                            verification_method="tool_result",
                        )
                    ],
                ),
                GoalAction(
                    kind="tool",
                    action_id="action-1",
                    tool_name="search_source",
                    arguments={"source_id": "primary", "query": "测试"},
                    criterion_ids=["criterion-1"],
                    rationale="收集目标证据",
                ),
                GoalAssessment(
                    status="completed",
                    progress_text="目标已由工具结果证明",
                    criteria=[
                        {
                            "criterion_id": "criterion-1",
                            "status": "satisfied",
                            "evidence_ids": ["ev_action-1"],
                            "explanation": "工具返回有效结果",
                        }
                    ],
                ),
                GoalFinalAnswer(answer="测试目标已完成。", evidence_ids=["ev_action-1"]),
            ]
        )
        executor = FakeAtomicExecutor()
        await manager.start(testing=True)
        try:
            with patch(
                "src.agent.langgraph_runtime.runtime.resolve_planning_mode",
                side_effect=AssertionError("Goal must not invoke Plan routing"),
            ):
                result = await manager.run_new(
                    messages=[{"role": "user", "content": "完成测试目标"}],
                    user_text="完成测试目标",
                    system_prompt="",
                    llm_config={},
                    database=None,
                    controller=None,
                    run_id="run-goal-integration",
                    conversation_id="conversation-goal-integration",
                    run_attempt=1,
                    tenant_id="tenant",
                    owner_id="owner",
                    model=model,
                    executor=executor,
                    agent_mode="goal",
                )
            assert result.status == "completed"
            assert result.state["orchestrator_mode"] == "goal_v1"
            assert result.state["resolved_agent_mode"] == "goal"
            assert result.state["goal_status"] == "completed"
            assert result.state["goal_criteria"][0]["evidence_ids"] == ["ev_action-1"]
            assert len(executor.calls) == 1
            assert "planning_status" not in result.state
            assert "team_status" not in result.state
            assert await manager.has_checkpoint("conversation-goal-integration", run_id="run-goal-integration")
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_auto_does_not_route_to_goal_before_the_rollout_flag_is_enabled(monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.delenv("AGENT_GOAL_AUTO_ROUTING_ENABLED", raising=False)
        model = _SequenceStructuredModel(
            [
                ProductModeRoute(
                    mode="goal",
                    reason="需要持续观察和可验证完成条件",
                    execution_strategy="goal",
                )
            ]
        )
        events = _Events()
        result = await resolve_product_mode(
            {"run_id": "run-route-disabled", "user_text": "持续完成一个目标", "agent_mode": "auto"},
            type("Context", (), {"model": model, "events": events})(),
            requested="auto",
        )
        assert result["status"] == "failed"
        assert result["error_code"] == "goal_auto_routing_disabled"
        assert result["resolved_agent_mode"] == ""

    asyncio.run(scenario())


def test_auto_can_route_to_goal_after_the_rollout_flag_is_enabled(monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("AGENT_GOAL_AUTO_ROUTING_ENABLED", "true")
        model = _SequenceStructuredModel(
            [
                ProductModeRoute(
                    mode="goal",
                    reason="需要持续观察和可验证完成条件",
                    execution_strategy="goal",
                )
            ]
        )
        events = _Events()
        result = await resolve_product_mode(
            {"run_id": "run-route", "user_text": "持续完成一个目标", "agent_mode": "auto"},
            type("Context", (), {"model": model, "events": events})(),
            requested="auto",
        )
        assert result["resolved_agent_mode"] == "goal"
        assert result["orchestrator_execution_strategy"] == "goal"

    asyncio.run(scenario())


def test_goal_confirmation_can_modify_the_contract_and_resume_the_same_goal_thread() -> None:
    async def scenario() -> None:
        manager = LangGraphRuntimeManager(registry=_registry(_search_operation()))
        model = _SequenceStructuredModel(
            [
                GoalContract(
                    objective="初始目标",
                    scope="当前测试会话",
                    constraints=["只使用已注册的测试工具"],
                    success_criteria=[
                        GoalCriterion(
                            criterion_id="criterion-unknown",
                            description="需要用户确认的条件",
                            verification_method="unknown",
                        )
                    ],
                ),
                GoalContract(
                    objective="修改后的可验证目标",
                    scope="当前测试会话",
                    constraints=["只使用已注册的测试工具"],
                    success_criteria=[
                        GoalCriterion(
                            criterion_id="criterion-1",
                            description="工具结果证明目标完成",
                            verification_method="tool_result",
                        )
                    ],
                ),
                GoalAction(
                    kind="tool",
                    action_id="action-1",
                    tool_name="search_source",
                    arguments={"source_id": "primary", "query": "修改后的目标"},
                    criterion_ids=["criterion-1"],
                ),
                GoalAssessment(
                    status="completed",
                    progress_text="修改后的目标已完成",
                    criteria=[
                        {
                            "criterion_id": "criterion-1",
                            "status": "satisfied",
                            "evidence_ids": ["ev_action-1"],
                            "explanation": "工具结果有效",
                        }
                    ],
                ),
                GoalFinalAnswer(answer="修改后的目标已完成。", evidence_ids=["ev_action-1"]),
            ]
        )
        executor = FakeAtomicExecutor()
        await manager.start(testing=True)
        try:
            first = await manager.run_new(
                messages=[{"role": "user", "content": "初始目标"}],
                user_text="初始目标",
                system_prompt="",
                llm_config={},
                database=None,
                controller=None,
                run_id="run-goal-modify",
                conversation_id="conversation-goal-modify",
                run_attempt=1,
                tenant_id="tenant",
                owner_id="owner",
                model=model,
                executor=executor,
                agent_mode="goal",
            )
            assert first.interrupted is True
            assert first.pending_interrupt is not None
            assert first.pending_interrupt["kind"] == "goal_contract_confirmation"

            resumed = await manager.resume(
                interrupt_id=str(first.pending_interrupt["interrupt_id"]),
                decision={
                    "decision": "modify",
                    "message": "修改后的可验证目标",
                },
                llm_config={},
                database=None,
                controller=None,
                run_id="run-goal-modify",
                conversation_id="conversation-goal-modify",
                run_attempt=2,
                tenant_id="tenant",
                owner_id="owner",
                model=model,
                executor=executor,
            )
            assert resumed.status == "completed"
            assert resumed.state["goal_contract"]["objective"] == "修改后的可验证目标"
            assert resumed.state["goal_status"] == "completed"
            assert len(executor.calls) == 1
        finally:
            await manager.close()

    asyncio.run(scenario())
