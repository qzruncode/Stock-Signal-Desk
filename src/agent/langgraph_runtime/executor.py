"""Policy-approved execution of one atomic tool action."""

from __future__ import annotations

import asyncio
from datetime import datetime
from functools import partial
import hashlib
import json
import os
import threading
from typing import Any, Callable, Mapping

from src.agent.resource_scheduler import ResourceCapacityExceeded, agent_resource_lease
from src.agent.run_registry import active_run_registry
from src.agent.runtime_safety import get_agent_runtime_limits
from src.agent.tool_dispatch import ToolDispatcher, ToolDispatchOutcome, ToolDispatchRequest
from src.tools.base import ToolProgressUpdate
from src.tools.process_runner import execute_tool_isolated
from src.tools.registry import ToolRegistry

from .presentation import project_arguments_for_timeline, project_tool_result_for_timeline


ACTIVE_STEP_LEASE_SECONDS = 3_600.0


def _tool_resource_slots() -> int:
    """Return the capacity of one named tool resource pool.

    The resource key is ``tool:{tool_name}``, so this is deliberately named
    per-resource rather than global.  Keep the old environment variable as a
    compatibility fallback for existing deployments.
    """
    raw = os.getenv("AGENT_TOOL_RESOURCE_CONCURRENCY")
    if raw is None:
        raw = os.getenv("AGENT_TOOL_GLOBAL_CONCURRENCY", "8")
    try:
        return max(1, min(64, int(raw)))
    except (TypeError, ValueError):
        return 8


