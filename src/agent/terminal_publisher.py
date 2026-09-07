"""Atomic terminal projection for the LangGraph Agent runtime."""

from __future__ import annotations

import asyncio
import uuid
from src.services.research_archive import project_research_conclusions
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from src.agent.run_registry import ActiveRun, RunBroadcaster
from src.agent.langgraph_runtime.events import project_stage_history_for_client
from src.agent.langgraph_runtime.answer_contract import finalize_terminal_answer
from src.agent.langgraph_runtime.presentation import (
    enrich_execution_trace_with_result_previews,
    project_arguments_for_timeline,
    project_tool_result_for_timeline,
)
from src.agent.behavior_audit import (
    INSPECTION_SCHEMA_VERSION,
    describe_tool_access,
    describe_tool_outcome,
    describe_tool_quality,
)
from src.agent.langgraph_runtime.evidence_identity import prepare_answer_for_client
from src.tools.base import evidence_record_is_eligible
from src.services.chat_session_service import ChatSessionService
from src.storage import DatabaseManager


def _short_text(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _short_list(value: Any, *, item_limit: int = 12, text_limit: int = 240) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [_short_text(item, text_limit) for item in value[:item_limit] if str(item or "").strip()]


def _trace_result_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    projected: list[dict[str, Any]] = []
    for raw in value[:12]:
        if not isinstance(raw, Mapping):
            continue
        attributes = []
        for attribute in list(raw.get("attributes") or [])[:5]:
            if not isinstance(attribute, Mapping):
                continue
            attributes.append(
                {
                    "name": _short_text(attribute.get("name"), 80),
                    "value": _short_text(attribute.get("value"), 160),
                }
            )
        projected.append(
            {
                "title": _short_text(raw.get("title"), 360),
                "url": _short_text(raw.get("url"), 1_000) or None,
                "source": _short_text(raw.get("source"), 320) or None,
                "published_at": _short_text(raw.get("published_at"), 160) or None,
                "summary": _short_text(raw.get("summary"), 500) or None,
                "attributes": attributes,
            }
        )
    return projected


def _trace_tool_results(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for item in results[:80]:
        raw_result = item.get("result") if isinstance(item.get("result"), Mapping) else {}
        source_refs = _short_list(
            item.get("source_refs") or raw_result.get("source_refs"),
            item_limit=16,
            text_limit=1_000,
        )
        outcome = describe_tool_outcome(
            _short_text(item.get("tool_name"), 128),
            item,
        )

        def value(*keys: str) -> Any:
            for key in keys:
                if key in item and item[key] is not None:
                    return item[key]
                if key in raw_result and raw_result[key] is not None:
                    return raw_result[key]
            return None

        display_result = (
            dict(item.get("display_result") or {})
            if isinstance(item.get("display_result"), Mapping)
            else project_tool_result_for_timeline(raw_result, source_refs=source_refs)
        )
        raw_arguments = (
            item.get("display_arguments")
            if isinstance(item.get("display_arguments"), Mapping)
            else item.get("arguments")
        )
        arguments = project_arguments_for_timeline(
            raw_arguments if isinstance(raw_arguments, Mapping) else {},
        )
        data_time = value("data_time", "dataTime")
        data_time_provenance = value("data_time_provenance", "dataTimeProvenance")
        data_time_applicable = value("data_time_applicable", "dataTimeApplicable")
        if data_time_applicable is None:
            data_time_applicable = True
        is_stale = value("is_stale", "isStale")
        freshness_unknown = value("freshness_unknown", "freshnessUnknown")
        if freshness_unknown is None:
            freshness_unknown = data_time is None
        partial_result = value("partial", "partial_result", "partialResult")
        fallback_used = value("fallback_used", "fallbackUsed")
        projected.append(
            {
                "action_id": _short_text(item.get("action_id") or item.get("id"), 96),
                "tool_call_id": _short_text(item.get("tool_call_id"), 128) or None,
                "tool_name": _short_text(item.get("tool_name"), 128),
                "effect": _short_text(item.get("effect"), 32) or "read",
                "arguments": arguments,
                "success": outcome["execution_status"] == "completed",
                "partial": bool(partial_result),
                "reused": bool(item.get("reused")),
                "error_code": _short_text(
                    item.get("error_code") or raw_result.get("error_code"),
                    128,
                ) or None,
                "errors": _short_list(
                    item.get("errors") or raw_result.get("errors"),
                    item_limit=8,
                    text_limit=800,
                ),
                "data_time": _short_text(data_time, 160) or None,
                "data_time_provenance": _short_text(data_time_provenance, 80) or None,
                "data_time_applicable": data_time_applicable,
                "is_stale": is_stale,
                "freshness_unknown": bool(freshness_unknown),
                "has_data": bool(value("has_data")),
                "data_status": _short_text(value("data_status"), 64) or outcome["data_status"],
                "usable": bool(value("usable")) if value("usable") is not None else bool(outcome["usable"]),
                "evidence_eligible": bool(value("evidence_eligible")),
                "partial_result": bool(partial_result),
                "fallback_used": bool(fallback_used),
                "fallback_provider": _short_text(value("fallback_provider", "fallbackProvider"), 160) or None,
                "source_scope": _short_text(value("source_scope", "sourceScope"), 240) or None,
                "source_origin": _short_text(value("source_origin", "sourceOrigin"), 240) or None,
                "warnings": _short_list(
                    item.get("warnings") or raw_result.get("warnings"),
                    item_limit=8,
                    text_limit=800,
                ),
                # These are provenance identifiers and links, not a source count.
                "source_refs": source_refs,
                "source_labels": _short_list(
                    display_result.get("source_labels"),
                    item_limit=16,
                    text_limit=320,
                ),
                "result_count": (
                    int(display_result["result_count"])
                    if isinstance(display_result.get("result_count"), int)
                    else None
                ),
                "omitted_result_count": (
                    int(display_result["omitted_result_count"])
                    if isinstance(display_result.get("omitted_result_count"), int)
                    else 0
                ),
                "result_summary": _short_text(display_result.get("result_summary"), 800)
                or None,
                "result_items": _trace_result_items(display_result.get("result_items")),
                "reference_links": _short_list(
                    display_result.get("reference_links"),
                    item_limit=16,
                    text_limit=1_000,
                ),
                "content_access": describe_tool_access(
                    _short_text(item.get("tool_name"), 128),
                    item,
                ),
                "outcome": outcome,
                "data_quality": describe_tool_quality(
                    _short_text(item.get("tool_name"), 128),
                    item,
                ),
            }
        )
    return projected


def _trace_evidence(evidence: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "evidence_id": _short_text(item.get("evidence_id") or item.get("id"), 96),
            "action_id": _short_text(item.get("action_id"), 96),
            "tool_call_id": _short_text(item.get("tool_call_id"), 128) or None,
            "tool_name": _short_text(item.get("tool_name"), 128),
            "success": item.get("success") is True,
            "partial": bool(item.get("partial")),
            "data_time": _short_text(item.get("data_time"), 160) or None,
            "observed_at": _short_text(item.get("observed_at"), 160) or None,
            "is_stale": item.get("is_stale"),
            "freshness_unknown": bool(item.get("freshness_unknown")),
            "has_data": bool(item.get("has_data")),
            "data_status": _short_text(item.get("data_status"), 64) or None,
            "usable": bool(item.get("usable")),
            "evidence_eligible": bool(item.get("evidence_eligible")),
            "source_refs": _short_list(item.get("source_refs"), item_limit=12, text_limit=240),
        }
        for item in evidence[:80]
        if evidence_record_is_eligible(item)
    ]


def _trace_claim_evidence(claims: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Persist the compact, user-safe projection of the claim audit ledger."""
    projected: list[dict[str, Any]] = []
    for item in claims[:80]:
        checks = item.get("checks") if isinstance(item.get("checks"), Mapping) else {}
        evidence = item.get("evidence") if isinstance(item.get("evidence"), Sequence) else []
        projected.append(
            {
                "claim_id": _short_text(item.get("claim_id"), 96),
                "text": _short_text(item.get("text"), 2_000),
                "kind": _short_text(item.get("kind"), 32),
                "evidence_ids": _short_list(item.get("evidence_ids"), item_limit=16, text_limit=96),
                "unresolved_evidence_ids": _short_list(
                    item.get("unresolved_evidence_ids"),
                    item_limit=16,
                    text_limit=96,
                ),
                "entity_fields": _short_list(item.get("entity_fields"), item_limit=24, text_limit=96),
                "time_references": _short_list(item.get("time_references"), item_limit=12, text_limit=96),
                "uses_relative_time": bool(item.get("uses_relative_time")),
                "checks": {
                    "tool_success": checks.get("tool_success") is True,
                    "source": checks.get("source") is True,
                    "entity_scope": checks.get("entity_scope") is True,
                    "time": checks.get("time") is True,
                },
                "evidence": [
                    {
                        "evidence_id": _short_text(entry.get("evidence_id"), 96),
                        "tool_name": _short_text(entry.get("tool_name"), 128),
                        "data_time": _short_text(entry.get("data_time"), 160) or None,
                        "source_refs": _short_list(entry.get("source_refs"), item_limit=8, text_limit=240),
                    }
                    for entry in evidence[:16]
                    if isinstance(entry, Mapping)
                ],
            }
        )
    return projected


def _execution_trace(
    *,
    stage_history: Sequence[Mapping[str, Any]],
    display_parts: Sequence[Mapping[str, Any]] = (),
    tool_results: Sequence[Mapping[str, Any]],
    evidence: Sequence[Mapping[str, Any]],
    claim_evidence: Sequence[Mapping[str, Any]],
    state: Mapping[str, Any],
) -> dict[str, Any]:
    projected = {
        "stages": project_stage_history_for_client(
            [item for item in stage_history if isinstance(item, Mapping)],
        ),
        # This is the bounded native assistant-stream projection used for
        # terminal hydration.  The ordered event log and tool ledger remain
        # the authoritative sources for audit and detailed inspection.
        "display_parts": [
            dict(item)
            for item in display_parts[:240]
            if isinstance(item, Mapping)
        ],
        "tool_results": _trace_tool_results(tool_results),
        "evidence": _trace_evidence(evidence),
        "claim_evidence": _trace_claim_evidence(claim_evidence),
        "loop": {
            "model_turn_count": int(state.get("model_turn_count") or 0),
            "tool_call_count": int(state.get("tool_call_count") or 0),
            "tool_call_limit": int(state.get("tool_call_limit") or 0),
            "evidence_repair_count": int(state.get("evidence_repair_count") or 0),
            "evidence_repair_limit": int(state.get("evidence_repair_limit") or 0),
            "response_repair_count": int(state.get("response_repair_count") or 0),
            "response_repair_limit": int(state.get("response_repair_limit") or 0),
            "fallback_repair_count": int(state.get("fallback_repair_count") or 0),
            "fallback_repair_limit": int(state.get("fallback_repair_limit") or 0),
            "content_access_repair_count": int(state.get("content_access_repair_count") or 0),
            "content_access_repair_limit": int(state.get("content_access_repair_limit") or 0),
            "content_access_target_count": len(state.get("content_access_targets") or []),
            "required_content_read_count": len(state.get("required_content_reads") or []),
            "pending_content_read_count": len(state.get("pending_content_reads") or []),
            "work_budget_exhausted": bool(state.get("work_budget_exhausted")),
            "work_budget_detail": _short_text(state.get("work_budget_detail"), 500) or None,
        },
        "completed_tool_call_ids": _short_list(
            state.get("completed_tool_call_ids"),
            item_limit=80,
            text_limit=96,
        ),
    }
    return enrich_execution_trace_with_result_previews(
        projected,
        tool_results=tool_results,
        evidence=evidence,
    )


@dataclass
class AgentTerminalPublisher:
    controller: RunBroadcaster
    run: ActiveRun
    messages: Sequence[Mapping[str, Any]]
    request_body: Mapping[str, Any]
    conversation_id: str
    database: DatabaseManager
    session_service: ChatSessionService
    worker_id: str

    async def commit(
        self,
        *,
        status: str,
        final_text: str,
        graph_state: Mapping[str, Any] | None = None,
        error_code: str | None = None,
        error_detail: str | None = None,
        latest_stage: Mapping[str, Any] | None = None,
        stage_history: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        """Commit transcript, generic trace, and run status in one DB transaction."""
        state = dict(graph_state or {})
        eligible_evidence = [
            dict(item)
            for item in (state.get("evidence") or [])
            if evidence_record_is_eligible(item)
        ]
        # The terminal publisher is the last server-owned boundary before a
        # message becomes durable.  Only evidence that passed the same semantic
        # eligibility rule as the claim ledger may resolve visible markers.
        final_text, _ = prepare_answer_for_client(final_text, eligible_evidence)
        final_text = finalize_terminal_answer(
            final_text,
            status=status,
            error_code=error_code,
            detail=error_detail,
        )
        await self.controller.drain()
        if stage_history is None:
            snapshot = getattr(self.controller, "stage_history_snapshot", None)
            stage_history = snapshot() if callable(snapshot) else []
        display_parts_snapshot = getattr(self.controller, "display_parts_snapshot", None)
        display_parts = (
            display_parts_snapshot(final_text=final_text)
            if callable(display_parts_snapshot)
            else []
        )
        terminal_messages = [dict(message) for message in self.messages]
        if final_text.strip():
            terminal_messages.append(
                {
                    "id": str(
                        self.request_body.get("unstable_assistantMessageId")
                        or f"assistant-{uuid.uuid4().hex}"
                    ),
                    "role": "assistant",
                    "content": final_text,
                    "created_at": datetime.now().isoformat(),
                }
            )
        normalized_messages = self.session_service.normalize_messages(terminal_messages)
        first_user_text = next(
            (
                str(message.get("content") or "")
                for message in normalized_messages
                if message.get("role") == "user"
            ),
            "",
        )
        tool_results = [
            dict(item)
            for item in (state.get("tool_results") or [])
            if isinstance(item, Mapping)
        ]
        evidence = [
            dict(item)
            for item in (state.get("evidence") or [])
            if isinstance(item, Mapping)
        ]
        claim_evidence = [
            dict(item)
            for item in (state.get("claim_evidence") or [])
            if isinstance(item, Mapping)
        ]
        execution_trace = _execution_trace(
            stage_history=stage_history,
            display_parts=display_parts,
            tool_results=tool_results,
            evidence=evidence,
            claim_evidence=claim_evidence,
            state=state,
        )
        quality_projection = {
            "engine": "langgraph_agent_loop",
            "inspection_schema_version": INSPECTION_SCHEMA_VERSION,
            # The quality projection is the bounded, user-safe run contract.
            # Full provider payloads remain in the durable step ledger and are
            # loaded only when the explorer requests them.
            "display_parts": execution_trace["display_parts"],
            "tool_results": execution_trace["tool_results"],
            "evidence": eligible_evidence,
            "claim_evidence": claim_evidence,
            "completed_tool_call_ids": list(state.get("completed_tool_call_ids") or []),
            "budgets": {
                "tool_call_count": int(state.get("tool_call_count") or 0),
                "tool_call_limit": int(state.get("tool_call_limit") or 0),
                "model_turn_count": int(state.get("model_turn_count") or 0),
                "evidence_repair_count": int(state.get("evidence_repair_count") or 0),
                "evidence_repair_limit": int(state.get("evidence_repair_limit") or 0),
                "response_repair_count": int(state.get("response_repair_count") or 0),
                "response_repair_limit": int(state.get("response_repair_limit") or 0),
                "fallback_repair_count": int(state.get("fallback_repair_count") or 0),
                "fallback_repair_limit": int(state.get("fallback_repair_limit") or 0),
            },
            "execution_trace": execution_trace,
        }
        trace_payload = {
            "engine": "langgraph_agent_loop",
            "run_id": self.run.run_id,
            "status": status,
            "schema_version": INSPECTION_SCHEMA_VERSION,
            "model_turn_count": state.get("model_turn_count"),
            "tool_call_count": state.get("tool_call_count"),
            "evidence_repair_count": state.get("evidence_repair_count"),
            "fallback_repair_count": state.get("fallback_repair_count"),
            "quality_projection": quality_projection,
        }
        if error_code:
            trace_payload["error_code"] = error_code
        if latest_stage is not None:
            trace_payload["latest_stage"] = dict(latest_stage)

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                committed = await asyncio.shield(
                    asyncio.to_thread(
                        self.database.commit_agent_run_terminal,
                        run_id=self.run.run_id,
                        conversation_id=self.conversation_id,
                        status=status,
                        messages=normalized_messages,
                        final_text=final_text,
                        # Old orchestration JSON is never fed into the new
                        # graph. Clear it as the new turn becomes canonical.
                        agent_context={},
                        artifacts=(),
                        conclusions=(
                            project_research_conclusions(state, as_of=datetime.now())
                            if status == "completed" else ()
                        ),
                        trace=trace_payload,
                        generated_title=(
                            self.session_service.generate_title(first_user_text)
                            if first_user_text
                            else None
                        ),
                        error_code=error_code,
                        error_detail=error_detail,
                        worker_id=self.worker_id,
                        attempt=self.run.attempt,
                    )
                )
                if not committed:
                    raise RuntimeError("atomic Agent terminal commit did not find its run")
                return
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.1 * (2**attempt))
        assert last_error is not None
        raise last_error


__all__ = ["AgentTerminalPublisher"]
