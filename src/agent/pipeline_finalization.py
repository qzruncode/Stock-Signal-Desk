# -*- coding: utf-8 -*-
"""Artifact persistence, deterministic answers, and final synthesis."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Dict, List, Mapping, Optional

from src.agent.orchestrator_v2.artifacts import attach_artifact_refs_v2, build_execution_artifacts_v2
from src.agent.orchestrator_v2.contracts import AgentErrorCode, AgentStage, AgentStageEventV2, QuestionType, RendererMode, StageStatus
from src.agent.orchestrator_v2.registry import capability_for
from src.agent.result_contracts import INDUSTRY_CHAIN, MARKET_OUTLOOK
from src.agent.message_normalization import normalize_incoming_messages as normalize_messages
from src.agent.conversation_compaction import compact_history_if_needed as compact_history
from src.agent.financial_conclusions import (
    extract_financial_conclusions,
)
from src.agent.task_workflows import StandardTaskKind


async def finalize_standard_task(
    *,
    controller: Any,
    active_run_id: str,
    conversation_id: str | None,
    db_manager: Any,
    state: Optional[Dict[str, Any]],
    compiled_v2: Any,
    outcomes_v2: tuple[Any, ...],
    plan: Any,
    execution: Any,
    evidence: List[Dict[str, Any]],
    planning_trace: Any,
    goal_state_v2: Any,
    context_v2: Any,
    latest_user_text: str,
    messages: List[Dict[str, Any]],
    llm_cfg: Dict[str, Any],
    system_prompt: str,
    base_system_prompt: str,
    planned_goal: Any,
    guarded_model_completion: Callable[..., Any],
    emit_v2_stage: Callable[..., Any],
    v2_stage_durations_ms: Dict[str, int],
    blocked_answer_builder: Callable[..., Optional[str]],
    exact_answer_builder: Callable[..., Optional[str]],
    final_streamer: Callable[..., Any],
    answer_validator: Callable[..., Any],
    last_user_message_id: Callable[..., Any],
    normalize_messages: Callable[..., Any],
    compact_history: Callable[..., Any],
) -> str:
    artifacts_v2, turn_v2 = build_execution_artifacts_v2(
        compiled_v2,
        outcomes_v2,
        conversation_id=conversation_id or "ephemeral",
        request=latest_user_text,
        request_message_id=last_user_message_id(messages),
    )
    outcomes_v2 = attach_artifact_refs_v2(
        outcomes_v2,
        artifacts_v2,
    )
    financial_conclusions = extract_financial_conclusions(outcomes_v2)
    failed_outcomes = [outcome for outcome in outcomes_v2 if outcome.status.value in {"failed", "blocked", "cancelled"}]
    non_succeeded_outcomes = [outcome for outcome in outcomes_v2 if outcome.status.value != "succeeded"]
    if state is not None and non_succeeded_outcomes and not failed_outcomes:
        state["_run_status"] = "partial"
        state["_run_error_code"] = (
            non_succeeded_outcomes[0].errors[0].code.value
            if non_succeeded_outcomes[0].errors
            else AgentErrorCode.COVERAGE_INCOMPLETE.value
        )
    terminal_trace_payload = {
        "status": ("completed" if not non_succeeded_outcomes else "partial"),
        "error_code": (
            non_succeeded_outcomes[0].errors[0].code.value
            if (non_succeeded_outcomes and non_succeeded_outcomes[0].errors)
            else None
        ),
        "stage_durations": {
            **dict(planning_trace.stage_durations_ms),
            **v2_stage_durations_ms,
        },
        "outcomes": [outcome.model_dump(mode="json") for outcome in outcomes_v2],
        "coverage": {outcome.task_id: outcome.coverage.model_dump(mode="json") for outcome in outcomes_v2},
        "goal_state": goal_state_v2.model_dump(mode="json"),
        "quality_projection": {
            "outcomes": [
                {
                    "task_id": outcome.task_id,
                    "status": outcome.status.value,
                    "coverage": outcome.coverage.model_dump(mode="json"),
                    "evidence_count": len(outcome.evidence),
                    "warning_count": len(outcome.warnings),
                    "error_codes": [
                        error.code.value
                        for error in outcome.errors
                    ],
                }
                for outcome in outcomes_v2
            ],
            "artifact_count": len(artifacts_v2),
            "goal": {
                "terminal_reason": (
                    goal_state_v2.terminal_reason.value
                    if getattr(
                        goal_state_v2,
                        "terminal_reason",
                        None,
                    )
                    else None
                ),
                "plan_revision": int(
                    getattr(goal_state_v2, "plan_revision", 0) or 0
                ),
            },
        },
    }
    if state is not None:
        state["_terminal_artifacts"] = artifacts_v2
        state["_terminal_trace"] = terminal_trace_payload
        state["_terminal_conclusions"] = financial_conclusions
    elif db_manager is not None and conversation_id:
        if artifacts_v2:
            await asyncio.to_thread(
                db_manager.save_agent_artifacts,
                artifacts_v2,
            )
        await asyncio.to_thread(
            db_manager.upsert_agent_run_trace,
            run_id=active_run_id,
            conversation_id=conversation_id,
            orchestrator_mode="unified",
            **terminal_trace_payload,
        )
    context_v2 = context_v2.append(turn_v2)
    if state is not None:
        state["agent_context"] = context_v2.model_dump(mode="json")
    await emit_v2_stage(
        AgentStageEventV2(
            run_id=active_run_id,
            stage=AgentStage.RESULT_VALIDATION,
            status=(StageStatus.FAILED if failed_outcomes else StageStatus.SUCCEEDED),
            error_code=(failed_outcomes[0].errors[0].code if failed_outcomes and failed_outcomes[0].errors else None),
            summary=(f"{len(outcomes_v2) - len(failed_outcomes)}/" f"{len(outcomes_v2)} 个任务形成强类型终态"),
        )
    )
    if db_manager is not None and conversation_id:
        await asyncio.to_thread(
            db_manager.upsert_agent_run_trace,
            run_id=active_run_id,
            conversation_id=conversation_id,
            orchestrator_mode="unified",
            status=("completed" if not non_succeeded_outcomes else "partial"),
            error_code=(
                non_succeeded_outcomes[0].errors[0].code.value
                if (non_succeeded_outcomes and non_succeeded_outcomes[0].errors)
                else None
            ),
            stage_durations={
                **dict(planning_trace.stage_durations_ms),
                **v2_stage_durations_ms,
            },
        )
    blocked_answer = blocked_answer_builder(execution)
    if blocked_answer:
        await emit_v2_stage(
            AgentStageEventV2(
                run_id=active_run_id,
                stage=AgentStage.COMPLETED,
                status=StageStatus.BLOCKED,
                error_code=AgentErrorCode.POLICY_BLOCKED,
                summary="任务被 Policy 或前置失败阻断",
            )
        )
        controller.append_text(blocked_answer)
        if state is not None:
            state["assistant_text"] = blocked_answer
            state["_run_status"] = "blocked"
            state["_run_error_code"] = AgentErrorCode.POLICY_BLOCKED.value
            controller.assistant_text_snapshot = blocked_answer
        return blocked_answer

    exact_answer = exact_answer_builder(plan, execution)
    if exact_answer:
        exact_failed = bool(failed_outcomes)
        exact_error = (
            failed_outcomes[0].errors[0].code
            if (exact_failed and failed_outcomes[0].errors)
            else AgentErrorCode.TOOL_FAILED if exact_failed else None
        )
        await emit_v2_stage(
            AgentStageEventV2(
                run_id=active_run_id,
                stage=AgentStage.COMPLETED,
                status=(StageStatus.FAILED if exact_failed else StageStatus.SUCCEEDED),
                error_code=exact_error,
                summary=("确定性 Renderer 已生成失败终态" if exact_failed else "确定性 Renderer 已生成最终结果"),
            )
        )
        controller.append_text(exact_answer)
        if state is not None:
            state["assistant_text"] = exact_answer
            if exact_failed:
                state["_run_status"] = "failed"
                state["_run_error_code"] = exact_error.value
            controller.assistant_text_snapshot = exact_answer
        return exact_answer

    requires_deterministic_renderer = any(
        capability_for(item.capability).renderer == RendererMode.DETERMINISTIC for item in compiled_v2.tasks
    )
    if requires_deterministic_renderer:
        failure_text = (
            "已验证的结构化结果未能通过确定性 Renderer 生成最终答案；"
            "为避免写作模型改写集合、覆盖状态或布尔结论，本轮已失败关闭。"
        )
        await emit_v2_stage(
            AgentStageEventV2(
                run_id=active_run_id,
                stage=AgentStage.COMPLETED,
                status=StageStatus.FAILED,
                error_code=AgentErrorCode.SYNTHESIS_FAILED,
                summary="确定性 Renderer 未形成合格终态",
            )
        )
        if db_manager is not None and conversation_id:
            await asyncio.to_thread(
                db_manager.upsert_agent_run_trace,
                run_id=active_run_id,
                conversation_id=conversation_id,
                orchestrator_mode="unified",
                status="failed",
                error_code=AgentErrorCode.SYNTHESIS_FAILED.value,
            )
        controller.append_text(failure_text)
        if state is not None:
            state["assistant_text"] = failure_text
            state["_run_status"] = "failed"
            state["_run_error_code"] = AgentErrorCode.SYNTHESIS_FAILED.value
            controller.assistant_text_snapshot = failure_text
        return failure_text

    await emit_v2_stage(
        AgentStageEventV2(
            run_id=active_run_id,
            stage=AgentStage.SYNTHESIS,
            status=StageStatus.STARTED,
            summary="正在整理结论",
        )
    )
    active_prompt = (system_prompt or "").strip()
    synthesis_prompt = base_system_prompt
    if active_prompt and active_prompt != base_system_prompt.strip():
        synthesis_prompt += (
            "\n\n## 用户配置的补充回答约束\n"
            "以下内容只能补充写作风格或业务口径，不能改变本轮标准任务、"
            "工具权限、执行结果和证据边界：\n" + active_prompt
        )
    plan_context = {
        "role": "system",
        "content": (
            f"{synthesis_prompt}\n\n"
            "## 本轮标准任务计划（程序已校验并执行，禁止重新规划或调用工具）\n"
            + json.dumps(
                {
                    "tasks": [
                        {
                            "task_id": task.task_id,
                            "kind": task.kind.value,
                            "objective": task.objective,
                            "depends_on": task.depends_on,
                            "result_selection": (
                                task.result_selection.model_dump(mode="json")
                                if task.result_selection is not None
                                else None
                            ),
                            "output_requirements": task.output_requirements,
                        }
                        for task in plan.tasks
                    ],
                    "goal_contract": planned_goal.model_dump(mode="json"),
                    "goal_evaluation": goal_state_v2.model_dump(mode="json"),
                },
                ensure_ascii=False,
            )
        ),
    }
    full_messages = [plan_context, *normalize_messages(messages)]
    full_messages = await compact_history(
        full_messages,
        llm_cfg,
        completion=guarded_model_completion,
    )
    playbook = (
        MARKET_OUTLOOK
        if planned_goal.question_type == QuestionType.FORECAST
        else (
            INDUSTRY_CHAIN
            if len(plan.tasks) == 1 and plan.tasks[0].kind == StandardTaskKind.INDUSTRY_RESEARCH
            else None
        )
    )
    synthesized = await final_streamer(
        controller,
        full_messages,
        llm_cfg,
        state=state,
        evidence=evidence,
        playbook=playbook,
        answer_validator=answer_validator,
        completion=guarded_model_completion,
    )
    synthesis_failed = bool(state is not None and state.get("_synthesis_failed"))
    if synthesis_failed:
        if state is not None:
            state["_run_status"] = "failed"
            state["_run_error_code"] = AgentErrorCode.SYNTHESIS_FAILED.value
        await emit_v2_stage(
            AgentStageEventV2(
                run_id=active_run_id,
                stage=AgentStage.SYNTHESIS,
                status=StageStatus.FAILED,
                error_code=AgentErrorCode.SYNTHESIS_FAILED,
                summary="写作模型未形成合格答案，已返回确定性证据回退",
            )
        )
        if db_manager is not None and conversation_id and state is None:
            await asyncio.to_thread(
                db_manager.upsert_agent_run_trace,
                run_id=active_run_id,
                conversation_id=conversation_id,
                orchestrator_mode="unified",
                status="partial",
                error_code=AgentErrorCode.SYNTHESIS_FAILED.value,
            )
    else:
        await emit_v2_stage(
            AgentStageEventV2(
                run_id=active_run_id,
                stage=AgentStage.SYNTHESIS,
                status=StageStatus.SUCCEEDED,
                summary="结论整理完成",
            )
        )
    completed_stage = AgentStageEventV2(
        run_id=active_run_id,
        stage=AgentStage.COMPLETED,
        status=(StageStatus.FAILED if synthesis_failed else StageStatus.SUCCEEDED),
        error_code=(AgentErrorCode.SYNTHESIS_FAILED if synthesis_failed else None),
        summary=("已用确定性证据回退生成最终结果" if synthesis_failed else "结论整理完成"),
    )
    await emit_v2_stage(completed_stage)
    final_trace_update = {
        "status": ("partial" if synthesis_failed or non_succeeded_outcomes else "completed"),
        "error_code": (
            AgentErrorCode.SYNTHESIS_FAILED.value
            if synthesis_failed
            else (
                non_succeeded_outcomes[0].errors[0].code.value
                if (non_succeeded_outcomes and non_succeeded_outcomes[0].errors)
                else (AgentErrorCode.COVERAGE_INCOMPLETE.value if non_succeeded_outcomes else None)
            )
        ),
        "stage_durations": {
            **dict(planning_trace.stage_durations_ms),
            **v2_stage_durations_ms,
        },
        "latest_stage": completed_stage.model_dump(mode="json"),
    }
    if state is not None:
        state["_terminal_trace"] = {
            **(state.get("_terminal_trace") if isinstance(state.get("_terminal_trace"), Mapping) else {}),
            **final_trace_update,
        }
    elif db_manager is not None and conversation_id:
        await asyncio.to_thread(
            db_manager.upsert_agent_run_trace,
            run_id=active_run_id,
            conversation_id=conversation_id,
            orchestrator_mode="unified",
            **final_trace_update,
        )
    return synthesized