def action_fingerprint(
    *,
    run_id: str,
    action_id: str,
    tool_name: str,
    arguments: Mapping[str, Any],
) -> str:
    payload = {
        "run_id": run_id,
        "action_id": action_id,
        "tool_name": tool_name,
        "arguments": dict(arguments),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _source_refs(value: Any, *, depth: int = 0) -> list[str]:
    if depth > 8:
        return []
    refs: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = str(key).lower()
            if normalized_key in {
                "url",
                "source_url",
                "source",
                "sources",
                "provider",
                "data_source",
                "reference",
            }:
                if isinstance(item, str) and item.strip():
                    refs.append(item.strip())
                elif isinstance(item, (list, tuple, set)):
                    refs.extend(str(entry).strip() for entry in item if str(entry).strip())
                elif isinstance(item, Mapping):
                    refs.extend(_source_refs(item, depth=depth + 1))
            refs.extend(_source_refs(item, depth=depth + 1))
    elif isinstance(value, (list, tuple)):
        for item in value:
            refs.extend(_source_refs(item, depth=depth + 1))
    deduplicated: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        compact = str(ref).strip()[:2_000]
        if compact and compact not in seen:
            seen.add(compact)
            deduplicated.append(compact)
    return deduplicated[:30]


def _request_context(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Keep authored query context without prescribing entity field names.

    An atomic source can discover the relevant entities in its response.  A
    static list of argument keys therefore cannot be the sole entity context
    for a later answer-to-evidence reference check.
    """
    return {
        str(key): value
        for key, value in arguments.items()
        if value not in (None, "", [], {})
    }


def _error_code(error: BaseException) -> str:
    text = str(error).lower()
    name = type(error).__name__.lower()
    if "budget exceeded" in text:
        return "budget_exceeded"
    if "circuit is open" in text:
        return "circuit_open"
    if isinstance(error, ResourceCapacityExceeded):
        return "capacity_exceeded"
    if "timeout" in name or "timeout" in text:
        return "timeout"
    if any(marker in name or marker in text for marker in ("connect", "network", "rate limit", "ratelimit")):
        return "provider_unavailable"
    return "tool_failed"


def _retryable(error: BaseException) -> bool:
    return _error_code(error) in {
        "capacity_exceeded",
        "timeout",
        "provider_unavailable",
    }


class AtomicToolExecutor:
    """Execute an action after graph policy and approval checks have passed."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        database: Any | None,
        run_id: str,
        conversation_id: str,
        controller: Any | None,
        events: Any,
        compact_result: Callable[[str, Any], Any],
        attach_fallback: Callable[[str, dict[str, Any], Any], Any],
        isolated_executor: Callable[..., Any] = execute_tool_isolated,
    ) -> None:
        self.registry = registry
        self.database = database
        self.run_id = run_id
        self.conversation_id = conversation_id
        self.controller = controller
        self.events = events
        self.compact_result = compact_result
        self.attach_fallback = attach_fallback
        self.isolated_executor = isolated_executor

    def _presentation(self, tool_name: str, result: Any) -> dict[str, Any]:
        projected = self.compact_result(tool_name, result)
        if isinstance(projected, dict):
            return projected
        source = result if isinstance(result, Mapping) else {}
        return {
            "success": source.get("success", True),
            "result": projected,
            "partial": source.get("partial", False),
            "errors": list(source.get("errors") or []),
        }

    @staticmethod
    def _completed_stage_details(
        record: Mapping[str, Any],
        evidence: Mapping[str, Any] | None,
        *,
        source_id: str | None,
    ) -> dict[str, Any]:
        display_result = (
            dict(record.get("display_result") or {})
            if isinstance(record.get("display_result"), Mapping)
            else {}
        )
        return {
            "tool_name": record.get("tool_name"),
            "success": record.get("success") is True,
            "partial": bool(record.get("partial")),
            "reused": bool(record.get("reused")),
            "arguments": dict(record.get("display_arguments") or {}),
            "data_time": record.get("data_time"),
            "evidence_id": evidence.get("evidence_id") if evidence else None,
            "source_id": source_id,
            **display_result,
        }

    async def execute(
        self,
        action: Mapping[str, Any],
        *,
        approved: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        action_id = str(action.get("action_id") or "").strip()
        tool_name = str(action.get("tool_name") or "").strip()
        authored_arguments = action.get("arguments")
        if not action_id or not tool_name or not isinstance(authored_arguments, dict):
            raise ValueError("action requires action_id, tool_name, and object arguments")
        effect = self.registry.effect_for(tool_name, authored_arguments)
        if effect == "side_effect" and not approved:
            raise PermissionError(f"side-effect tool {tool_name} requires an approved interrupt")
        arguments = self.registry.validate_model_arguments(
            tool_name,
            dict(authored_arguments),
            approved=approved,
        )
        spec = self.registry.get_tool(tool_name)
        if spec is None:
            raise KeyError(f"Tool not found: {tool_name}")
        source_id = str(arguments.get("source_id") or "").strip() or None
        display_arguments = project_arguments_for_timeline(
            arguments,
            sensitive_fields=spec.sensitive_fields,
            server_controlled_fields=spec.server_controlled_fields,
        )
        fingerprint = action_fingerprint(
            run_id=self.run_id,
            action_id=action_id,
            tool_name=tool_name,
            arguments=authored_arguments,
        )
        tool_call_id = f"lg_{fingerprint[:24]}"
        tool_call = None
        if self.controller is not None:
            tool_call = await self.controller.add_tool_call(
                tool_name,
                tool_call_id=tool_call_id,
                parent_id=action_id,
            )
            tool_call.append_args_text(
                json.dumps(
                    {
                        key: value
                        for key, value in arguments.items()
                        if key not in set(spec.server_controlled_fields)
                    },
                    ensure_ascii=False,
                    default=str,
                )
            )
        self.events.stage(
            "tool",
            "started",
            f"执行原子工具 {tool_name}",
            action_id=action_id,
            tool_call_id=tool_call_id,
            details={
                "tool_name": tool_name,
                "effect": effect,
                "argument_keys": sorted(str(key) for key in arguments),
                "arguments": display_arguments,
                "source_id": source_id,
            },
        )

        max_attempts = 1 if effect == "side_effect" else max(1, int(spec.max_attempts))
        idempotency_key = fingerprint
        if self.database is not None and effect == "side_effect":
            outbox = await asyncio.to_thread(
                self.database.upsert_effect_outbox,
                idempotency_key=idempotency_key,
                run_id=self.run_id,
                tool_name=tool_name,
                payload=arguments,
            )
            if outbox.get("status") == "completed":
                reused = outbox.get("result")
                canonical = reused if isinstance(reused, dict) else {"success": True, "result": reused}
                presentation = self._presentation(tool_name, canonical)
                if tool_call is not None:
                    tool_call.set_response(presentation, is_error=presentation.get("success") is False)
                record, evidence = self._result_record(
                    action_id=action_id,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    arguments=authored_arguments,
                    effect=effect,
                    fingerprint=fingerprint,
                    result=canonical,
                    reused=True,
                )
                self.events.stage(
                    "tool",
                    "completed" if record["success"] else "failed",
                    f"{tool_name} 已复用幂等结果",
                    action_id=action_id,
                    tool_call_id=tool_call_id,
                    error_code=None if record["success"] else "tool_failed",
                    details=self._completed_stage_details(record, evidence, source_id=source_id),
                )
                return record, evidence

        attempt = 0
        last_error: BaseException | None = None
        while attempt < max_attempts:
            claim: dict[str, Any] = {"action": "execute", "attempt": attempt + 1}
            if self.database is not None:
                claim = await asyncio.to_thread(
                    self.database.claim_agent_step,
                    idempotency_key=idempotency_key,
                    run_id=self.run_id,
                    conversation_id=self.conversation_id,
                    task_id="agent_loop",
                    step_id=action_id,
                    tool_name=tool_name,
                    effect=effect,
                    arguments=arguments,
                    worker_id=active_run_registry.worker_id,
                    lease_seconds=ACTIVE_STEP_LEASE_SECONDS,
                    max_attempts=max_attempts,
                )
            claim_action = str(claim.get("action") or "execute")
            if claim_action == "wait":
                await asyncio.sleep(0.2)
                continue
            if claim_action == "reuse":
                canonical = claim.get("result")
                if not isinstance(canonical, dict):
                    canonical = {"success": True, "result": canonical}
                presentation = self._presentation(tool_name, canonical)
                if tool_call is not None:
                    tool_call.set_response(presentation, is_error=presentation.get("success") is False)
                record, evidence = self._result_record(
                    action_id=action_id,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    arguments=authored_arguments,
                    effect=effect,
                    fingerprint=fingerprint,
                    result=canonical,
                    reused=True,
                )
                self.events.stage(
                    "tool",
                    "completed" if record["success"] else "failed",
                    f"{tool_name} 已复用幂等结果",
                    action_id=action_id,
                    tool_call_id=tool_call_id,
                    error_code=None if record["success"] else "tool_failed",
                    details=self._completed_stage_details(record, evidence, source_id=source_id),
                )
                return record, evidence
            if claim_action == "exhausted":
                last_error = RuntimeError(str(claim.get("error_detail") or "tool attempts exhausted"))
                break

            attempt = int(claim.get("attempt") or attempt + 1)
            cancel_event = threading.Event()
            loop = asyncio.get_running_loop()

            def progress(update: ToolProgressUpdate) -> None:
                if update.reasoning_delta:
                    loop.call_soon_threadsafe(self.events.reasoning, update.reasoning_delta)
                    return
                suffix = f"（{update.progress}%）" if update.progress is not None else ""
                loop.call_soon_threadsafe(
                    partial(
                        self.events.stage,
                        "tool",
                        "started",
                        f"{tool_name}：{update.message}{suffix}",
                        action_id=action_id,
                        tool_call_id=tool_call_id,
                    )
                )

            dispatcher = ToolDispatcher(
                self.registry,
                isolated_executor=self.isolated_executor,
                compact_result=self.compact_result,
                attach_fallback=self.attach_fallback,
            )

            def dispatch() -> ToolDispatchOutcome:
                return dispatcher.execute(
                    ToolDispatchRequest(
                        tool_name=tool_name,
                        arguments=arguments,
                        idempotency_key=idempotency_key,
                        conversation_id=self.conversation_id,
                        run_id=self.run_id,
                        timeout_seconds=spec.timeout_seconds,
                        approved=approved,
                        # A source tool can block in a parser, driver or remote
                        # socket even when its own library timeout is ignored.
                        # Module-owned catalog tools run in the existing
                        # cancellable child-process boundary. A caller may
                        # inject an in-memory registry (for embedding or
                        # testing); that registry cannot be reconstructed by
                        # the worker, so the dispatcher keeps those calls in
                        # process while preserving the same policy checks.
                        # This does not impose a timeout on model reasoning.
                        force_isolation=True,
                    ),
                    cancel_event=cancel_event,
                    progress_observer=progress,
                )

            try:
                if self.database is not None:
                    budget = await asyncio.to_thread(
                        self.database.reserve_agent_run_budget,
                        self.run_id,
                        tool_calls=1,
                        max_tool_calls=get_agent_runtime_limits().max_tool_calls,
                    )
                    if not budget.get("allowed") and budget.get("reason") != "run_not_found":
                        raise RuntimeError(f"Agent run tool-call budget exceeded: {budget.get('reason')}")
                    circuit = await asyncio.to_thread(
                        self.database.agent_circuit_before_request,
                        f"tool:{tool_name}",
                        worker_id=active_run_registry.worker_id,
                    )
                    if not circuit.get("allowed"):
                        raise RuntimeError("tool circuit is open")
                async with agent_resource_lease(
                    self.database,
                    resource_name=f"tool:{tool_name}",
                    slots=_tool_resource_slots(),
                    lease_seconds=120.0,
                    run_id=self.run_id,
                    step_id=action_id,
                ):
                    outcome = await asyncio.to_thread(dispatch)
                canonical = outcome.canonical_result
                presentation = outcome.presentation_result
                if self.database is not None:
                    finished = await asyncio.to_thread(
                        self.database.finish_agent_step,
                        idempotency_key,
                        result=canonical,
                        worker_id=active_run_registry.worker_id,
                        attempt=attempt,
                    )
                    if not finished:
                        raise RuntimeError("durable tool-step lease was lost")
                    if effect == "side_effect":
                        await asyncio.to_thread(
                            self.database.complete_effect_outbox,
                            idempotency_key,
                            result=canonical,
                        )
                    await asyncio.to_thread(
                        self.database.record_agent_circuit_success,
                        f"tool:{tool_name}",
                    )
                if tool_call is not None:
                    tool_call.set_response(presentation, is_error=presentation.get("success") is False)
                record, evidence = self._result_record(
                    action_id=action_id,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    arguments=authored_arguments,
                    effect=effect,
                    fingerprint=fingerprint,
                    result=canonical,
                    reused=False,
                )
                self.events.stage(
                    "tool",
                    "completed" if record["success"] else "failed",
                    f"{tool_name} 已返回结果",
                    action_id=action_id,
                    tool_call_id=tool_call_id,
                    error_code=(None if record["success"] else "tool_failed"),
                    details=self._completed_stage_details(record, evidence, source_id=source_id),
                )
                return record, evidence
            except asyncio.CancelledError:
                cancel_event.set()
                raise
            except BaseException as exc:
                cancel_event.set()
                last_error = exc
                retry = effect == "read" and _retryable(exc) and attempt < max_attempts
                if self.database is not None:
                    await asyncio.to_thread(
                        self.database.fail_agent_step,
                        idempotency_key,
                        error_code=_error_code(exc),
                        error_detail=f"{type(exc).__name__}: {exc}",
                        retryable=retry,
                        worker_id=active_run_registry.worker_id,
                        attempt=attempt,
                    )
                    await asyncio.to_thread(
                        self.database.record_agent_circuit_failure,
                        f"tool:{tool_name}",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                if retry:
                    await asyncio.sleep(max(0.0, float(spec.retry_backoff_seconds)) * (2 ** (attempt - 1)))
                    continue
                break

        assert last_error is not None
        failure = {
            "success": False,
            "partial": False,
            "errors": [f"{type(last_error).__name__}: {last_error}"],
            "error_code": _error_code(last_error),
            "data_time": None,
            "freshness_unknown": True,
            "is_stale": None,
        }
        if tool_call is not None:
            tool_call.set_response(failure, is_error=True)
        record, _ = self._result_record(
            action_id=action_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            arguments=authored_arguments,
            effect=effect,
            fingerprint=fingerprint,
            result=failure,
            reused=False,
        )
        self.events.stage(
            "tool",
            "failed",
            f"{tool_name} 执行失败",
            action_id=action_id,
            tool_call_id=tool_call_id,
            error_code=_error_code(last_error),
            details={
                "tool_name": tool_name,
                "success": False,
                "error_code": _error_code(last_error),
                "errors": failure["errors"],
                "arguments": record.get("display_arguments") or display_arguments,
                "source_id": source_id,
            },
        )
        return record, None

    async def execute_native_read(
        self,
        action: Mapping[str, Any],
        *,
        approved: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Execute one read through LangGraph's native tool handler.

        This path intentionally keeps the application-owned result, evidence,
        isolation, timeout and retry contracts, but does not enter the durable
        tool-step claim or DB resource-slot scheduler.  LangGraph already
        created one task per ``Send`` call; reads should remain independent
        tasks unless the tool itself requires a stronger resource policy.
        """
        action_id = str(action.get("action_id") or "").strip()
        tool_name = str(action.get("tool_name") or "").strip()
        authored_arguments = action.get("arguments")
        if not action_id or not tool_name or not isinstance(authored_arguments, dict):
            raise ValueError("action requires action_id, tool_name, and object arguments")
        effect = self.registry.effect_for(tool_name, authored_arguments)
        if effect != "read":
            raise ValueError(f"native handler only supports read tools: {tool_name}")
        if approved:
            raise ValueError("read tools cannot be marked as approved side effects")
        arguments = self.registry.validate_model_arguments(
            tool_name,
            dict(authored_arguments),
            approved=False,
        )
        spec = self.registry.get_tool(tool_name)
        if spec is None:
            raise KeyError(f"Tool not found: {tool_name}")
        source_id = str(arguments.get("source_id") or "").strip() or None
        display_arguments = project_arguments_for_timeline(
            arguments,
            sensitive_fields=spec.sensitive_fields,
            server_controlled_fields=spec.server_controlled_fields,
        )
        fingerprint = action_fingerprint(
            run_id=self.run_id,
            action_id=action_id,
            tool_name=tool_name,
            arguments=authored_arguments,
        )
        tool_call_id = f"lg_{fingerprint[:24]}"
        tool_call = None
        if self.controller is not None:
            tool_call = await self.controller.add_tool_call(
                tool_name,
                tool_call_id=tool_call_id,
                parent_id=action_id,
            )
            tool_call.append_args_text(
                json.dumps(
                    {
                        key: value
                        for key, value in arguments.items()
                        if key not in set(spec.server_controlled_fields)
                    },
                    ensure_ascii=False,
                    default=str,
                )
            )
        self.events.stage(
            "tool",
            "started",
            f"通过 LangGraph 原生 handler 执行只读工具 {tool_name}",
            action_id=action_id,
            tool_call_id=tool_call_id,
            details={
                "tool_name": tool_name,
                "effect": "read",
                "execution_path": "langgraph_tool_handler",
                "argument_keys": sorted(str(key) for key in arguments),
                "arguments": display_arguments,
                "source_id": source_id,
            },
        )

        max_attempts = max(1, int(spec.max_attempts))
        last_error: BaseException | None = None
        for attempt in range(1, max_attempts + 1):
            cancel_event = threading.Event()
            loop = asyncio.get_running_loop()

            def progress(update: ToolProgressUpdate) -> None:
                if update.reasoning_delta:
                    loop.call_soon_threadsafe(self.events.reasoning, update.reasoning_delta)
                    return
                suffix = f"（{update.progress}%）" if update.progress is not None else ""
                loop.call_soon_threadsafe(
                    partial(
                        self.events.stage,
                        "tool",
                        "started",
                        f"{tool_name}：{update.message}{suffix}",
                        action_id=action_id,
                        tool_call_id=tool_call_id,
                    )
                )

            dispatcher = ToolDispatcher(
                self.registry,
                isolated_executor=self.isolated_executor,
                compact_result=self.compact_result,
                attach_fallback=self.attach_fallback,
            )

            def dispatch() -> ToolDispatchOutcome:
                return dispatcher.execute(
                    ToolDispatchRequest(
                        tool_name=tool_name,
                        arguments=arguments,
                        idempotency_key=fingerprint,
                        conversation_id=self.conversation_id,
                        run_id=self.run_id,
                        timeout_seconds=spec.timeout_seconds,
                        approved=False,
                        force_isolation=True,
                    ),
                    cancel_event=cancel_event,
                    progress_observer=progress,
                )

            try:
                if self.database is not None:
                    budget = await asyncio.to_thread(
                        self.database.reserve_agent_run_budget,
                        self.run_id,
                        tool_calls=1,
                        max_tool_calls=get_agent_runtime_limits().max_tool_calls,
                    )
                    if not budget.get("allowed") and budget.get("reason") != "run_not_found":
                        raise RuntimeError(f"Agent run tool-call budget exceeded: {budget.get('reason')}")
                    circuit = await asyncio.to_thread(
                        self.database.agent_circuit_before_request,
                        f"tool:{tool_name}",
                        worker_id=active_run_registry.worker_id,
                    )
                    if not circuit.get("allowed"):
                        raise RuntimeError("tool circuit is open")
                outcome = await asyncio.to_thread(dispatch)
                canonical = outcome.canonical_result
                presentation = outcome.presentation_result
                if self.database is not None:
                    await asyncio.to_thread(
                        self.database.record_agent_circuit_success,
                        f"tool:{tool_name}",
                    )
                if tool_call is not None:
                    tool_call.set_response(presentation, is_error=presentation.get("success") is False)
                record, evidence = self._result_record(
                    action_id=action_id,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    arguments=authored_arguments,
                    effect="read",
                    fingerprint=fingerprint,
                    result=canonical,
                    reused=False,
                )
                self.events.stage(
                    "tool",
                    "completed" if record["success"] else "failed",
                    f"{tool_name} 已通过 LangGraph 原生 handler 返回结果",
                    action_id=action_id,
                    tool_call_id=tool_call_id,
                    error_code=None if record["success"] else "tool_failed",
                    details={
                        **self._completed_stage_details(record, evidence, source_id=source_id),
                        "execution_path": "langgraph_tool_handler",
                    },
                )
                return record, evidence
            except asyncio.CancelledError:
                cancel_event.set()
                raise
            except BaseException as exc:
                cancel_event.set()
                last_error = exc
                if self.database is not None:
                    await asyncio.to_thread(
                        self.database.record_agent_circuit_failure,
                        f"tool:{tool_name}",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                if _retryable(exc) and attempt < max_attempts:
                    await asyncio.sleep(max(0.0, float(spec.retry_backoff_seconds)) * (2 ** (attempt - 1)))
                    continue
                break

        assert last_error is not None
        failure = {
            "success": False,
            "partial": False,
            "errors": [f"{type(last_error).__name__}: {last_error}"],
            "error_code": _error_code(last_error),
            "data_time": None,
            "freshness_unknown": True,
            "is_stale": None,
        }
        if tool_call is not None:
            tool_call.set_response(failure, is_error=True)
        record, _ = self._result_record(
            action_id=action_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            arguments=authored_arguments,
            effect="read",
            fingerprint=fingerprint,
            result=failure,
            reused=False,
        )
        self.events.stage(
            "tool",
            "failed",
            f"{tool_name} 通过 LangGraph 原生 handler 执行失败",
            action_id=action_id,
            tool_call_id=tool_call_id,
            error_code=_error_code(last_error),
            details={
                "tool_name": tool_name,
                "execution_path": "langgraph_tool_handler",
                "success": False,
                "error_code": _error_code(last_error),
                "errors": failure["errors"],
                "arguments": display_arguments,
                "source_id": source_id,
            },
        )
        return record, None

    def _result_record(
        self,
        *,
        action_id: str,
        tool_name: str,
        tool_call_id: str,
        arguments: Mapping[str, Any],
        effect: str,
        fingerprint: str,
        result: Mapping[str, Any],
        reused: bool,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        success = result.get("success") is not False
        now = datetime.now().astimezone().isoformat()
        source_refs = _source_refs(result)
        spec = self.registry.get_tool(tool_name)
        display_arguments = project_arguments_for_timeline(
            arguments,
            sensitive_fields=(spec.sensitive_fields if spec else ()),
            server_controlled_fields=(spec.server_controlled_fields if spec else ("confirmed",)),
        )
        display_result = project_tool_result_for_timeline(result, source_refs=source_refs)
        record = {
            "id": action_id,
            "action_id": action_id,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "effect": effect,
            "fingerprint": fingerprint,
            "arguments": dict(arguments),
            "display_arguments": display_arguments,
            "success": success,
            "partial": bool(result.get("partial")),
            "result": dict(result),
            "display_result": display_result,
            "errors": list(result.get("errors") or []),
            "error_code": result.get("error_code"),
            "data_time": result.get("data_time"),
            "data_time_applicable": result.get("data_time_applicable", True),
            "data_time_provenance": result.get("data_time_provenance"),
            "data_time_note": result.get("data_time_note"),
            "is_stale": result.get("is_stale"),
            "freshness_unknown": bool(result.get("freshness_unknown", result.get("data_time") is None)),
            "source_refs": source_refs,
            "reused": reused,
            "completed_at": now,
        }
        if isinstance(result.get("content_access"), Mapping):
            record["content_access"] = dict(result["content_access"])
        if not success:
            return record, None
        evidence_id = f"ev_{fingerprint[:20]}"
        evidence = {
            "id": evidence_id,
            "evidence_id": evidence_id,
            "action_id": action_id,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "effect": effect,
            "success": True,
            "partial": bool(result.get("partial")),
            "entities": _request_context(arguments),
            "data_time": result.get("data_time"),
            "data_time_applicable": result.get("data_time_applicable", True),
            "data_time_provenance": result.get("data_time_provenance"),
            "data_time_note": result.get("data_time_note"),
            "is_stale": result.get("is_stale"),
            "freshness_unknown": bool(result.get("freshness_unknown", result.get("data_time") is None)),
            "source_refs": source_refs or [f"tool:{tool_name}"],
            "result": dict(result),
            "observed_at": now,
        }
        return record, evidence


__all__ = ["AtomicToolExecutor", "action_fingerprint"]
