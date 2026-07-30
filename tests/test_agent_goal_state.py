# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from src.agent.orchestrator_v2.contracts import (
    AgentErrorCode,
    Capability,
    ClaimRequirementV2,
    CoverageV2,
    ErrorDetailV2,
    EvidenceDimension,
    GoalBudgetV2,
    GoalContractV2,
    GoalDisposition,
    GoalTerminalReason,
    IntentOutlineV2,
    OutcomeStatus,
    PlannerVerificationV2,
    QuestionType,
    TaskOutcomeV2,
    UncertaintyMode,
)
from src.agent.orchestrator_v2.goal_state import evaluate_goal_v2
from src.agent.orchestrator_v2.planner import (
    ExactContractValidationError,
    _validate_outline_capability_contracts,
    _validate_recovery_outline_contracts,
    plan_intent_graph_v2,
)
from src.agent.orchestrator_v2.registry import capability_for
from src.agent.task_workflows import (
    ResolvedTask,
    StandardTask,
    StandardTaskKind,
    compile_task,
)


def _forecast_goal() -> GoalContractV2:
    return GoalContractV2(
        objective="研判未来一至六个月市场主线",
        question_type=QuestionType.FORECAST,
        uncertainty_mode=UncertaintyMode.SCENARIO,
        time_horizon="未来一至六个月",
        deliverables=(
            "候选主线排序",
            "成立条件与失效信号",
        ),
        claims=(ClaimRequirementV2(
            claim_id="mainline",
            question="未来市场主线及其验证条件是什么",
            required_dimensions=(
                EvidenceDimension.MARKET_MAINLINE,
                EvidenceDimension.RESEARCH_CONSENSUS,
                EvidenceDimension.MACRO_POLICY,
                EvidenceDimension.INDUSTRY_STRUCTURE,
            ),
        ),),
    )


def _outcome(
    *,
    task_id: str,
    status: OutcomeStatus,
) -> TaskOutcomeV2:
    failed = status in {
        OutcomeStatus.FAILED,
        OutcomeStatus.BLOCKED,
    }
    return TaskOutcomeV2(
        task_id=task_id,
        status=status,
        coverage=CoverageV2(
            requested=1,
            covered=0 if failed else 1,
            missing=("result",) if failed else (),
            complete=not failed,
        ),
        errors=(
            (ErrorDetailV2(
                code=AgentErrorCode.TOOL_FAILED,
                message="source unavailable",
                retryable=True,
            ),)
            if failed
            else ()
        ),
        result=None if failed else {"answer": "structured"},
    )


def test_goal_evaluation_completes_when_every_claim_dimension_is_covered():
    state = evaluate_goal_v2(
        goal=_forecast_goal(),
        task_outcomes=((
            Capability.MARKET_MAINLINE_RESEARCH,
            _outcome(
                task_id="mainline",
                status=OutcomeStatus.SUCCEEDED,
            ),
        ),),
        attempted_capabilities=(Capability.MARKET_MAINLINE_RESEARCH,),
        budget=GoalBudgetV2(),
        plan_revision=0,
    )

    assert state.evaluation is not None
    assert state.evaluation.disposition == GoalDisposition.COMPLETE
    assert (
        state.evaluation.terminal_reason
        == GoalTerminalReason.GOAL_SATISFIED
    )
    assert state.claim_ledger[0].missing_dimensions == ()
    assert state.evidence_ledger[0].dimensions == tuple(sorted(
        capability_for(
            Capability.MARKET_MAINLINE_RESEARCH
        ).evidence_dimensions,
        key=lambda item: item.value,
    ))


def test_failed_primary_proposes_only_new_read_capabilities_that_improve_coverage():
    state = evaluate_goal_v2(
        goal=_forecast_goal(),
        task_outcomes=((
            Capability.MARKET_MAINLINE_RESEARCH,
            _outcome(
                task_id="mainline",
                status=OutcomeStatus.FAILED,
            ),
        ),),
        attempted_capabilities=(Capability.MARKET_MAINLINE_RESEARCH,),
        budget=GoalBudgetV2(),
        plan_revision=0,
    )

    assert state.evaluation is not None
    assert state.evaluation.disposition == GoalDisposition.EXPAND_READS
    assert Capability.MARKET_MAINLINE_RESEARCH not in (
        state.evaluation.proposed_capabilities
    )
    assert set(state.evaluation.proposed_capabilities) >= {
        Capability.MACRO_ANALYSIS,
        Capability.INDUSTRY_RESEARCH,
    }
    for capability in state.evaluation.proposed_capabilities:
        spec = capability_for(capability)
        assert spec.execution_policy.effect.value == "read"
        assert spec.auto_expandable is True
        assert not spec.input_resources
        assert (
            spec.evidence_dimensions
            & set(state.evaluation.missing_dimensions)
        )


