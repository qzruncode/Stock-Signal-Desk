"""Policy-approved execution of one atomic tool action."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from functools import partial
import hashlib
import json
import os
import threading
from typing import Any, Callable, Mapping, Sequence

from src.agent.resource_scheduler import ResourceCapacityExceeded, agent_resource_lease
from src.agent.run_registry import active_run_registry
from src.agent.runtime_errors import emit_runtime_error
from src.agent.runtime_safety import get_agent_runtime_limits
from src.agent.tool_dispatch import ToolDispatcher, ToolDispatchOutcome, ToolDispatchRequest
from src.tools.base import ToolProgressUpdate, classify_result_semantics, tool_execution_context
from src.tools.process_runner import execute_tool_isolated
from src.tools.registry import ToolRegistry

from .presentation import project_arguments_for_timeline, project_tool_result_for_timeline


ACTIVE_STEP_LEASE_SECONDS = 3_600.0

_WEB_FALLBACK_CATEGORIES = frozenset(
    {
        "source_read",
        "source_search",
        "news_source",
        "research",
        "events",
        "macro",
        "market",
        "financials",
    }
)

_EXECUTION_EVENTS: ContextVar[Any | None] = ContextVar(
    "dsa_execution_events",
    default=None,
)
_EXECUTION_CONTROLLER: ContextVar[Any | None] = ContextVar(
    "dsa_execution_controller",
    default=None,
)


@contextmanager
def execution_event_scope(*, events: Any | None = None, controller: Any | None = None):
    """Temporarily route executor projections to a private child bridge."""
    events_token = _EXECUTION_EVENTS.set(events)
    controller_token = _EXECUTION_CONTROLLER.set(controller)
    try:
        yield
    finally:
        _EXECUTION_EVENTS.reset(events_token)
        _EXECUTION_CONTROLLER.reset(controller_token)


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


def _should_trip_tool_circuit(error: BaseException) -> bool:
    """Trip a tool circuit only for failures that indicate source unavailability."""
    return _error_code(error) in {"timeout", "provider_unavailable"}


def _web_fallback_eligible(spec: Any, effect: str) -> bool:
    if effect != "read" or spec is None:
        return False
    configured = getattr(spec, "web_fallback", None)
    if configured is not None:
        return bool(configured)
    return str(getattr(spec, "category", "") or "") in _WEB_FALLBACK_CATEGORIES


def _runtime_scope(events: Any, action: Mapping[str, Any]) -> tuple[str, str, str, str]:
    team_id = str(
        action.get("collaboration_id")
        or action.get("team_id")
        or getattr(events, "team_id", "")
        or ""
    ).strip()
    agent_id = str(action.get("agent_id") or getattr(events, "agent_id", "") or "").strip()
    task_id = str(action.get("task_id") or getattr(events, "task_id", "") or "").strip()
    event_scope = str(action.get("scope") or getattr(events, "scope", "") or "").strip().lower()
    scope = event_scope if event_scope in {"coordinator", "expert", "review"} else "expert" if team_id and (agent_id or task_id) else "coordinator"
    return scope, team_id, agent_id, task_id


class AtomicToolExecutor:
    """Execute an action after graph policy and approval checks have passed."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        database: Any | None,
        run_id: str,
        conversation_id: str,
        tenant_id: str = "local",
        owner_id: str = "admin",
        knowledge_base_ids: Sequence[str] = (),
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
        self.tenant_id = tenant_id
        self.owner_id = owner_id
        self.knowledge_base_ids = tuple(str(item) for item in knowledge_base_ids)
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
        event_sink = _EXECUTION_EVENTS.get() or self.events
        event_controller = _EXECUTION_CONTROLLER.get() or self.controller
        with tool_execution_context(
            conversation_id=self.conversation_id, run_id=self.run_id,
            tenant_id=self.tenant_id, owner_id=self.owner_id,
            knowledge_base_ids=self.knowledge_base_ids,
        ):
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
        scope, collaboration_id, agent_id, task_id = _runtime_scope(event_sink, action)
        fallback_eligible = _web_fallback_eligible(spec, effect)
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
        if event_controller is not None:
            tool_call = await event_controller.add_tool_call(
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
        event_sink.stage(
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
                event_sink.stage(
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
        runtime_errors: list[dict[str, Any]] = []
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
                event_sink.stage(
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
                    loop.call_soon_threadsafe(event_sink.reasoning, update.reasoning_delta)
                    return
                suffix = f"（{update.progress}%）" if update.progress is not None else ""
                loop.call_soon_threadsafe(
                    partial(
                        event_sink.stage,
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
                        tenant_id=self.tenant_id,
                        owner_id=self.owner_id,
                        knowledge_base_ids=self.knowledge_base_ids,
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
                if runtime_errors:
                    record["runtime_errors"] = [dict(item) for item in runtime_errors]
                    record["recovered_from_runtime_error"] = True
                event_sink.stage(
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
                receipt = emit_runtime_error(
                    event_sink,
                    exc,
                    summary=f"{tool_name} 第 {attempt} 次调用失败，已记录运行时异常",
                    error_code=_error_code(exc),
                    failure_kind="timeout" if _error_code(exc) == "timeout" else "provider" if _error_code(exc) == "provider_unavailable" else "tool",
                    retryable=retry,
                    fallback_eligible=fallback_eligible,
                    fallback_status="pending" if fallback_eligible else "not_eligible",
                    terminal_impact="retrying" if retry else "recoverable",
                    run_id=self.run_id,
                    conversation_id=self.conversation_id,
                    collaboration_id=collaboration_id,
                    scope=scope,
                    agent_id=agent_id,
                    task_id=task_id,
                    node="atomic_tool_executor",
                    phase="tool",
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    action_id=action_id,
                    attempt=attempt,
                    details={
                        "arguments": display_arguments,
                        "effect": effect,
                        "tool_category": str(getattr(spec, "category", "") or ""),
                        "team_id": collaboration_id,
                        "task_id": task_id,
                        "agent_id": agent_id,
                    },
                )
                runtime_errors.append(receipt)
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
                    if _should_trip_tool_circuit(exc):
                        await asyncio.to_thread(
                            self.database.record_agent_circuit_failure,
                            f"tool:{tool_name}",
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    elif _error_code(exc) != "circuit_open":
                        await asyncio.to_thread(
                            self.database.record_agent_circuit_success,
                            f"tool:{tool_name}",
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
        if runtime_errors:
            failure["runtime_errors"] = [dict(item) for item in runtime_errors]
            failure["runtime_error"] = dict(runtime_errors[-1])
            failure["fallback_eligible"] = fallback_eligible
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
        if runtime_errors:
            record["runtime_errors"] = [dict(item) for item in runtime_errors]
            record["runtime_error"] = dict(runtime_errors[-1])
            record["fallback_eligible"] = fallback_eligible
        event_sink.stage(
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
                "errors": [runtime_errors[-1].get("message") or "tool execution failed"]
                if runtime_errors
                else failure["errors"],
                "arguments": record.get("display_arguments") or display_arguments,
                "source_id": source_id,
                "runtime_error_id": runtime_errors[-1].get("error_id") if runtime_errors else None,
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
        # The result contract requires an explicit boolean.  Treat anything
        # other than ``True`` as a failed execution so an incomplete/malformed
        # payload can never mint an evidence id.
        success = result.get("success") is True
        now = datetime.now().astimezone().isoformat()
        source_refs = _source_refs(result)
        semantics = classify_result_semantics(result)
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
            "has_data": bool(semantics["has_data"]),
            "data_status": str(semantics["data_status"]),
            "usable": bool(semantics["usable"]),
            "evidence_eligible": bool(
                semantics["evidence_eligible"] and effect == "read"
            ),
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
        if not success or not semantics["evidence_eligible"] or effect != "read":
            return record, None
        evidence_id = f"ev_{fingerprint[:20]}"
        evidence = {
            "id": evidence_id,
            "evidence_id": evidence_id,
            "action_id": action_id,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "effect": effect,
            "success": success,
            "partial": bool(result.get("partial")),
            "has_data": bool(semantics["has_data"]),
            "data_status": str(semantics["data_status"]),
            "usable": bool(semantics["usable"]),
            "evidence_eligible": bool(
                semantics["evidence_eligible"] and effect == "read"
            ),
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
