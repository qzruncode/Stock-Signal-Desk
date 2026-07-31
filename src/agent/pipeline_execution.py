# -*- coding: utf-8 -*-
"""Execution, bounded goal recovery, and result-envelope validation."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping

from src.agent.orchestrator_v2.contracts import (
    AgentErrorCode, AgentStage, AgentStageEventV2, EffectLevel, GoalBudgetV2, GoalDisposition,
    OrchestratorV2Error, StageStatus,
)
from src.agent.capability_release import (
    build_capability_release_manifest,
)
from src.agent.orchestrator_v2.outcomes import execution_outcomes_v2
from src.agent.orchestrator_v2.goal_state import evaluate_goal_v2
from src.agent.orchestrator_v2.registry import capability_for
from src.agent.orchestrator_v2.runtime import CompiledIntentGraphV2, compile_intent_graph_v2, serialize_compiled_intent_graph_v2
from src.agent.orchestrator_v2.planner import plan_intent_graph_v2
from src.agent.task_executor import (
    PlanExecutionResult,
    WorkflowExecutor,
    action_fingerprint,
)
from src.agent.task_workflows import (
    TaskPlan,
    WorkflowCall,
    workflow_for,
)
from src.agent.resource_scheduler import ResourceCapacityExceeded
from src.agent.runtime_safety import get_agent_runtime_limits
from src.agent.run_registry import active_run_registry
from src.agent.pipeline_observers import build_result_processor_runner, build_workflow_outcome_observer
from src.agent.workflow_call_runner import run_workflow_call as _run_workflow_call_impl

logger = logging.getLogger(__name__)


@dataclass
class ExecutionPhaseResult:
    execution: PlanExecutionResult
    evidence: List[Dict[str, Any]]
    compiled_v2: CompiledIntentGraphV2
    resolved_tasks: list[Any]
    plan: TaskPlan
    planning_trace: Any
    outcomes_v2: tuple[Any, ...]
    goal_state_v2: Any


async def execute_standard_tasks(
    *,
    controller: Any,
    active_run_id: str,
    conversation_id: str | None,
    db_manager: Any,
    llm_cfg: Dict[str, Any],
    plan: TaskPlan,
    resolved_tasks: list[Any],
    compiled_v2: CompiledIntentGraphV2,
    planning_trace: Any,
    context_v2: Any,
    artifact_map: Dict[str, Any],
    planned_goal: Any,
    current_entities: list[dict[str, Any]],
    latest_user_text: str,
    request_fingerprint: str,
    messages: List[Dict[str, Any]],
    run_attempt: int,
    emit_v2_stage: Callable[..., Any],
    guarded_model_completion: Callable[..., Any],
    stream_structured_completion: Callable[..., Any],
    registry: Any,
    heartbeat_seconds: float,
    isolated_executor: Any,
    compact_result: Any,
    attach_fallback: Any,
    status_evidence_builder: Callable[..., Dict[str, Any]],
    flush_substreams: Callable[..., Any],
) -> ExecutionPhaseResult:
    await emit_v2_stage(
        AgentStageEventV2(
            run_id=active_run_id,
            stage=AgentStage.EXECUTION,
            status=StageStatus.STARTED,
            summary="正在执行已通过 Policy 预检的固定 Workflow",
        )
    )
    workflow_specs_by_task = {task.task_id: workflow_for(task.kind) for task in resolved_tasks}
    v2_policy_by_task = compiled_v2.policy_by_task_id
    v2_compiled_by_task = {item.task.task_id: item for item in compiled_v2.tasks}
    reviewed_action_fingerprints = (
        context_v2.pending_action_fingerprints()
    )
    approved_action_fingerprints: set[str] = set(
        reviewed_action_fingerprints
    )
    receipt_by_task: dict[str, str] = {}
    requires_approval_by_task: dict[str, bool] = {}

    def task_descriptor(compiled_task: Any) -> dict[str, Any]:
        task = compiled_task.task
        spec = workflow_for(task.kind)
        policy = v2_policy_by_task.get(task.task_id)
        requires_approval = (
            spec.requires_confirmation(task.parameters)
            or bool(
                policy is not None
                and policy.confirmation_required
            )
            or (
                task.kind.value == "batch_analysis"
                and len(task.symbols) > 10
            )
        )
        requires_approval_by_task[task.task_id] = requires_approval
        return {
            "task_id": task.task_id,
            "capability": compiled_task.capability.value,
            "effect": spec.effect.value,
            "confirmation": task.candidate.confirmation.value,
            "requires_approval": requires_approval,
            "action_fingerprint": action_fingerprint(task),
            "action_snapshot": {
                "kind": task.kind.value,
                "parameters": dict(task.parameters),
                "symbols": list(task.symbols),
            },
        }

    task_descriptors = [
        task_descriptor(compiled_task)
        for compiled_task in compiled_v2.tasks
    ]
    prepare_authorization = getattr(
        type(db_manager),
        "prepare_agent_run_authorization",
        None,
    )
    governance_enabled = bool(
        db_manager is not None
        and conversation_id
        and callable(prepare_authorization)
    )

    async def authorize_task_descriptors(
        descriptors: list[dict[str, Any]],
    ) -> tuple[dict[str, bool], set[str], dict[str, str]]:
        if not governance_enabled:
            return (
                {
                    str(item.get("task_id") or ""): True
                    for item in descriptors
                },
                set(reviewed_action_fingerprints),
                {},
            )
        authorization = await asyncio.to_thread(
            db_manager.prepare_agent_run_authorization,
            run_id=active_run_id,
            conversation_id=conversation_id,
            task_descriptors=descriptors,
            reviewed_action_fingerprints=tuple(
                reviewed_action_fingerprints
            ),
            source_run_id=(
                context_v2.turns[-1].run_id
                if context_v2.turns
                else None
            ),
            registry_manifest=build_capability_release_manifest(),
        )
        if isinstance(authorization, Mapping):
            return (
                {
                    str(key): bool(value)
                    for key, value in (
                        authorization.get("authorized_tasks") or {}
                    ).items()
                },
                set(
                    authorization.get(
                        "approved_action_fingerprints"
                    )
                    or ()
                ),
                {
                    str(key): str(value)
                    for key, value in (
                        authorization.get("receipt_by_task") or {}
                    ).items()
                },
            )
        return (
            {
                str(item.get("task_id") or ""): False
                for item in descriptors
            },
            set(),
            {},
        )

    (
        capability_authorizations,
        approved_action_fingerprints,
        receipt_by_task,
    ) = await authorize_task_descriptors(task_descriptors)

    async def run_workflow_call(
        call: WorkflowCall,
        arguments: Dict[str, Any],
    ) -> Dict[str, Any]:
        if (
            governance_enabled
            and requires_approval_by_task.get(call.task_id)
        ):
            receipt_id = receipt_by_task.get(call.task_id)
            authorize_step = getattr(
                type(db_manager),
                "authorize_agent_effect_step",
                None,
            )
            if (
                not receipt_id
                or db_manager is None
                or not callable(authorize_step)
                or not await asyncio.to_thread(
                    db_manager.authorize_agent_effect_step,
                    run_id=active_run_id,
                    task_id=call.task_id,
                    step_id=call.step_id,
                    receipt_id=receipt_id,
                )
            ):
                return {
                    "success": False,
                    "partial": False,
                    "error_code": (
                        AgentErrorCode.POLICY_BLOCKED.value
                    ),
                    "errors": [
                        "审批凭证缺失、过期或与当前运行不匹配，"
                        "程序已阻止内置能力调用。"
                    ],
                }
        return await _run_workflow_call_impl(
            call,
            arguments,
            controller=controller,
            active_run_id=active_run_id,
            conversation_id=conversation_id,
            db_manager=db_manager,
            llm_cfg=llm_cfg,
            v2_compiled_by_task=v2_compiled_by_task,
            emit_v2_stage=emit_v2_stage,
            registry=registry,
            heartbeat_seconds=heartbeat_seconds,
            isolated_executor=isolated_executor,
            compact_result=compact_result,
            attach_fallback=attach_fallback,
        )
    run_result_processor = build_result_processor_runner(
        active_run_id=active_run_id,
        emit_v2_stage=emit_v2_stage,
        guarded_model_completion=guarded_model_completion,
        stream_structured_completion=stream_structured_completion,
    )
    report_workflow_outcome = build_workflow_outcome_observer(
        active_run_id=active_run_id,
        emit_v2_stage=emit_v2_stage,
    )
    executor = WorkflowExecutor(
        registry,
        run_workflow_call,
        max_plan_tool_calls=get_agent_runtime_limits().max_plan_tool_calls,
        approved_actions=approved_action_fingerprints,
        processor_runner=run_result_processor,
        outcome_observer=report_workflow_outcome,
        execution_policies=v2_policy_by_task,
        capability_authorizations=capability_authorizations,
    )
    execution = await executor.execute(resolved_tasks)
    await flush_substreams(controller)
    evidence = [*execution.evidence, status_evidence_builder(plan, execution)]
    assert context_v2 is not None
    await emit_v2_stage(
        AgentStageEventV2(
            run_id=active_run_id,
            stage=AgentStage.EXECUTION,
            status=(StageStatus.SUCCEEDED if execution.success else StageStatus.FAILED),
            error_code=(None if execution.success else AgentErrorCode.TOOL_FAILED),
            summary=("固定 Workflow 执行完成" if execution.success else "固定 Workflow 存在失败或阻断"),
        )
    )
    await emit_v2_stage(
        AgentStageEventV2(
            run_id=active_run_id,
            stage=AgentStage.RESULT_VALIDATION,
            status=StageStatus.STARTED,
            summary="正在校验结果 Envelope、覆盖和资源投影",
        )
    )
    raw_outcomes_v2 = execution_outcomes_v2(execution)
    outcomes_v2 = tuple(
        capability_for(compiled_task.capability).result_model.model_validate(outcome)
        for compiled_task, outcome in zip(
            compiled_v2.tasks,
            raw_outcomes_v2,
            strict=True,
        )
    )
    limits = get_agent_runtime_limits()
    try:
        max_goal_revisions = max(
            0,
            min(4, int(os.getenv("AGENT_GOAL_MAX_REVISIONS", "2"))),
        )
    except (TypeError, ValueError):
        max_goal_revisions = 2
    goal_budget = GoalBudgetV2(
        max_plan_revisions=max_goal_revisions,
        max_provider_calls=limits.max_provider_calls,
        max_tool_calls=limits.max_plan_tool_calls,
        tool_calls_used=sum(len(item.calls) for item in execution.tasks),
    )

    def evaluate_current_goal():
        return evaluate_goal_v2(
            goal=planned_goal,
            task_outcomes=tuple(
                (compiled_task.capability, outcome)
                for compiled_task, outcome in zip(
                    compiled_v2.tasks,
                    outcomes_v2,
                    strict=True,
                )
            ),
            attempted_capabilities=tuple(item.capability for item in compiled_v2.tasks),
            budget=goal_budget,
            plan_revision=goal_budget.plan_revisions_used,
            max_expansion_capabilities=max(
                0,
                min(4, 12 - len(compiled_v2.tasks)),
            ),
        )

    goal_state_v2 = evaluate_current_goal()
    while goal_state_v2.evaluation is not None and goal_state_v2.evaluation.disposition == GoalDisposition.EXPAND_READS:
        revision = goal_budget.plan_revisions_used + 1
        proposed = goal_state_v2.evaluation.proposed_capabilities
        await emit_v2_stage(
            AgentStageEventV2(
                run_id=active_run_id,
                stage=AgentStage.RESULT_VALIDATION,
                status=StageStatus.STARTED,
                summary=(f"目标证据仍有缺口，开始第 {revision} 次受控补充；" "只允许追加无副作用读取能力"),
            )
        )
        try:
            recovery_context = dict(
                context_v2.planner_payload(
                    current_request=latest_user_text,
                )
            )
            recovery_context["goal_recovery"] = goal_state_v2.model_dump(mode="json")
            recovery_graph = await plan_intent_graph_v2(
                messages,
                llm_cfg,
                completion=stream_structured_completion,
                semantic_context=recovery_context,
                stage_observer=emit_v2_stage,
                run_id=active_run_id,
                current_entities=current_entities,
                fixed_goal=planned_goal,
                allowed_capabilities=proposed,
                plan_revision=revision,
            )
            recovery_compiled = await compile_intent_graph_v2(
                recovery_graph,
                llm_cfg,
                completion=stream_structured_completion,
                current_entities=current_entities,
                artifacts=artifact_map,
                stage_observer=emit_v2_stage,
                registry=registry,
            )
            existing_task_ids = {item.task.task_id for item in compiled_v2.tasks}
            duplicate_task_ids = existing_task_ids & {item.task.task_id for item in recovery_compiled.tasks}
            if duplicate_task_ids:
                raise OrchestratorV2Error(
                    AgentErrorCode.PLANNER_SCHEMA_INVALID,
                    "goal recovery reused existing task ids: " + ", ".join(sorted(duplicate_task_ids)),
                )
            non_read = [
                item.capability.value
                for item in recovery_compiled.tasks
                if item.execution_policy.effect != EffectLevel.READ
            ]
            if non_read:
                raise OrchestratorV2Error(
                    AgentErrorCode.POLICY_BLOCKED,
                    "goal recovery attempted non-read capabilities: " + ", ".join(non_read),
                )

            workflow_specs_by_task.update(
                {task.task_id: workflow_for(task.kind) for task in recovery_compiled.resolved_tasks}
            )
            v2_policy_by_task.update(recovery_compiled.policy_by_task_id)
            v2_compiled_by_task.update({item.task.task_id: item for item in recovery_compiled.tasks})
            recovery_descriptors = [
                task_descriptor(compiled_task)
                for compiled_task in recovery_compiled.tasks
            ]
            (
                recovery_authorizations,
                recovery_approvals,
                recovery_receipts,
            ) = await authorize_task_descriptors(
                recovery_descriptors
            )
            approved_action_fingerprints.update(
                recovery_approvals
            )
            receipt_by_task.update(recovery_receipts)
            recovery_executor = WorkflowExecutor(
                registry,
                run_workflow_call,
                max_plan_tool_calls=limits.max_plan_tool_calls,
                approved_actions=approved_action_fingerprints,
                processor_runner=run_result_processor,
                outcome_observer=report_workflow_outcome,
                execution_policies=recovery_compiled.policy_by_task_id,
                capability_authorizations=recovery_authorizations,
            )
            recovery_execution = await recovery_executor.execute(recovery_compiled.resolved_tasks)
            execution = PlanExecutionResult(
                tasks=[
                    *execution.tasks,
                    *recovery_execution.tasks,
                ]
            )
            plan = TaskPlan.model_validate(
                {
                    "tasks": [
                        *plan.tasks,
                        *recovery_compiled.plan.tasks,
                    ],
                    "needs_clarification": False,
                    "clarification_question": None,
                    "source": "semantic_goal_recovery",
                }
            )
            compiled_v2 = CompiledIntentGraphV2(
                run_id=active_run_id,
                plan=plan,
                tasks=(
                    *compiled_v2.tasks,
                    *recovery_compiled.tasks,
                ),
                assumptions=(
                    *compiled_v2.assumptions,
                    *recovery_compiled.assumptions,
                ),
            )
            resolved_tasks = compiled_v2.resolved_tasks
            goal_budget = goal_budget.model_copy(
                update={
                    "plan_revisions_used": revision,
                    "tool_calls_used": sum(len(item.calls) for item in execution.tasks),
                }
            )
            raw_outcomes_v2 = execution_outcomes_v2(execution)
            outcomes_v2 = tuple(
                capability_for(compiled_task.capability).result_model.model_validate(outcome)
                for compiled_task, outcome in zip(
                    compiled_v2.tasks,
                    raw_outcomes_v2,
                    strict=True,
                )
            )
            goal_state_v2 = evaluate_current_goal()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "[AgentGoal] bounded recovery stopped run=%s revision=%s: %s",
                active_run_id,
                revision,
                exc,
                exc_info=True,
            )
            goal_budget = goal_budget.model_copy(
                update={
                    "plan_revisions_used": goal_budget.max_plan_revisions,
                }
            )
            goal_state_v2 = evaluate_current_goal()
            break

    evidence = [*execution.evidence, status_evidence_builder(plan, execution)]
    planning_trace = planning_trace.model_copy(
        update={
            "goal_state": goal_state_v2.model_dump(mode="json"),
            "plan_revision": goal_state_v2.plan_revision,
        }
    )
    if db_manager is not None and conversation_id and goal_state_v2.plan_revision > 0:
        recovery_checkpoint_saved = await asyncio.to_thread(
            db_manager.save_agent_run_checkpoint,
            active_run_id,
            worker_id=active_run_registry.worker_id,
            attempt=run_attempt,
            checkpoint=serialize_compiled_intent_graph_v2(
                compiled_v2,
                planning_trace=planning_trace,
                request_fingerprint=request_fingerprint,
            ),
        )
        if not recovery_checkpoint_saved:
            logger.warning(
                "[AgentGoal] merged recovery checkpoint lost ownership " "run=%s revision=%s",
                active_run_id,
                goal_state_v2.plan_revision,
            )
    return ExecutionPhaseResult(
        execution=execution,
        evidence=evidence,
        compiled_v2=compiled_v2,
        resolved_tasks=resolved_tasks,
        plan=plan,
        planning_trace=planning_trace,
        outcomes_v2=outcomes_v2,
        goal_state_v2=goal_state_v2,
    )


__all__ = ["ExecutionPhaseResult", "execute_standard_tasks"]
