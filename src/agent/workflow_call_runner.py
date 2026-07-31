# -*- coding: utf-8 -*-
"""Durable execution of one compiled workflow call."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from typing import Any, Dict, List

from api.v1.endpoints.agent.chat_reasoning import _append_model_reasoning, _trace_json_preview
from api.v1.endpoints.agent.tools import _compact_tool_result, _maybe_attach_search_fallback
from src.agent.orchestrator_v2.contracts import AgentStage, AgentStageEventV2, EffectLevel, StageStatus, stable_fingerprint
from src.agent.orchestrator_v2.cache import execution_cache_key_v2, load_execution_cache_v2, save_execution_cache_v2
from src.agent.orchestrator_v2.runtime import compile_workflow_call_v2
from src.agent.resource_scheduler import ResourceCapacityExceeded, agent_resource_lease
from src.agent.runtime_safety import get_agent_runtime_limits, is_production_environment
from src.agent.run_registry import active_run_registry
from src.agent.tool_dispatch import ToolDispatcher, ToolDispatchRequest
from src.agent.task_workflows import WorkflowCall
from src.tools.base import ToolProgressUpdate
from src.tools.process_runner import execute_tool_isolated

logger = logging.getLogger(__name__)
ACTIVE_STEP_LEASE_SECONDS = 3_600.0

async def run_workflow_call(
    call: WorkflowCall,
    arguments: Dict[str, Any],
    *,
    controller: Any,
    active_run_id: str,
    conversation_id: str | None,
    db_manager: Any,
    llm_cfg: Dict[str, Any],
    v2_compiled_by_task: Dict[str, Any],
    emit_v2_stage: Any,
    registry: Any,
    heartbeat_seconds: float,
    isolated_executor: Any = execute_tool_isolated,
    compact_result: Any = _compact_tool_result,
    attach_fallback: Any = _maybe_attach_search_fallback,
) -> Dict[str, Any]:
    compiled_task = v2_compiled_by_task[call.task_id]
    compiled_call_v2 = compile_workflow_call_v2(
        compiled_task,
        call,
        arguments,
        registry=registry,
    )
    # Dynamic/resource-bound arguments must pass the same typed model as
    # the tool adapter before a tool card is exposed or any executor/cache
    # path can observe them.
    typed_arguments = compiled_call_v2.arguments.model_dump(mode="json")
    execution_policy = compiled_task.execution_policy
    # The step ledger protects crash/recovery within one durable run.
    # Cross-run read reuse belongs to the separate TTL cache; otherwise a
    # completed quote step could be replayed forever after its cache TTL.
    step_idempotency_key = stable_fingerprint(
        {
            "run_id": active_run_id,
            "compiled_key": compiled_call_v2.idempotency_key,
        }
    )
    await emit_v2_stage(
        AgentStageEventV2(
            run_id=active_run_id,
            stage=AgentStage.EXECUTION,
            status=StageStatus.STARTED,
            task_id=call.task_id,
            summary=(f"准备执行 {call.tool_name}/{call.step_id}，" f"参数={_trace_json_preview(typed_arguments)}"),
        )
    )
    call_id = f"workflow_{uuid.uuid4().hex}"
    tool = await controller.add_tool_call(call.tool_name, tool_call_id=call_id)
    tool.append_args_text(json.dumps(typed_arguments, ensure_ascii=False))
    cache_key = execution_cache_key_v2(
        compiled_task,
        call,
        typed_arguments,
        model_config=llm_cfg,
    )
    if (
        cache_key is not None
        and db_manager is not None
        and compiled_task.freshness_policy.max_age_seconds is not None
    ):
        cached_result = await asyncio.to_thread(
            load_execution_cache_v2,
            db_manager,
            cache_key,
            ttl_seconds=(compiled_task.freshness_policy.max_age_seconds),
        )
        if cached_result is not None:
            active_run_registry.record_execution_cache_result(hit=True)
            result = {**cached_result, "runtime_cache_hit": True}
            tool.set_response(
                result,
                is_error=result.get("success") is False,
            )
            return result
        active_run_registry.record_execution_cache_result(hit=False)
    if db_manager is not None:
        if execution_policy.effect != EffectLevel.READ:
            outbox = await asyncio.to_thread(
                db_manager.upsert_effect_outbox,
                idempotency_key=step_idempotency_key,
                run_id=active_run_id,
                tool_name=call.tool_name,
                payload=typed_arguments,
            )
            if outbox.get("status") == "completed":
                reused_effect = outbox.get("result")
                if not isinstance(reused_effect, dict):
                    reused_effect = {
                        "success": True,
                        "result": reused_effect,
                        "errors": [],
                        "partial": False,
                    }
                reused_effect = {
                    **reused_effect,
                    "idempotency_reused": True,
                }
                tool.set_response(
                    reused_effect,
                    is_error=reused_effect.get("success") is False,
                )
                return reused_effect
    started_at = time.monotonic()
    isolated_cancel_event = threading.Event()
    event_loop = asyncio.get_running_loop()
    reasoning_buffer: List[str] = []
    reasoning_buffer_chars = 0
    last_reasoning_flush = time.monotonic()
    reasoning_buffer_lock = threading.Lock()

    def flush_tool_reasoning() -> None:
        nonlocal reasoning_buffer_chars, last_reasoning_flush
        with reasoning_buffer_lock:
            if not reasoning_buffer:
                return
            text = "".join(reasoning_buffer)
            reasoning_buffer.clear()
            reasoning_buffer_chars = 0
            last_reasoning_flush = time.monotonic()
        event_loop.call_soon_threadsafe(
            _append_model_reasoning,
            controller,
            text,
        )

    def report_tool_update(update: ToolProgressUpdate) -> None:
        nonlocal reasoning_buffer_chars
        if update.reasoning_delta:
            with reasoning_buffer_lock:
                reasoning_buffer.append(update.reasoning_delta)
                reasoning_buffer_chars += len(update.reasoning_delta)
                should_flush = reasoning_buffer_chars >= 160 or time.monotonic() - last_reasoning_flush >= 0.1
            if should_flush:
                flush_tool_reasoning()
            return
        progress_suffix = f"（{update.progress}%）" if update.progress is not None else ""
        future = asyncio.run_coroutine_threadsafe(
            emit_v2_stage(
                AgentStageEventV2(
                    run_id=active_run_id,
                    stage=AgentStage.EXECUTION,
                    status=StageStatus.STARTED,
                    task_id=call.task_id,
                    summary=(f"{call.tool_name}/{call.step_id}：" f"{update.message}{progress_suffix}"),
                )
            ),
            event_loop,
        )
        try:
            future.result()
        except Exception as exc:
            logger.debug(
                "Tool progress observer stopped for task=%s step=%s: %s",
                call.task_id,
                call.step_id,
                exc,
            )

    def execute_sync() -> Dict[str, Any]:
        try:
            dispatcher = ToolDispatcher(
                registry,
                isolated_executor=isolated_executor,
                compact_result=compact_result,
                attach_fallback=attach_fallback,
            )
            return dispatcher.execute(
                ToolDispatchRequest(
                    tool_name=call.tool_name,
                    arguments=typed_arguments,
                    idempotency_key=step_idempotency_key,
                    force_isolation=(
                        is_production_environment()
                        or str(os.getenv("AGENT_ISOLATE_ALL_STATELESS") or "").strip().lower()
                        in {
                            "1",
                            "true",
                            "yes",
                            "on",
                        }
                    ),
                ),
                cancel_event=isolated_cancel_event,
                progress_observer=report_tool_update,
            )
        finally:
            flush_tool_reasoning()

    async def execute_with_heartbeat() -> Dict[str, Any]:
        worker = asyncio.create_task(asyncio.to_thread(execute_sync))
        heartbeat: asyncio.Task[None] | None = None
        try:
            while True:
                heartbeat = asyncio.create_task(
                    asyncio.sleep(heartbeat_seconds)
                )
                done, _pending = await asyncio.wait(
                    {worker, heartbeat},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if worker in done:
                    heartbeat.cancel()
                    await asyncio.gather(
                        heartbeat,
                        return_exceptions=True,
                    )
                    heartbeat = None
                    return await worker
                if db_manager is not None:
                    renewed = await asyncio.to_thread(
                        db_manager.renew_agent_step_lease,
                        step_idempotency_key,
                        worker_id=active_run_registry.worker_id,
                        attempt=attempt_number,
                        lease_seconds=ACTIVE_STEP_LEASE_SECONDS,
                    )
                    if not renewed:
                        isolated_cancel_event.set()
                        worker.cancel()
                        raise RuntimeError(
                            "durable step lease was lost during execution"
                        )
                await emit_v2_stage(
                    AgentStageEventV2(
                        run_id=active_run_id,
                        stage=AgentStage.EXECUTION,
                        status=StageStatus.STARTED,
                        task_id=call.task_id,
                        summary=(
                            f"{call.tool_name}/{call.step_id} 仍在执行，"
                            f"已运行 {int(time.monotonic() - started_at)} 秒"
                        ),
                    )
                )
        finally:
            if heartbeat is not None and not heartbeat.done():
                heartbeat.cancel()
                await asyncio.gather(
                    heartbeat,
                    return_exceptions=True,
                )
            if not worker.done():
                isolated_cancel_event.set()
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)

    attempt_number = 0
    last_exception: Exception | None = None
    while attempt_number < execution_policy.max_attempts:
        ledger_claim: Dict[str, Any] = {
            "action": "execute",
            "attempt": attempt_number + 1,
        }
        if db_manager is not None:
            ledger_claim = await asyncio.to_thread(
                db_manager.claim_agent_step,
                idempotency_key=step_idempotency_key,
                run_id=active_run_id,
                conversation_id=conversation_id or "",
                task_id=call.task_id,
                step_id=call.step_id,
                tool_name=call.tool_name,
                effect=execution_policy.effect.value,
                arguments=typed_arguments,
                worker_id=active_run_registry.worker_id,
                lease_seconds=ACTIVE_STEP_LEASE_SECONDS,
                max_attempts=execution_policy.max_attempts,
            )
        action = str(ledger_claim.get("action") or "execute")
        if action == "reuse":
            reused = ledger_claim.get("result")
            result = (
                reused
                if isinstance(reused, dict)
                else {
                    "success": True,
                    "result": reused,
                    "errors": [],
                    "partial": False,
                }
            )
            result = {**result, "idempotency_reused": True}
            tool.set_response(
                result,
                is_error=result.get("success") is False,
            )
            return result
        if action == "wait":
            await asyncio.sleep(0.2)
            continue
        if action == "exhausted":
            last_exception = RuntimeError(
                str(ledger_claim.get("error_detail") or "durable step attempts exhausted")
            )
            break

        attempt_number = int(ledger_claim.get("attempt") or attempt_number + 1)
        isolated_cancel_event.clear()
        try:
            if db_manager is not None:
                budget = await asyncio.to_thread(
                    db_manager.reserve_agent_run_budget,
                    active_run_id,
                    tool_calls=1,
                    max_tool_calls=get_agent_runtime_limits().max_plan_tool_calls,
                )
                if not budget.get("allowed"):
                    raise RuntimeError("Agent run tool-call budget exceeded: " f"{budget.get('reason')}")
                circuit = await asyncio.to_thread(
                    db_manager.agent_circuit_before_request,
                    f"tool:{call.tool_name}",
                    worker_id=active_run_registry.worker_id,
                )
                if not circuit.get("allowed"):
                    raise RuntimeError(
                        "tool circuit is open"
                        + (
                            f"; retry_after={circuit.get('retry_after_seconds')}"
                            if circuit.get("retry_after_seconds")
                            else ""
                        )
                    )
            try:
                configured_global_slots = int(os.getenv("AGENT_TOOL_GLOBAL_CONCURRENCY", "8"))
            except (TypeError, ValueError):
                configured_global_slots = 8
            global_slots = max(
                1,
                min(
                    64,
                    execution_policy.max_parallelism,
                    configured_global_slots,
                ),
            )
            async with agent_resource_lease(
                db_manager,
                resource_name=f"tool:{call.tool_name}",
                slots=global_slots,
                lease_seconds=120.0,
                run_id=active_run_id,
                step_id=call.step_id,
            ):
                result = await execute_with_heartbeat()
            succeeded = result.get("success") is not False
            if db_manager is not None:
                step_finished = await asyncio.to_thread(
                    db_manager.finish_agent_step,
                    step_idempotency_key,
                    result=result,
                    worker_id=active_run_registry.worker_id,
                    attempt=attempt_number,
                )
                if not step_finished:
                    raise RuntimeError("durable step lease was lost before completion")
                if execution_policy.effect != EffectLevel.READ:
                    await asyncio.to_thread(
                        db_manager.complete_effect_outbox,
                        step_idempotency_key,
                        result=result,
                    )
                await asyncio.to_thread(
                    db_manager.record_agent_circuit_success,
                    f"tool:{call.tool_name}",
                )
            tool.set_response(result, is_error=not succeeded)
            logger.info(
                "[WorkflowTool] task=%s step=%s tool=%s success=%s " "attempt=%s duration_ms=%d",
                call.task_id,
                call.step_id,
                call.tool_name,
                succeeded,
                attempt_number,
                int((time.monotonic() - started_at) * 1000),
            )
            if cache_key is not None and db_manager is not None:
                await asyncio.to_thread(
                    save_execution_cache_v2,
                    db_manager,
                    cache_key,
                    result,
                    freshness_policy=compiled_task.freshness_policy,
                )
            return result
        except asyncio.CancelledError:
            isolated_cancel_event.set()
            raise
        except Exception as exc:
            isolated_cancel_event.set()
            last_exception = exc
            error_name = type(exc).__name__.lower()
            error_code = (
                "budget_exceeded"
                if "budget exceeded" in str(exc).lower()
                else (
                    "circuit_open"
                    if "circuit is open" in str(exc).lower()
                    else (
                        "capacity_exceeded"
                        if isinstance(exc, ResourceCapacityExceeded)
                        else (
                            "timeout"
                            if isinstance(exc, TimeoutError)
                            else (
                                "connection_error"
                                if isinstance(exc, ConnectionError)
                                else (
                                    "provider_rate_limited" if "ratelimit" in error_name else "tool_process_crashed"
                                )
                            )
                        )
                    )
                )
            )
            retryable = (
                execution_policy.effect == EffectLevel.READ
                and error_code in execution_policy.retryable_error_codes
                and attempt_number < execution_policy.max_attempts
            )
            if db_manager is not None:
                await asyncio.to_thread(
                    db_manager.fail_agent_step,
                    step_idempotency_key,
                    error_code=error_code,
                    error_detail=f"{type(exc).__name__}: {exc}",
                    retryable=retryable,
                    worker_id=active_run_registry.worker_id,
                    attempt=attempt_number,
                )
                if error_code in {
                    "timeout",
                    "connection_error",
                    "provider_rate_limited",
                    "provider_unavailable",
                    "tool_process_crashed",
                }:
                    await asyncio.to_thread(
                        db_manager.record_agent_circuit_failure,
                        f"tool:{call.tool_name}",
                        error=f"{type(exc).__name__}: {exc}",
                    )
            logger.warning(
                "[WorkflowTool] task=%s step=%s tool=%s failed " "attempt=%s retryable=%s: %s",
                call.task_id,
                call.step_id,
                call.tool_name,
                attempt_number,
                retryable,
                exc,
            )
            if not retryable:
                break
            delay = min(
                30.0,
                execution_policy.retry_backoff_seconds
                * (execution_policy.retry_backoff_multiplier ** max(0, attempt_number - 1)),
            )
            if delay:
                await asyncio.sleep(delay)

    exc = last_exception or RuntimeError("tool execution attempts exhausted")
    error_text = f"工具执行失败：{type(exc).__name__}: {exc}"
    tool_spec = registry.get_tool(call.tool_name)
    if tool_spec is not None and tool_spec.failure_result is not None:
        result = tool_spec.failure_result(
            typed_arguments,
            error_text,
            max(1, attempt_number),
        )
        tool.set_response(result, is_error=True)
        return result
    result = {
        "success": False,
        "errors": [error_text],
        "partial": False,
    }
    tool.set_response(result, is_error=True)
    return result



__all__ = ["run_workflow_call"]
