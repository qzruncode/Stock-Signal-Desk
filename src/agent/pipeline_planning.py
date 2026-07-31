# -*- coding: utf-8 -*-
"""Planning and compilation phase of the standard-task pipeline."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional

import litellm

from api.v1.endpoints.agent.chat_reasoning import _agent_stage_reasoning_line, _append_process_reasoning
from api.v1.endpoints.agent.chat_context_helpers import last_user_text as _last_user_text
from src.agent.conversation_compaction import estimate_messages_tokens
from src.agent.message_normalization import normalize_incoming_messages as _normalize_incoming_messages
from src.agent.orchestrator_v2.contracts import (
    AgentErrorCode, AgentStage, AgentStageEventV2, OrchestratorV2Error, PlanningTraceV2, StageStatus,
    stable_fingerprint,
)
from src.agent.orchestrator_v2.planner import PlannedIntentGraphV2, plan_intent_graph_v2
from src.agent.orchestrator_v2.runtime import (
    CompiledIntentGraphV2, compile_intent_graph_v2, restore_compiled_intent_graph_v2,
    serialize_compiled_intent_graph_v2,
)
from src.agent.orchestrator_v2.state import ConversationContextV2, migrate_legacy_context
from src.agent.model_runtime import GuardedModelRuntime
from src.agent.run_registry import active_run_registry
from src.agent.task_planner import TaskPlanValidationError
from src.tools.symbols import find_securities_in_text

logger = logging.getLogger(__name__)


def _estimate_messages_tokens(messages: List[Dict[str, Any]], model: str) -> int:
    return estimate_messages_tokens(messages, model, token_counter=litellm.token_counter)


@dataclass
class StandardPlanningResult:
    active_run_id: str
    latest_user_text: str
    request_fingerprint: str
    stage_durations_ms: Dict[str, int]
    current_entities: list[dict[str, Any]]
    context_v2: ConversationContextV2
    compiled_v2: CompiledIntentGraphV2
    graph_v2: PlannedIntentGraphV2 | None
    planning_trace: PlanningTraceV2
    artifact_map: Dict[str, Any]
    plan: Any
    resolved_tasks: list[Any]
    planned_goal: Any
    emit_v2_stage: Callable[..., Any]
    guarded_model_completion: Callable[..., Any]
    stream_structured_completion: Callable[..., Any]


async def plan_standard_task(
    controller: Any,
    messages: List[Dict[str, Any]],
    llm_cfg: Dict[str, Any],
    base_system_prompt: str,
    *,
    state: Optional[Dict[str, Any]],
    conversation_context: Optional[Dict[str, Any]],
    conversation_id: str | None,
    run_id: str | None,
    run_attempt: int,
    recovery_checkpoint: Mapping[str, Any] | None,
    db_manager: Any,
    registry: Any,
    final_streamer: Callable[..., Any],
    structured_streamer: Callable[..., Any],
    answer_validator: Callable[..., Any],
    planner: Callable[..., Any],
    compiler: Callable[..., Any],
    model_completion: Callable[..., Any],
) -> StandardPlanningResult | str:
    latest_user_text = _last_user_text(messages)
    active_run_id = run_id or uuid.uuid4().hex
    context_v2: ConversationContextV2
    compiled_v2: CompiledIntentGraphV2
    graph_v2: PlannedIntentGraphV2 | None = None
    planning_trace: PlanningTraceV2 | None = None
    artifact_map: Dict[str, Any] = {}
    v2_stage_started: Dict[tuple[str, str], float] = {}
    v2_stage_durations_ms: Dict[str, int] = {}
    current_entities = find_securities_in_text(latest_user_text, limit=300)
    request_fingerprint = stable_fingerprint(
        {
            "messages": messages,
            "conversation_context": conversation_context or {},
        }
    )

    async def emit_v2_stage(event: AgentStageEventV2) -> None:
        stage_key = (
            event.stage.value,
            event.task_id or "__run__",
        )
        if event.status == StageStatus.STARTED:
            v2_stage_started.setdefault(stage_key, time.monotonic())
        else:
            started_at = v2_stage_started.pop(stage_key, None)
            if started_at is not None:
                duration = int((time.monotonic() - started_at) * 1000)
                duration_key = event.stage.value if event.task_id is None else f"{event.stage.value}:{event.task_id}"
                v2_stage_durations_ms[duration_key] = duration
        add_data = getattr(controller, "add_data", None)
        if callable(add_data):
            add_data(event.model_dump(mode="json"))
        _append_process_reasoning(
            controller,
            _agent_stage_reasoning_line(event),
        )
        if db_manager is not None and conversation_id and event.status != StageStatus.STARTED:
            trace_status = (
                "completed"
                if (event.stage == AgentStage.COMPLETED and event.status == StageStatus.SUCCEEDED)
                else (
                    "cancelled"
                    if event.status == StageStatus.CANCELLED
                    else (
                        "blocked"
                        if (event.stage == AgentStage.COMPLETED and event.status == StageStatus.BLOCKED)
                        else (
                            "failed"
                            if (event.stage == AgentStage.COMPLETED and event.status == StageStatus.FAILED)
                            else "running"
                        )
                    )
                )
            )
            try:
                await asyncio.to_thread(
                    db_manager.upsert_agent_run_trace,
                    run_id=active_run_id,
                    conversation_id=conversation_id,
                    orchestrator_mode="unified",
                    status=trace_status,
                    error_code=(event.error_code.value if event.error_code is not None else None),
                    latest_stage=event.model_dump(mode="json"),
                )
            except Exception:
                logger.warning(
                    "[AgentOrchestrator] failed to persist latest stage " "run=%s stage=%s",
                    active_run_id,
                    event.stage.value,
                    exc_info=True,
                )

    model_runtime = GuardedModelRuntime(
        database=db_manager,
        run_id=active_run_id,
        worker_id=active_run_registry.worker_id,
        model=str(llm_cfg.get("model") or "default"),
        token_estimator=_estimate_messages_tokens,
    )

    async def guarded_model_completion(**kwargs: Any) -> Any:
        return await model_runtime.complete(
            model_completion,
            **kwargs,
        )

    async def stream_structured_completion(**kwargs: Any) -> Any:
        return await structured_streamer(
            controller,
            guarded_model_completion,
            **kwargs,
        )

    if isinstance(conversation_context, dict) and str(conversation_context.get("version") or "") == "3":
        context_v2 = ConversationContextV2.from_value(conversation_context)
        migrated_artifacts = ()
    else:
        context_v2, migrated_artifacts = migrate_legacy_context(
            conversation_context,
            conversation_id=conversation_id or "ephemeral",
        )
        if db_manager is not None and migrated_artifacts:
            await asyncio.to_thread(
                db_manager.save_agent_artifacts,
                migrated_artifacts,
            )
    artifact_map.update({artifact.artifact_id: artifact for artifact in migrated_artifacts})
    if db_manager is not None and conversation_id:
        referenced_artifact_ids = [
            reference.artifact_id
            for turn in context_v2.turns
            for reference in turn.terminal_artifacts
            if reference.artifact_id not in artifact_map
        ]
        loaded_artifacts = await asyncio.to_thread(
            db_manager.get_agent_artifacts,
            referenced_artifact_ids,
        )
        artifact_map.update({artifact.artifact_id: artifact for artifact in loaded_artifacts})

    try:
        restored_checkpoint = (
            restore_compiled_intent_graph_v2(
                recovery_checkpoint,
                expected_run_id=active_run_id,
                request_fingerprint=request_fingerprint,
            )
            if isinstance(recovery_checkpoint, Mapping)
            else None
        )
        if restored_checkpoint is not None:
            compiled_v2, planning_trace = restored_checkpoint
            await emit_v2_stage(
                AgentStageEventV2(
                    run_id=active_run_id,
                    stage=AgentStage.COMPILATION,
                    status=StageStatus.SUCCEEDED,
                    summary="已从持久检查点恢复编译结果",
                )
            )
        else:
            graph_v2 = await planner(
                messages,
                llm_cfg,
                completion=stream_structured_completion,
                semantic_context=context_v2.planner_payload(
                    current_request=latest_user_text,
                ),
                stage_observer=emit_v2_stage,
                run_id=active_run_id,
                current_entities=current_entities,
            )
            planning_trace = graph_v2.trace
            compiled_v2 = await compiler(
                graph_v2,
                llm_cfg,
                completion=stream_structured_completion,
                current_entities=current_entities,
                artifacts=artifact_map,
                stage_observer=emit_v2_stage,
                registry=registry,
            )
            if db_manager is not None and conversation_id:
                checkpoint_saved = await asyncio.to_thread(
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
                if not checkpoint_saved:
                    raise RuntimeError("compiled checkpoint rejected because run ownership changed")
        plan = compiled_v2.plan
        resolved_tasks = compiled_v2.resolved_tasks
        if db_manager is not None and conversation_id:
            assert planning_trace is not None
            await asyncio.to_thread(
                db_manager.upsert_agent_run_trace,
                run_id=active_run_id,
                conversation_id=conversation_id,
                orchestrator_mode="unified",
                status="compiled",
                schema_version=planning_trace.schema_version,
                model_config=llm_cfg,
                stage_durations=dict(planning_trace.stage_durations_ms),
                raw_outline=planning_trace.raw_outline,
                normalized_outline=planning_trace.normalized_outline,
                raw_intents=dict(planning_trace.raw_intents),
                normalized_intents=dict(planning_trace.normalized_intents),
                repairs=[item.model_dump(mode="json") for item in planning_trace.repairs],
                verification=planning_trace.verification,
                goal_state=planning_trace.goal_state,
                compiled_plan={
                    "assumptions": [assumption.model_dump(mode="json") for assumption in compiled_v2.assumptions],
                    "tasks": [
                        {
                            "task_id": item.task.task_id,
                            "capability": item.capability.value,
                            "parameters": dict(item.task.parameters),
                            "depends_on": list(item.task.candidate.depends_on),
                            "execution_policy": item.execution_policy.model_dump(mode="json"),
                            "resource_fingerprint": item.resource_fingerprint,
                        }
                        for item in compiled_v2.tasks
                    ],
                },
            )
        logger.info(
            "[TaskPlanner] source=%s tasks=%s dependencies=%s",
            plan.source,
            [task.kind.value for task in plan.tasks],
            {task.task_id: task.depends_on for task in plan.tasks},
        )
    except OrchestratorV2Error as exc:
        logger.warning(
            "[AgentOrchestrator] run=%s code=%s task=%s detail=%s",
            active_run_id,
            exc.code.value,
            exc.task_id,
            exc,
        )
        failure_status = (
            "blocked"
            if exc.code
            in {
                AgentErrorCode.CLARIFICATION_REQUIRED,
                AgentErrorCode.RESOURCE_UNAVAILABLE,
                AgentErrorCode.POLICY_BLOCKED,
            }
            else "failed"
        )
        if db_manager is not None and conversation_id and state is None:
            await asyncio.to_thread(
                db_manager.upsert_agent_run_trace,
                run_id=active_run_id,
                conversation_id=conversation_id,
                orchestrator_mode="unified",
                status=failure_status,
                error_code=exc.code.value,
                schema_version=(planning_trace.schema_version if planning_trace is not None else "orchestrator-4.0"),
                model_config=llm_cfg,
                stage_durations=(
                    {
                        **dict(planning_trace.stage_durations_ms),
                        **v2_stage_durations_ms,
                    }
                    if planning_trace is not None
                    else v2_stage_durations_ms
                ),
                raw_outline=(planning_trace.raw_outline if planning_trace is not None else None),
                normalized_outline=(planning_trace.normalized_outline if planning_trace is not None else None),
                raw_intents=(dict(planning_trace.raw_intents) if planning_trace is not None else None),
                normalized_intents=(dict(planning_trace.normalized_intents) if planning_trace is not None else None),
                repairs=([exc.metadata["repair"]] if isinstance(exc.metadata.get("repair"), dict) else []),
            )
        if exc.code == AgentErrorCode.CLARIFICATION_REQUIRED:
            failure_text = str(exc)
        elif exc.code == AgentErrorCode.RESOURCE_UNAVAILABLE:
            failure_text = f"{exc} 本轮没有调用数据工具，也没有改用新闻或公网来源兜底。"
        elif exc.code == AgentErrorCode.PLANNER_TIMEOUT:
            failure_text = f"规划模型请求被上游连接终止：{exc}；" "本轮没有调用任何数据工具。"
        elif exc.code == AgentErrorCode.PLANNER_SCHEMA_INVALID:
            failure_text = (
                "规划输出在一次字段级修复后仍未通过精确 Schema，" "这是内部规划契约错误；本轮没有调用任何数据工具。"
            )
        else:
            failure_text = f"编排在 {exc.code.value} 阶段失败：{exc}；" "本轮没有继续执行。"
        if exc.code in {
            AgentErrorCode.PLANNER_SCHEMA_INVALID,
            AgentErrorCode.PLANNER_TIMEOUT,
        }:
            degraded_messages = [
                {
                    "role": "system",
                    "content": (
                        f"{base_system_prompt}\n\n"
                        "本轮结构化编排不可用。你没有工具权限，也没有实时数据。"
                        "请仍然直接回答用户可以由通用知识、解释、推理或写作完成的部分；"
                        "对需要实时数据或外部动作的部分明确边界，但不要暴露内部错误、"
                        "Schema、编排器或要求用户重试。"
                    ),
                },
                *_normalize_incoming_messages(messages),
            ]
            degraded_answer = await final_streamer(
                controller,
                degraded_messages,
                llm_cfg,
                state=state,
                evidence=[],
                answer_validator=answer_validator,
                completion=guarded_model_completion,
            )
            if degraded_answer.strip():
                await emit_v2_stage(
                    AgentStageEventV2(
                        run_id=active_run_id,
                        stage=AgentStage.COMPLETED,
                        status=StageStatus.SUCCEEDED,
                        error_code=exc.code,
                        summary="结构化编排失败，已降级为无工具回答",
                    )
                )
                if state is not None:
                    state["assistant_text"] = degraded_answer
                    state["_run_status"] = "partial"
                    state["_run_error_code"] = exc.code.value
                    if context_v2 is not None:
                        state["agent_context"] = context_v2.model_dump(mode="json")
                    controller.assistant_text_snapshot = degraded_answer
                if db_manager is not None and conversation_id and state is None:
                    await asyncio.to_thread(
                        db_manager.upsert_agent_run_trace,
                        run_id=active_run_id,
                        conversation_id=conversation_id,
                        orchestrator_mode="unified",
                        status="partial",
                        error_code=exc.code.value,
                    )
                return degraded_answer
        controller.append_text(failure_text)
        if state is not None:
            state["assistant_text"] = failure_text
            state["_run_status"] = failure_status
            state["_run_error_code"] = exc.code.value
            if context_v2 is not None:
                state["agent_context"] = context_v2.model_dump(mode="json")
            controller.assistant_text_snapshot = failure_text
        return failure_text
    except TaskPlanValidationError as exc:
        logger.warning(
            "[TaskPlanner] internal contract validation failed: %s",
            exc,
        )
        await emit_v2_stage(
            AgentStageEventV2(
                run_id=active_run_id,
                stage=AgentStage.COMPILATION,
                status=StageStatus.FAILED,
                error_code=AgentErrorCode.PLANNER_SCHEMA_INVALID,
                summary=str(exc),
            )
        )
        if db_manager is not None and conversation_id:
            await asyncio.to_thread(
                db_manager.upsert_agent_run_trace,
                run_id=active_run_id,
                conversation_id=conversation_id,
                orchestrator_mode="unified",
                status="failed",
                error_code=AgentErrorCode.PLANNER_SCHEMA_INVALID.value,
                schema_version=(planning_trace.schema_version if planning_trace is not None else "orchestrator-4.0"),
                model_config=llm_cfg,
                stage_durations=(
                    {
                        **dict(planning_trace.stage_durations_ms),
                        **v2_stage_durations_ms,
                    }
                    if planning_trace is not None
                    else v2_stage_durations_ms
                ),
                raw_outline=(planning_trace.raw_outline if planning_trace is not None else None),
                normalized_outline=(planning_trace.normalized_outline if planning_trace is not None else None),
                raw_intents=(dict(planning_trace.raw_intents) if planning_trace is not None else None),
                normalized_intents=(dict(planning_trace.normalized_intents) if planning_trace is not None else None),
                repairs=(
                    [item.model_dump(mode="json") for item in planning_trace.repairs]
                    if planning_trace is not None
                    else []
                ),
            )
        failure_text = (
            "标准任务计划未通过程序校验：这是内部规划契约错误，不是你缺少对象" "或条件；本轮没有调用任何数据工具。"
        )
        controller.append_text(failure_text)
        if state is not None:
            state["assistant_text"] = failure_text
            state["_run_status"] = "failed"
            state["_run_error_code"] = AgentErrorCode.PLANNER_SCHEMA_INVALID.value
            controller.assistant_text_snapshot = failure_text
        return failure_text
    except Exception:
        logger.exception("[TaskPlanner] failed closed")
        failure_text = (
            "标准任务编排发生内部异常，本轮没有调用任何数据工具。" "这不是用户条件缺失，系统已按失败关闭处理。"
        )
        controller.append_text(failure_text)
        if state is not None:
            state["assistant_text"] = failure_text
            state["_run_status"] = "failed"
            controller.assistant_text_snapshot = failure_text
        return failure_text

    assert planning_trace is not None
    planned_goal = (
        graph_v2.outline.goal
        if graph_v2 is not None
        else IntentOutlineV2.model_validate(planning_trace.normalized_outline).goal
    )
    if plan.needs_clarification:
        clarification = plan.clarification_question or "请补充本轮要执行的对象或条件。"
        controller.append_text(clarification)
        if state is not None:
            state["assistant_text"] = clarification
            controller.assistant_text_snapshot = clarification
        return clarification

    return StandardPlanningResult(
        active_run_id=active_run_id,
        latest_user_text=latest_user_text,
        request_fingerprint=request_fingerprint,
        stage_durations_ms=v2_stage_durations_ms,
        current_entities=current_entities,
        context_v2=context_v2,
        compiled_v2=compiled_v2,
        graph_v2=graph_v2,
        planning_trace=planning_trace,
        artifact_map=artifact_map,
        plan=plan,
        resolved_tasks=resolved_tasks,
        planned_goal=planned_goal,
        emit_v2_stage=emit_v2_stage,
        guarded_model_completion=guarded_model_completion,
        stream_structured_completion=stream_structured_completion,
    )


__all__ = ["StandardPlanningResult", "plan_standard_task"]