def test_goal_returns_best_effort_after_revision_budget_is_exhausted():
    budget = GoalBudgetV2(
        max_plan_revisions=1,
        plan_revisions_used=1,
    )
    state = evaluate_goal_v2(
        goal=_forecast_goal(),
        task_outcomes=((
            Capability.MARKET_MAINLINE_RESEARCH,
            _outcome(
                task_id="mainline",
                status=OutcomeStatus.FAILED,
            ),
        ),),
        attempted_capabilities=(Capability.MARKET_MAINLINE_RESEARCH,),
        budget=budget,
        plan_revision=1,
    )

    assert state.evaluation is not None
    assert state.evaluation.disposition == GoalDisposition.BEST_EFFORT
    assert (
        state.evaluation.terminal_reason
        == GoalTerminalReason.BUDGET_EXHAUSTED
    )
    assert state.evaluation.proposed_capabilities == ()


def test_forecast_goal_rejects_false_exactness():
    try:
        GoalContractV2(
            objective="预测市场主线",
            question_type=QuestionType.FORECAST,
            uncertainty_mode=UncertaintyMode.EXACT,
            deliverables=("结论",),
            claims=(ClaimRequirementV2(
                claim_id="mainline",
                question="主线是什么",
                required_dimensions=(
                    EvidenceDimension.MARKET_MAINLINE,
                ),
            ),),
        )
    except ValueError as exc:
        assert "scenario" in str(exc)
    else:
        raise AssertionError("forecast goal must require scenario uncertainty")


def test_market_overview_cannot_satisfy_a_market_mainline_goal():
    outline = IntentOutlineV2(
        goal=_forecast_goal(),
        nodes=({
            "node_id": "overview",
            "capability": "market_overview",
            "objective": "判断未来市场主线",
            "input_refs": [],
            "result_selection": None,
        },),
    )

    try:
        _validate_outline_capability_contracts(outline)
    except ExactContractValidationError as exc:
        assert exc.issues[0].code == "goal_evidence_coverage_incomplete"
        assert (
            EvidenceDimension.MARKET_MAINLINE.value
            in exc.issues[0].message
        )
    else:
        raise AssertionError("market_overview must not prove market mainline")


def test_market_mainline_is_a_standalone_typed_workflow():
    outline = IntentOutlineV2(
        goal=_forecast_goal(),
        nodes=({
            "node_id": "mainline",
            "capability": "market_mainline_research",
            "objective": "研判未来市场主线",
            "input_refs": [],
            "result_selection": None,
        },),
    )
    _validate_outline_capability_contracts(outline)

    calls = compile_task(ResolvedTask(candidate=StandardTask(
        task_id="mainline",
        kind=StandardTaskKind.MARKET_MAINLINE_RESEARCH,
        objective="研判未来市场主线",
    )))

    assert len(calls) == 1
    assert calls[0].tool_name == "prepare_market_mainline_snapshot"
    assert calls[0].arguments == {"force": False}


def test_recovery_plan_cannot_change_goal_namespace_or_capability_allowlist():
    invalid = IntentOutlineV2(
        goal=_forecast_goal(),
        nodes=({
            "node_id": "mutate",
            "capability": "watchlist_mutation",
            "objective": "修改自选",
            "input_refs": [],
            "result_selection": None,
        },),
    )

    try:
        _validate_recovery_outline_contracts(
            invalid,
            fixed_goal=_forecast_goal(),
            allowed_capabilities=frozenset({
                Capability.MACRO_ANALYSIS,
            }),
            node_id_prefix="repair_1_",
        )
    except ExactContractValidationError as exc:
        assert {
            issue.code for issue in exc.issues
        } == {
            "recovery_capability_out_of_scope",
            "recovery_node_id_not_namespaced",
        }
    else:
        raise AssertionError("unsafe recovery graph must be rejected")


def test_forecast_planner_routes_to_mainline_and_runs_independent_verifier():
    calls: list[str] = []

    async def completion(**kwargs: Any) -> dict[str, Any]:
        function_name = kwargs["tool_choice"]["function"]["name"]
        calls.append(function_name)
        if function_name == "submit_intent_outline_v2":
            payload = {
                "goal": _forecast_goal().model_dump(mode="json"),
                "nodes": [{
                    "node_id": "mainline",
                    "capability": "market_mainline_research",
                    "objective": "研判未来一至六个月市场主线",
                    "input_refs": [],
                    "result_selection": None,
                }],
                "needs_clarification": False,
                "clarification_question": None,
            }
        elif function_name == "verify_intent_outline_v2":
            payload = {
                "accepted": True,
                "confidence": 0.96,
                "missing_capabilities": [],
                "extraneous_node_ids": [],
                "resource_issues": [],
                "rationale": "目标、能力和证据维度完整一致。",
            }
        else:
            raise AssertionError(function_name)
        return {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": function_name,
                            "arguments": json.dumps(
                                payload,
                                ensure_ascii=False,
                            ),
                        },
                    }],
                },
            }],
        }

    graph = asyncio.run(plan_intent_graph_v2(
        [{"role": "user", "content": "未来市场主线会是什么？"}],
        {"model": "test-model"},
        completion=completion,
    ))

    assert graph.outline.goal.question_type == QuestionType.FORECAST
    assert [
        node.outline.capability for node in graph.nodes
    ] == [Capability.MARKET_MAINLINE_RESEARCH]
    assert calls == [
        "submit_intent_outline_v2",
        "verify_intent_outline_v2",
    ]


