"""Bounded, user-safe projection for the independent Goal runtime."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


def _text(value: Any, limit: int = 1_200) -> str:
    text = str(value or "")
    text = re.sub(r"https?://[^\s)\]}>,]+", "[链接已隐藏]", text, flags=re.IGNORECASE)
    return text[:limit]


def _items(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def goal_trace(state: Mapping[str, Any]) -> dict[str, Any] | None:
    """Project only Goal state that a user can verify from the UI.

    The LangGraph checkpoint remains the recovery source.  This projection is
    intentionally bounded and does not include model messages, prompts, or
    hidden reasoning.
    """

    mode = str(state.get("resolved_agent_mode") or state.get("agent_mode") or "").strip().lower()
    if mode != "goal" and not state.get("goal_contract") and not state.get("goal_criteria"):
        return None

    contract = state.get("goal_contract") if isinstance(state.get("goal_contract"), Mapping) else {}
    criteria: list[dict[str, Any]] = []
    for raw in _items(state.get("goal_criteria") or contract.get("success_criteria"))[:8]:
        if not isinstance(raw, Mapping):
            continue
        criteria.append(
            {
                "criterion_id": _text(raw.get("criterion_id"), 96),
                "description": _text(raw.get("description"), 900),
                "required": bool(raw.get("required", True)),
                "verification_method": _text(raw.get("verification_method"), 40) or "unknown",
                "status": _text(raw.get("status"), 24) or "pending",
                "evidence_ids": [
                    _text(value, 96)
                    for value in _items(raw.get("evidence_ids"))[:24]
                    if str(value or "").strip()
                ],
                "explanation": _text(raw.get("explanation"), 700),
            }
        )

    raw_action = state.get("goal_action") if isinstance(state.get("goal_action"), Mapping) else None
    action = None
    if raw_action is not None:
        action = {
            "action_id": _text(raw_action.get("action_id"), 128),
            "kind": _text(raw_action.get("kind"), 24),
            "tool_name": _text(raw_action.get("tool_name"), 128) or None,
            "status": _text(state.get("goal_action_status"), 32) or "selected",
            "criterion_ids": [
                _text(value, 96)
                for value in _items(raw_action.get("criterion_ids"))[:8]
                if str(value or "").strip()
            ],
        }

    evidence_ids = []
    for value in _items(state.get("goal_evidence_ids"))[:80]:
        text = _text(value, 96)
        if text and text not in evidence_ids:
            evidence_ids.append(text)
    for criterion in criteria:
        for value in criterion["evidence_ids"]:
            if value not in evidence_ids:
                evidence_ids.append(value)

    goal_status = _text(state.get("goal_status"), 32) or _text(state.get("status"), 32) or "pending"
    terminal = goal_status in {"completed", "blocked", "failed", "cancelled"}
    return {
        "schema_version": "goal.v1",
        "status": goal_status,
        "objective": _text(contract.get("objective"), 2_000),
        "scope": _text(contract.get("scope"), 1_000),
        "revision": int(contract.get("revision") or 1),
        "criteria": criteria,
        "current_action": None if terminal else action,
        "last_action": action if terminal else None,
        "progress": _text(state.get("goal_progress"), 1_200),
        "iteration": int(state.get("goal_iterations") or 0),
        "replan_count": int(state.get("goal_replan_count") or 0),
        "limits": {
            "iterations": int(state.get("goal_iteration_limit") or 0),
            "replans": int(state.get("goal_replan_limit") or 0),
            "tool_calls": int(state.get("goal_tool_call_limit") or 0),
            "model_calls": int(state.get("goal_model_call_limit") or 0),
            "action_validation_repairs": int(state.get("goal_action_validation_repairs") or 0),
            "action_validation_repair_limit": int(state.get("goal_action_validation_repair_limit") or 0),
        },
        "evidence_ids": evidence_ids[:80],
        "blocker": _text(state.get("goal_blocker"), 1_200) or None,
        "terminal_reason": _text(state.get("goal_terminal_reason"), 1_200) or None,
        "confirmation_status": _text(state.get("goal_confirmation_status"), 32) or "not_required",
        "pending_confirmation_criteria": [
            _text(value, 96)
            for value in _items(state.get("goal_pending_confirmation_criteria"))[:8]
            if str(value or "").strip()
        ],
    }


__all__ = ["goal_trace"]
