"""User-safe execution trace projection for multi-agent runs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


def _text(value: Any, limit: int = 1_600) -> str:
    text = str(value or "")
    text = re.sub(r"https?://[^\s)\]}>,]+", "[链接已隐藏]", text, flags=re.IGNORECASE)
    return text[:limit]


def team_trace(state: Mapping[str, Any]) -> dict[str, Any] | None:
    mode = str(state.get("orchestrator_mode") or "")
    if not mode.startswith("multi_agent") and not state.get("team_plan") and not state.get("team_results"):
        return None
    route = str(state.get("orchestrator_route") or "")
    strategy = str(state.get("orchestrator_execution_strategy") or "")
    if route != "team" and not state.get("team_plan"):
        return None
    requested_mode = str(state.get("agent_mode") or "").strip().lower()
    if requested_mode not in {"auto", "direct", "plan", "team"}:
        requested_mode = "team" if strategy == "team" else "plan"
    resolved_mode = str(state.get("resolved_agent_mode") or "").strip().lower()
    if resolved_mode not in {"direct", "plan", "team"}:
        resolved_mode = "team" if strategy == "team" else "plan"
    tasks = []
    collaboration = state.get("collaboration") if isinstance(state.get("collaboration"), Mapping) else {}
    raw_plan = state.get("team_plan")
    if not isinstance(raw_plan, Mapping) and isinstance(collaboration.get("plan"), Mapping):
        raw_plan = collaboration.get("plan")
    if isinstance(raw_plan, Mapping):
        for item in list(raw_plan.get("tasks") or [])[:12]:
            if not isinstance(item, Mapping):
                continue
            tasks.append(
                {
                    "task_id": _text(item.get("task_id"), 96),
                    "agent_id": _text(item.get("agent_id"), 96),
                    "agent_node": _text(item.get("agent_node"), 96),
                    "agent_display_name": _text(item.get("agent_display_name"), 160),
                    "objective": _text(item.get("objective"), 900),
                    "input_refs": [_text(value, 600) for value in list(item.get("input_refs") or [])[:12]],
                    "allowed_tools": [_text(value, 128) for value in list(item.get("allowed_tools") or [])[:16]],
                    "output_format": _text(item.get("output_format"), 600),
                    "timeout_seconds": int(item.get("timeout_seconds") or 0),
                    "failure_strategy": _text(item.get("failure_strategy"), 24),
                    "max_attempts": int(item.get("max_attempts") or 0),
                    "required_evidence": [_text(value, 600) for value in list(item.get("required_evidence") or [])[:8]],
                    "success_criteria": [_text(value, 600) for value in list(item.get("success_criteria") or [])[:8]],
                    "max_tool_calls": int(item.get("max_tool_calls") or 0),
                    "parallel_group": _text(item.get("parallel_group"), 64),
                    "depends_on": [_text(value, 96) for value in list(item.get("depends_on") or [])[:8]],
                }
            )
    raw_results = list(state.get("team_results") or [])
    if not raw_results and isinstance(collaboration.get("reports"), Mapping):
        raw_results = list(collaboration.get("reports", {}).values())
    results = []
    for item in raw_results[:12]:
        if not isinstance(item, Mapping):
            continue
        results.append(
            {
                "task_id": _text(item.get("task_id"), 96),
                "agent_id": _text(item.get("agent_id"), 96),
                "agent_node": _text(item.get("agent_node"), 64),
                "expert_id": _text(item.get("expert_id"), 96),
                "agent_display_name": _text(item.get("agent_display_name"), 160),
                "expert_id": _text(item.get("expert_id") or item.get("agent_id"), 96),
                "status": _text(item.get("status"), 24),
                "attempt": int(item.get("attempt") or 1),
                "summary": _text(item.get("summary"), 2_000),
                "findings": [_text(value, 600) for value in list(item.get("findings") or [])[:12]],
                "finding_evidence_refs": [
                    [_text(value, 96) for value in list(refs or [])[:24]]
                    for refs in list(item.get("finding_evidence_refs") or [])[:12]
                ],
                "limitations": [_text(value, 600) for value in list(item.get("limitations") or [])[:8]],
                "open_questions": [_text(value, 600) for value in list(item.get("open_questions") or [])[:8]],
                "confidence": _text(item.get("confidence"), 24),
                "evidence_ids": [_text(value, 96) for value in list(item.get("evidence_ids") or [])[:80]],
                "tool_call_count": int(item.get("tool_call_count") or 0),
                "model_turn_count": int(item.get("model_turn_count") or 0),
                "assessment_status": _text(item.get("assessment_status"), 24),
                "criteria_status": _text(item.get("criteria_status"), 24),
                "criteria_checks": [
                    {
                        "criterion_index": int(check.get("criterion_index") or 0),
                        "criterion": _text(check.get("criterion"), 600),
                        "verdict": _text(check.get("verdict"), 24),
                        "explanation": _text(check.get("explanation"), 800),
                        "source_ids": [int(value) for value in list(check.get("source_ids") or [])[:24]],
                    }
                    for check in list(item.get("criteria_checks") or [])[:8]
                    if isinstance(check, Mapping)
                ],
                "unmet_criteria": [_text(value, 600) for value in list(item.get("unmet_criteria") or [])[:8]],
                "error_code": _text(item.get("error_code"), 128) or None,
                "error_detail": _text(item.get("error_detail"), 600) or None,
            }
        )
    review = state.get("team_critic_review")
    projected_review = None
    if isinstance(review, Mapping):
        projected_review = {
            "verdict": _text(review.get("verdict"), 24),
            "summary": _text(review.get("summary"), 800),
            "issues": [
                {
                    "category": _text(issue.get("category"), 32),
                    "severity": _text(issue.get("severity"), 24),
                    "reason": _text(issue.get("reason"), 600),
                    "task_ids": [_text(value, 96) for value in list(issue.get("task_ids") or [])[:8]],
                    "repair_instruction": _text(issue.get("repair_instruction"), 600),
                }
                for issue in list(review.get("issues") or [])[:12]
                if isinstance(issue, Mapping)
            ],
        }
    conflict = state.get("team_conflict_assessment")
    projected_conflict = None
    if isinstance(conflict, Mapping):
        projected_conflict = {
            "status": _text(conflict.get("status"), 24),
            "reason": _text(conflict.get("reason"), 900),
            "risk_flags": [_text(value, 240) for value in list(conflict.get("risk_flags") or [])[:12]],
            "requires_adversarial_review": bool(conflict.get("requires_adversarial_review")),
            "issues": [
                {
                    "category": _text(issue.get("category"), 32),
                    "severity": _text(issue.get("severity"), 24),
                    "reason": _text(issue.get("reason"), 700),
                    "task_ids": [_text(value, 96) for value in list(issue.get("task_ids") or [])[:8]],
                    "source_ids": [int(value) for value in list(issue.get("source_ids") or [])[:24]],
                }
                for issue in list(conflict.get("issues") or [])[:12]
                if isinstance(issue, Mapping)
            ],
        }

    def project_case(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, Mapping):
            return None
        return {
            "stance": _text(value.get("stance"), 12),
            "summary": _text(value.get("summary"), 1_600),
            "arguments": [_text(item, 600) for item in list(value.get("arguments") or [])[:10]],
            "supporting_source_ids": [
                int(item) for item in list(value.get("supporting_source_ids") or [])[:40]
            ],
            "counter_source_ids": [int(item) for item in list(value.get("counter_source_ids") or [])[:40]],
            "assumptions": [_text(item, 600) for item in list(value.get("assumptions") or [])[:10]],
            "risks": [_text(item, 600) for item in list(value.get("risks") or [])[:10]],
            "confidence": _text(value.get("confidence"), 24),
        }

    consensus = state.get("team_consensus")
    projected_consensus = None
    if isinstance(consensus, Mapping):
        projected_consensus = {
            "verdict": _text(consensus.get("verdict"), 24),
            "conclusion": _text(consensus.get("conclusion"), 1_800),
            "rationale": _text(consensus.get("rationale"), 1_200),
            "source_ids": [int(value) for value in list(consensus.get("source_ids") or [])[:80]],
            "unresolved_conflicts": [
                _text(value, 700) for value in list(consensus.get("unresolved_conflicts") or [])[:12]
            ],
            "confidence": _text(consensus.get("confidence"), 24),
            "allow_final_answer": bool(consensus.get("allow_final_answer")),
            "needs_replan": bool(consensus.get("needs_replan")),
        }
    criteria_assessment = state.get("team_criteria_assessment")
    projected_criteria = None
    if isinstance(criteria_assessment, Mapping):
        projected_criteria = {
            "status": _text(criteria_assessment.get("status"), 24),
            "checks": [
                {
                    "criterion_index": int(check.get("criterion_index") or 0),
                    "criterion": _text(check.get("criterion"), 600),
                    "verdict": _text(check.get("verdict"), 24),
                    "explanation": _text(check.get("explanation"), 800),
                    "evidence_ids": [_text(value, 96) for value in list(check.get("evidence_ids") or [])[:24]],
                }
                for check in list(criteria_assessment.get("checks") or [])[:8]
                if isinstance(check, Mapping)
            ],
            "unmet_criteria": [_text(value, 600) for value in list(criteria_assessment.get("unmet_criteria") or [])[:8]],
            "evidence_ids": [_text(value, 96) for value in list(criteria_assessment.get("evidence_ids") or [])[:80]],
        }

    def project_evidence_merge(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, Mapping):
            return None
        worker_evidence = value.get("worker_evidence")
        finding_evidence = value.get("finding_evidence")
        return {
            "status": _text(value.get("status"), 24),
            "summary": _text(value.get("summary"), 1_000),
            "evidence_ids": [_text(item, 96) for item in list(value.get("evidence_ids") or [])[:80]],
            "worker_evidence": {
                _text(task_id, 96): [_text(item, 96) for item in list(ids or [])[:80]]
                for task_id, ids in list(worker_evidence.items())[:8]
                if str(task_id).strip()
            } if isinstance(worker_evidence, Mapping) else {},
            "finding_evidence": {
                _text(finding_id, 96): [_text(item, 96) for item in list(ids or [])[:24]]
                for finding_id, ids in list(finding_evidence.items())[:96]
                if str(finding_id).strip()
            } if isinstance(finding_evidence, Mapping) else {},
            "missing_task_ids": [_text(item, 96) for item in list(value.get("missing_task_ids") or [])[:8]],
            "invalid_evidence_ids": [_text(item, 96) for item in list(value.get("invalid_evidence_ids") or [])[:24]],
            "limitations": [_text(item, 600) for item in list(value.get("limitations") or [])[:8]],
            "duplicate_count": int(value.get("duplicate_count") or 0),
        }

    projected_critic = None
    critic = state.get("team_critic_review")
    if isinstance(critic, Mapping):
        projected_critic = {
            "verdict": _text(critic.get("verdict"), 24),
            "summary": _text(critic.get("summary"), 900),
            "issues": [
                {
                    "category": _text(issue.get("category"), 32),
                    "severity": _text(issue.get("severity"), 24),
                    "reason": _text(issue.get("reason"), 600),
                    "task_ids": [_text(item, 96) for item in list(issue.get("task_ids") or [])[:8]],
                    "repair_instruction": _text(issue.get("repair_instruction"), 600),
                }
                for issue in list(critic.get("issues") or [])[:12]
                if isinstance(issue, Mapping)
            ],
        }
    draft = state.get("team_draft")
    projected_draft = None
    if isinstance(draft, Mapping):
        projected_draft = {
            "status": _text(draft.get("status"), 24),
            "summary": _text(draft.get("summary"), 1_200),
            "sections": [
                {
                    "task_id": _text(section.get("task_id"), 96),
                    "agent_id": _text(section.get("agent_id"), 96),
                    "title": _text(section.get("title"), 160),
                    "content": _text(section.get("content"), 4_000),
                    "status": _text(section.get("status"), 24),
                    "evidence_ids": [_text(item, 96) for item in list(section.get("evidence_ids") or [])[:80]],
                    "limitations": [_text(item, 600) for item in list(section.get("limitations") or [])[:8]],
                }
                for section in list(draft.get("sections") or [])[:12]
                if isinstance(section, Mapping)
            ],
            "evidence_ids": [_text(item, 96) for item in list(draft.get("evidence_ids") or [])[:80]],
            "unresolved_task_ids": [_text(item, 96) for item in list(draft.get("unresolved_task_ids") or [])[:12]],
            "unresolved_questions": [_text(item, 600) for item in list(draft.get("unresolved_questions") or [])[:24]],
            "limitations": [_text(item, 600) for item in list(draft.get("limitations") or [])[:12]],
        }
    raw_reexecution = state.get("team_reexecution")
    projected_reexecution = None
    if isinstance(raw_reexecution, Mapping):
        projected_reexecution = {
            "status": _text(raw_reexecution.get("status"), 24),
            "round": int(raw_reexecution.get("round") or 0),
            "max_rounds": int(raw_reexecution.get("max_rounds") or 0),
            "task_ids": [_text(item, 96) for item in list(raw_reexecution.get("task_ids") or [])[:12]],
            "reasons": [_text(item, 600) for item in list(raw_reexecution.get("reasons") or [])[:12]],
            "next_action": _text(raw_reexecution.get("next_action"), 600),
        }
    raw_failure = collaboration.get("failure")
    if not isinstance(raw_failure, Mapping):
        raw_failure = {}
    raw_team_status = _text(state.get("team_status"), 32)
    runtime_status = _text(state.get("status"), 32)
    effective_status = raw_team_status
    raw_failure_status = _text(raw_failure.get("status"), 24)
    if raw_failure_status in {"failed", "blocked", "cancelled"}:
        effective_status = raw_failure_status
    elif runtime_status in {"failed", "blocked", "cancelled"}:
        effective_status = runtime_status
    failure_status = raw_failure_status
    if failure_status not in {"failed", "blocked", "cancelled"}:
        failure_status = runtime_status if runtime_status in {"failed", "blocked", "cancelled"} else ""
    projected_failure = None
    if raw_failure or failure_status or effective_status in {"failed", "blocked", "cancelled"}:
        projected_failure = {
            "status": failure_status or effective_status,
            "error_code": _text(raw_failure.get("error_code"), 128)
            or _text(state.get("error_code"), 128)
            or None,
            "detail": _text(raw_failure.get("detail"), 1_000)
            or _text(state.get("terminal_detail"), 1_000)
            or _text(state.get("team_plan_error"), 1_000)
            or None,
            "phase": _text(raw_failure.get("phase"), 64)
            or _text(collaboration.get("phase"), 64)
            or None,
            "dispatch_status": _text(raw_failure.get("dispatch_status"), 24)
            or (
                "not_started"
                if not state.get("team_dispatched_task_ids")
                and int(state.get("team_dispatch_round") or 0) == 0
                else "stopped"
            ),
        }
    projected_collaboration = {
        "schema_version": _text(collaboration.get("schema_version"), 24) or "team.v1",
        "phase": _text(collaboration.get("phase"), 64),
        "revision": int(collaboration.get("revision") or 0),
        "plan": (
            {
                "plan_id": _text(raw_plan.get("plan_id"), 96),
                "goal": _text(raw_plan.get("goal"), 1_200),
                "tasks": tasks,
            }
            if isinstance(raw_plan, Mapping)
            else None
        ),
        "tasks": tasks,
        "reports": {str(item.get("task_id")): item for item in results if str(item.get("task_id") or "").strip()},
        "review": {
            "conflict": projected_conflict,
            "critic": projected_critic,
            "bull_case": project_case(state.get("team_bull_case_review")),
            "bear_case": project_case(state.get("team_bear_case_review")),
            "consensus": projected_consensus,
            "criteria": projected_criteria,
        },
        "draft": projected_draft,
        "reexecution": projected_reexecution,
        "failure": projected_failure,
    }
    return {
        "agent_mode": requested_mode,
        "resolved_agent_mode": resolved_mode,
        "mode": mode or "multi_agent_team",
        "requested_mode": _text(state.get("agent_mode"), 24),
        "route": _text(route, 24),
        "execution_strategy": _text(strategy, 24),
        "route_reason": _text(
            state.get("orchestrator_route_reason"),
            600,
        ),
        "team_id": _text(state.get("team_id"), 96),
        "status": effective_status,
        "plan_source": _text(state.get("team_plan_source"), 32) or "model",
        "plan_error": _text(state.get("team_plan_error"), 600) or None,
        "worker_count": int(state.get("team_worker_count") or len(tasks)),
        "completed_worker_count": int(state.get("team_completed_worker_count") or 0),
        "dispatched_task_ids": [_text(value, 96) for value in list(state.get("team_dispatched_task_ids") or [])[:8]],
        "dispatch_round": int(state.get("team_dispatch_round") or 0),
        "task_attempts": {
            _text(task_id, 96): int(attempt or 0)
            for task_id, attempt in (state.get("team_task_attempts") or {}).items()
            if str(task_id).strip()
        },
        "worker_handoff": {
            "status": _text(state.get("team_worker_handoff_status"), 24),
            "task_ids": [_text(value, 96) for value in list(state.get("team_worker_handoff_task_ids") or [])[:8]],
            "incomplete_task_ids": [
                _text(value, 96)
                for value in list(state.get("team_worker_handoff_incomplete_task_ids") or [])[:8]
            ],
            "error": _text(state.get("team_worker_handoff_error"), 600) or None,
        },
        "failure_policy": {
            "action": _text(state.get("team_failure_policy_action"), 24),
            "task_ids": [_text(value, 96) for value in list(state.get("team_failure_policy_task_ids") or [])[:8]],
            "status": _text(state.get("team_failure_policy_status"), 32),
            "error": _text(state.get("team_failure_policy_error"), 600) or None,
        },
        "contract_call_count": int(state.get("team_contract_call_count") or 0),
        "plan": (
            {
                "plan_id": _text(raw_plan.get("plan_id"), 96) if isinstance(raw_plan, Mapping) else None,
                "goal": _text(raw_plan.get("goal"), 1_200) if isinstance(raw_plan, Mapping) else None,
                "completion_criteria": (
                    [_text(value, 600) for value in list(raw_plan.get("completion_criteria") or [])[:8]]
                    if isinstance(raw_plan, Mapping)
                    else []
                ),
                "synthesis_instructions": (
                    _text(raw_plan.get("synthesis_instructions"), 1_600) if isinstance(raw_plan, Mapping) else None
                ),
                "tasks": tasks,
            }
            if raw_plan is not None
            else None
        ),
        "results": results,
        "review": projected_review,
        "review_status": _text(state.get("team_critic_status"), 24),
        "review_error": _text(state.get("team_critic_error"), 600) or None,
        "evidence_merge": project_evidence_merge(state.get("team_evidence_merge")),
        "evidence_merge_status": _text(state.get("team_evidence_merge_status"), 24),
        "review_dispatch": (
            {
                "status": _text(state.get("team_review_dispatch_status"), 24),
                "error": _text(state.get("team_review_dispatch_error"), 600) or None,
            }
            if state.get("team_review_dispatch_status")
            else None
        ),
        "review_dispatch_status": _text(state.get("team_review_dispatch_status"), 24),
        "conflict": projected_conflict,
        "conflict_status": _text(state.get("team_conflict_status"), 24),
        "critic": projected_critic or projected_review,
        "critic_status": _text(state.get("team_critic_status"), 24),
        "review_gate": (
            {
                "status": _text(state.get("team_review_gate_status"), 24),
                "error": _text(state.get("team_review_gate_error"), 600) or None,
            }
            if state.get("team_review_gate_status")
            else None
        ),
        "review_gate_status": _text(state.get("team_review_gate_status"), 24),
        "criteria_assessment": projected_criteria,
        "criteria_status": _text(state.get("team_criteria_status"), 24),
        "criteria_error": _text(state.get("team_criteria_error"), 600) or None,
        "bull_case": project_case(state.get("team_bull_case_review")),
        "bull_case_status": _text(state.get("team_bull_case_status"), 24),
        "bull_case_error": _text(state.get("team_bull_case_error"), 600) or None,
        "bear_case": project_case(state.get("team_bear_case_review")),
        "bear_case_status": _text(state.get("team_bear_case_status"), 24),
        "bear_case_error": _text(state.get("team_bear_case_error"), 600) or None,
        "consensus": projected_consensus,
        "consensus_status": _text(state.get("team_consensus_status"), 24),
        "draft": projected_draft,
        "draft_status": _text(state.get("team_draft_status"), 24),
        "reexecution": projected_reexecution,
        "reexecution_status": _text(state.get("team_reexecution_status"), 24),
        "failure": projected_failure,
        "collaboration": projected_collaboration,
    }


__all__ = ["team_trace"]
