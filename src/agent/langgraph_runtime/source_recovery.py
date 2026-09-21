"""Checkpointed source recovery decisions; execution stays in native ToolNode.

One failed logical request owns one recovery, optionally search then read.
Tools, evidence and error receipts retain that ownership. Completing a web
request is not proof that its evidence answers the original research question.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from src.tools.base import classify_result_semantics

from .content_access import reference_candidates
from .state import merge_records


def active_source_recovery(state: Mapping[str, Any]) -> dict[str, Any] | None:
    return next((dict(item) for item in state.get("source_fallback_attempts") or []
                 if isinstance(item, Mapping) and item.get("status") == "started"), None)


def recovery_feedback(attempt: Mapping[str, Any]) -> str:
    return (
        "当前执行一次网页来源恢复，不提交最终答案，也不重试已失败的来源和参数。\n"
        + json.dumps({key: attempt.get(key) for key in (
            "tool_name", "reason", "arguments", "fallback_operation", "source_refs",
        )}, ensure_ascii=False, default=str)
        + "\n仅调用指定的网页工具；搜索须匹配原任务的实体、指标和时间范围。"
        "读取时只选择上述来源中与缺口相关的 URL。无关结果不能算恢复，仍需明确保留缺口。"
    )


def advance_source_recovery(
    state: Mapping[str, Any],
    context: Any,
    requirements: Sequence[Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Run after a native tools join, not inside a tool or a parallel writer."""
    attempts = [dict(item) for item in state.get("source_fallback_attempts") or []]
    changed: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    changed_records: list[dict[str, Any]] = []
    turn = int(state.get("model_turn_count") or 0)
    remaining = max(0, int(state.get("tool_call_limit") or 0) - int(state.get("tool_call_count") or 0))
    step_limit = min(2, max(0, int(state.get("fallback_repair_limit") or 0)))
    count = 0
    active = active_source_recovery(state)

    def commit(attempt: dict[str, Any]) -> None:
        changed.append(dict(attempt))
        updated_receipts = []
        for receipt in state.get("runtime_errors") or []:
            if str(receipt.get("error_id") or "") not in attempt.get("error_ids", []):
                continue
            updated_receipts.append({
                **dict(receipt),
                "fallback_status": attempt["status"],
                "fallback_call_ids": list(attempt["fallback_call_ids"]),
                "details": {**dict(receipt.get("details") or {}),
                            "fallback_request_id": attempt["id"],
                            "fallback_reason": attempt.get("completion_reason", ""),
                            "coverage_requires_validation": True},
            })
        receipts.extend(updated_receipts)
        by_id = {item["error_id"]: item for item in updated_receipts}
        for record in state.get("tool_results") or []:
            errors = [by_id.get(item.get("error_id"), item) for item in record.get("runtime_errors") or []]
            if any(item.get("error_id") in by_id for item in errors):
                changed_records.append({**dict(record), "runtime_errors": errors,
                                        "runtime_error": errors[0]})
        context.events.stage(
            "source_fallback", "completed" if attempt["status"] == "completed"
            else "started" if attempt["status"] == "started" else "failed",
            "网页恢复工具已返回，证据覆盖仍需核验" if attempt["status"] == "completed"
            else "正在通过网页来源恢复当前数据缺口" if attempt["status"] == "started"
            else "网页来源恢复未完成，保留数据缺口并继续分析",
            action_id=attempt["id"],
            details={"fallback_request_id": attempt["id"], "source_fallback": dict(attempt),
                     "runtime_errors": updated_receipts,
                     **{key: attempt.get(key) for key in ("task_id", "agent_id", "collaboration_id")}},
        )

    if active and turn > int(active["started_model_turn"]):
        owned = [record for record in records if record.get("fallback_request_id") == active["id"]]
        active["fallback_call_ids"] = list(dict.fromkeys([
            *active["fallback_call_ids"],
            *(str(record.get("action_id") or record.get("id") or "") for record in owned),
        ]))
        usable = [record for record in owned if record.get("success") is True
                  and classify_result_semantics({"success": True, **dict(record.get("result") or {})})["usable"]]
        # Search snippets are references even when a provider omitted the
        # optional content_access envelope. Reuse the shared URL projection.
        search_references = [{**record, "content_access": {"mode": "reference_only"}}
                             for record in usable if record.get("tool_name") == "search_web_source"]
        urls = list(dict.fromkeys(item["url"] for item in reference_candidates(search_references)))[:6]
        if (active["fallback_operation"] == "search_web_source" and urls
                and active["step"] < step_limit and remaining
                and context.registry.get_tool("read_web_source") is not None):
            active.update(fallback_operation="read_web_source", source_refs=urls,
                          step=active["step"] + 1, started_model_turn=turn)
            count += 1
            commit(active)
        else:
            active.update(status="completed" if usable else "failed",
                          completion_reason="usable_web_result" if usable else "no_usable_recovery_result")
            commit(active)
            active = None

    attempts = merge_records(attempts, changed)
    handled = {item.get("source_fallback_key") for item in attempts}
    for requirement in requirements:
        if active or requirement["fallback_key"] in handled:
            continue
        operation = str(requirement["fallback_operation"])
        if context.registry.get_tool(operation) is None:
            operation = "search_web_source"
        can_start = bool(step_limit and remaining and context.registry.get_tool(operation) is not None)
        request_key = f"{context.run_id}:{requirement['fallback_key']}"
        active = {
            **dict(requirement),
            "id": "source-fallback:" + hashlib.sha256(request_key.encode()).hexdigest()[:24],
            "source_fallback_key": requirement["fallback_key"],
            "status": "started" if can_start else "skipped",
            "fallback_operation": operation, "fallback_call_ids": [],
            "started_model_turn": turn, "step": 1,
            "completion_reason": "" if can_start else "budget_or_recovery_tool_unavailable",
        }
        handled.add(requirement["fallback_key"])
        commit(active)
        if can_start:
            count += 1
        else:
            active = None
    if not changed:
        return {}
    return {
        "source_fallback_attempts": changed,
        "runtime_errors": receipts,
        "tool_results": changed_records,
        "fallback_feedback": recovery_feedback(active) if active else "",
        "fallback_repair_count": count,
    }
