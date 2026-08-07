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
from src.agent.runtime_safety import get_agent_runtime_limits, is_production_environment
from src.agent.tool_dispatch import ToolDispatcher, ToolDispatchOutcome, ToolDispatchRequest
from src.tools.base import ToolProgressUpdate
from src.tools.process_runner import execute_tool_isolated
from src.tools.registry import ToolRegistry


ACTIVE_STEP_LEASE_SECONDS = 3_600.0


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


def _entities(arguments: Mapping[str, Any]) -> dict[str, Any]:
    entity_keys = {
        "symbol",
        "symbols",
        "code",
        "codes",
        "stock_code",
        "stock_codes",
        "name",
        "names",
        "index_code",
        "sector",
        "industry",
        "query",
        "keyword",
        "keywords",
    }
    return {
        str(key): value
        for key, value in arguments.items()
        if str(key).lower() in entity_keys and value not in (None, "", [], {})
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
        fingerprint = action_fingerprint(
            run_id=self.run_id,
            action_id=action_id,
            tool_name=tool_name,
            arguments=authored_arguments,
        )
        tool_call = None
        if self.controller is not None:
            tool_call = await self.controller.add_tool_call(
                tool_name,
                tool_call_id=f"lg_{fingerprint[:24]}",
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
            "execute",
            "started",
            f"执行原子工具 {tool_name}",
            action_id=action_id,
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
                return self._result_record(
                    action_id=action_id,
                    tool_name=tool_name,
                    arguments=authored_arguments,
                    effect=effect,
                    fingerprint=fingerprint,
                    result=presentation,
                    reused=True,
                )

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
                    task_id="langgraph",
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
                return self._result_record(
                    action_id=action_id,
                    tool_name=tool_name,
                    arguments=authored_arguments,
                    effect=effect,
                    fingerprint=fingerprint,
                    result=presentation,
                    reused=True,
                )
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
                        "execute",
                        "started",
                        f"{tool_name}：{update.message}{suffix}",
                        action_id=action_id,
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
                        force_isolation=(
                            is_production_environment()
                            or str(os.getenv("AGENT_ISOLATE_ALL_STATELESS") or "").strip().lower()
                            in {"1", "true", "yes", "on"}
                        ),
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
                        max_tool_calls=get_agent_runtime_limits().max_plan_tool_calls,
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
                try:
                    slots = max(1, min(64, int(os.getenv("AGENT_TOOL_GLOBAL_CONCURRENCY", "8"))))
                except ValueError:
                    slots = 8
                async with agent_resource_lease(
                    self.database,
                    resource_name=f"tool:{tool_name}",
                    slots=slots,
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
                    arguments=authored_arguments,
                    effect=effect,
                    fingerprint=fingerprint,
                    result=presentation,
                    reused=False,
                )
                self.events.stage(
                    "execute",
                    "completed" if record["success"] else "failed",
                    f"{tool_name} 已返回结果",
                    action_id=action_id,
                    error_code=(None if record["success"] else "tool_failed"),
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
            arguments=authored_arguments,
            effect=effect,
            fingerprint=fingerprint,
            result=failure,
            reused=False,
        )
        self.events.stage(
            "execute",
            "failed",
            f"{tool_name} 执行失败",
            action_id=action_id,
            error_code=_error_code(last_error),
        )
        return record, None

    def _result_record(
        self,
        *,
        action_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        effect: str,
        fingerprint: str,
        result: Mapping[str, Any],
        reused: bool,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        success = result.get("success") is not False
        now = datetime.now().astimezone().isoformat()
        source_refs = _source_refs(result)
        record = {
            "id": action_id,
            "action_id": action_id,
            "tool_name": tool_name,
            "effect": effect,
            "fingerprint": fingerprint,
            "arguments": dict(arguments),
            "success": success,
            "partial": bool(result.get("partial")),
            "result": dict(result),
            "errors": list(result.get("errors") or []),
            "data_time": result.get("data_time"),
            "is_stale": result.get("is_stale"),
            "freshness_unknown": bool(result.get("freshness_unknown", result.get("data_time") is None)),
            "source_refs": source_refs,
            "reused": reused,
            "completed_at": now,
        }
        if not success:
            return record, None
        evidence_id = f"ev_{fingerprint[:20]}"
        evidence = {
            "id": evidence_id,
            "evidence_id": evidence_id,
            "action_id": action_id,
            "tool_name": tool_name,
            "success": True,
            "partial": bool(result.get("partial")),
            "entities": _entities(arguments),
            "data_time": result.get("data_time"),
            "is_stale": result.get("is_stale"),
            "freshness_unknown": bool(result.get("freshness_unknown", result.get("data_time") is None)),
            "source_refs": source_refs or [f"tool:{tool_name}"],
            "result": dict(result),
            "observed_at": now,
        }
        return record, evidence


__all__ = ["AtomicToolExecutor", "action_fingerprint"]