def test_verifier_negative_verdict_can_use_rationale_without_typed_issues():
    verification = PlannerVerificationV2(
        accepted=False,
        confidence=0.61,
        rationale="目标与当前请求之间仍存在无法归类的语义偏差。",
    )

    assert verification.accepted is False
    with pytest.raises(ValueError):
        PlannerVerificationV2(
            accepted=True,
            confidence=0.99,
            resource_issues=("资源边方向错误",),
            rationale="不应接受带有明确问题的计划。",
        )


def test_low_confidence_verifier_abstention_does_not_replan_valid_graph():
    calls: list[str] = []

    async def completion(**kwargs: Any) -> dict[str, Any]:
        function_name = kwargs["tool_choice"]["function"]["name"]
        calls.append(function_name)
        if function_name == "submit_intent_outline_v2":
            payload = {
                "goal": _forecast_goal().model_dump(mode="json"),
                "nodes": [{
                    "node_id": "mainline",
                    "capability": "market_mainline_research",
                    "objective": "研判未来一至六个月市场主线",
                    "input_refs": [],
                    "result_selection": None,
                }],
                "needs_clarification": False,
                "clarification_question": None,
            }
        elif function_name == "verify_intent_outline_v2":
            payload = {
                "accepted": False,
                "confidence": 0.0,
                "missing_capabilities": [],
                "extraneous_node_ids": [],
                "resource_issues": [],
                "rationale": "尚未完成逐条检查。",
            }
        else:
            raise AssertionError(function_name)
        return {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": function_name,
                            "arguments": json.dumps(
                                payload,
                                ensure_ascii=False,
                            ),
                        },
                    }],
                },
            }],
        }

    graph = asyncio.run(plan_intent_graph_v2(
        [{"role": "user", "content": "未来市场主线会是什么？"}],
        {"model": "test-model"},
        completion=completion,
    ))

    assert graph.outline.goal == _forecast_goal()
    assert calls == [
        "submit_intent_outline_v2",
        "verify_intent_outline_v2",
    ]


def test_forecast_verifier_can_trigger_only_one_bounded_replan():
    calls: list[str] = []
    verifier_calls = 0

    async def completion(**kwargs: Any) -> dict[str, Any]:
        nonlocal verifier_calls
        function_name = kwargs["tool_choice"]["function"]["name"]
        calls.append(function_name)
        if function_name == "submit_intent_outline_v2":
            payload = {
                "goal": _forecast_goal().model_dump(mode="json"),
                "nodes": [{
                    "node_id": "mainline",
                    "capability": "market_mainline_research",
                    "objective": "研判未来一至六个月市场主线",
                    "input_refs": [],
                    "result_selection": None,
                }],
                "needs_clarification": False,
                "clarification_question": None,
            }
        elif function_name == "verify_intent_outline_v2":
            verifier_calls += 1
            payload = (
                {
                    "accepted": False,
                    "confidence": 0.92,
                    "missing_capabilities": [],
                    "extraneous_node_ids": [],
                    "resource_issues": ["缺少失效信号交付物"],
                    "rationale": "预测回答必须包含失效信号。",
                }
                if verifier_calls == 1
                else {
                    "accepted": True,
                    "confidence": 0.95,
                    "missing_capabilities": [],
                    "extraneous_node_ids": [],
                    "resource_issues": [],
                    "rationale": "重规划后目标与能力覆盖完整。",
                }
            )
        else:
            raise AssertionError(function_name)
        return {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": function_name,
                            "arguments": json.dumps(
                                payload,
                                ensure_ascii=False,
                            ),
                        },
                    }],
                },
            }],
        }

    graph = asyncio.run(plan_intent_graph_v2(
        [{"role": "user", "content": "未来市场主线会是什么？"}],
        {"model": "test-model"},
        completion=completion,
    ))

    assert graph.outline.goal == _forecast_goal()
    assert calls == [
        "submit_intent_outline_v2",
        "verify_intent_outline_v2",
        "submit_intent_outline_v2",
        "verify_intent_outline_v2",
    ]
