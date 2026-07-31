# -*- coding: utf-8 -*-
"""Runtime observers used by the standard-task executor."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping

from src.agent.orchestrator_v2.contracts import AgentErrorCode, AgentStage, AgentStageEventV2, StageStatus
from src.agent.result_processors import process_task_result
from src.agent.task_workflows import StandardTaskKind


def build_result_processor_runner(
    *,
    active_run_id: str,
    emit_v2_stage: Callable[..., Any],
    guarded_model_completion: Callable[..., Any],
    stream_structured_completion: Callable[..., Any],
) -> Callable[..., Any]:
    async def run_result_processor(
        processor_name: str,
        task,
        processor_evidence: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        async def report_company_progress(
            completed: int,
            total: int,
            company_result: Dict[str, Any],
        ) -> None:
            verdict_labels = {
                "pass": "通过",
                "fail": "不符合",
                "insufficient": "证据不足",
                "error": "分析错误",
            }
            company_name = str(company_result.get("company_name") or company_result.get("symbol") or "").strip()
            verdict = verdict_labels.get(
                str(company_result.get("verdict") or ""),
                "已完成",
            )
            detail = f"；刚完成：{company_name}（{verdict}）" if company_name else ""
            await emit_v2_stage(
                AgentStageEventV2(
                    run_id=active_run_id,
                    stage=AgentStage.RESULT_VALIDATION,
                    status=StageStatus.STARTED,
                    task_id=task.task_id,
                    summary=f"逐股独立判断进度：{completed}/{total}{detail}",
                )
            )

        async def report_domain_progress(
            completed: int,
            total: int,
            detail: Dict[str, Any],
        ) -> None:
            raw_stage = str(detail.get("stage") or "")
            raw_status = str(detail.get("status") or "started")
            try:
                stage = AgentStage(raw_stage)
            except ValueError:
                stage = AgentStage.RESULT_VALIDATION
            try:
                status = StageStatus(raw_status)
            except ValueError:
                status = StageStatus.STARTED
            raw_error_code = str(detail.get("error_code") or "")
            try:
                error_code = AgentErrorCode(raw_error_code)
            except ValueError:
                error_code = None
            summary = str(detail.get("summary") or "").strip()
            if not summary:
                summary = f"实时板块有限集合选择进度：{completed}/{total}"
            await emit_v2_stage(
                AgentStageEventV2(
                    run_id=active_run_id,
                    stage=stage,
                    status=status,
                    task_id=task.task_id,
                    error_code=error_code,
                    summary=summary,
                )
            )

        processor_completion = (
            guarded_model_completion if processor_name == "company_evidence_binding" else stream_structured_completion
        )
        return await process_task_result(
            processor_name,
            task,
            processor_evidence,
            llm_cfg,
            # 高基数逐股处理保留每家公司终态进度，但不把上百个内部模型
            # token 流叠加到一个页面 reasoning part；最终结构化结果不受影响。
            completion=processor_completion,
            progress=(
                report_company_progress
                if processor_name == "company_evidence_binding"
                else report_domain_progress if processor_name == "ranked_domain_selection" else None
            ),
        )

    return run_result_processor


def build_workflow_outcome_observer(
    *,
    active_run_id: str,
    emit_v2_stage: Callable[..., Any],
) -> Callable[..., Any]:
    async def report_workflow_outcome(
        task,
        outcome,
        completed: int,
        total: int,
    ) -> None:
        result = outcome.result if isinstance(outcome.result, Mapping) else {}
        coverage_parts = [
            f"{key}={result[key]}" for key in ("requested_count", "covered_count") if result.get(key) is not None
        ]
        result_detail = "，" + "，".join(coverage_parts) if coverage_parts else ""
        outcome_label = (
            "已由共享首关形成终态"
            if outcome.success and not outcome.executed
            else "执行成功" if outcome.success else "执行失败"
        )
        await emit_v2_stage(
            AgentStageEventV2(
                run_id=active_run_id,
                stage=AgentStage.EXECUTION,
                status=StageStatus.STARTED,
                task_id=task.task_id,
                summary=(
                    f"{outcome.tool_name}/{outcome.step_id} "
                    f"{outcome_label}，"
                    f"任务内进度 {completed}/{total}{result_detail}"
                ),
            )
        )
        if task.kind == StandardTaskKind.INVESTMENT_DECISION and outcome.step_id.startswith("professional_buy_"):
            stock_completed = buy_progress_counts.get(task.task_id, 0) + 1
            buy_progress_counts[task.task_id] = stock_completed
            stock_total = len(task.symbols)
            if stock_completed == 1 or stock_completed == stock_total or stock_completed % 5 == 0:
                symbol = str(outcome.arguments.get("symbols") or "").strip()
                state_label = (
                    "已由共享首关阻断"
                    if outcome.success and not outcome.executed
                    else "已形成终态" if outcome.success else "执行失败"
                )
                progress_summary = f"逐股八维判断进度：{stock_completed}/{stock_total}" + (
                    f"（{symbol} {state_label}）" if symbol else ""
                )
                await emit_v2_stage(
                    AgentStageEventV2(
                        run_id=active_run_id,
                        stage=AgentStage.EXECUTION,
                        status=StageStatus.STARTED,
                        task_id=task.task_id,
                        summary=progress_summary,
                    )
                )

    return report_workflow_outcome


__all__ = ["build_result_processor_runner", "build_workflow_outcome_observer"]
