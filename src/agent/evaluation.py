# -*- coding: utf-8 -*-
"""Deterministic end-to-end quality scoring for durable Agent runs.

The evaluator consumes only program-owned projections: compiled capabilities,
step ledger state, typed outcome coverage, evidence counts, terminal status and
explicit user feedback.  It never asks the answer model to grade itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


EVALUATOR_VERSION = "agent-quality-1.0"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, (list, tuple)) else ()


def _string_set(value: Any) -> set[str]:
    return {
        str(item).strip()
        for item in _sequence(value)
        if str(item).strip()
    }


def _bounded_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class QualityDimension:
    name: str
    score: float
    weight: float
    details: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(max(0.0, min(1.0, self.score)), 6),
            "weight": self.weight,
            "details": dict(self.details),
        }


def score_agent_run_snapshot(
    snapshot: Mapping[str, Any],
    expectations: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score one complete run snapshot against an explicit release contract."""

    expected = dict(expectations or {})
    run = _mapping(snapshot.get("run"))
    projection = _mapping(snapshot.get("quality_projection"))
    steps = tuple(
        _mapping(item)
        for item in _sequence(snapshot.get("steps"))
        if isinstance(item, Mapping)
    )
    feedback = _mapping(snapshot.get("feedback"))
    final_text = str(run.get("final_text") or "")

    tasks = tuple(
        _mapping(item)
        for item in _sequence(projection.get("tasks"))
        if isinstance(item, Mapping)
    )
    outcomes = tuple(
        _mapping(item)
        for item in _sequence(projection.get("outcomes"))
        if isinstance(item, Mapping)
    )
    actual_capabilities = {
        str(item.get("capability") or "").strip()
        for item in tasks
        if str(item.get("capability") or "").strip()
    }
    required_capabilities = _string_set(expected.get("required_capabilities"))
    forbidden_capabilities = _string_set(expected.get("forbidden_capabilities"))
    missing_capabilities = sorted(required_capabilities - actual_capabilities)
    present_forbidden = sorted(forbidden_capabilities & actual_capabilities)
    maximum_nodes = _bounded_int(expected.get("maximum_nodes"), 0)
    planning_issues = len(missing_capabilities) + len(present_forbidden)
    if maximum_nodes and len(tasks) > maximum_nodes:
        planning_issues += 1
    planning_score = 1.0 if planning_issues == 0 else max(
        0.0,
        1.0 - planning_issues / max(1, len(required_capabilities) + 1),
    )

    allowed_statuses = _string_set(
        expected.get("allowed_statuses") or ["completed"]
    )
    actual_status = str(run.get("status") or "")
    terminal_ok = actual_status in allowed_statuses
    failed_steps = [
        item
        for item in steps
        if str(item.get("status") or "") not in {"completed", "skipped"}
    ]
    require_all_steps = bool(
        expected.get("require_all_steps_succeeded", True)
    )
    execution_ok = terminal_ok and (
        not require_all_steps or not failed_steps
    )
    execution_score = (
        1.0
        if execution_ok
        else 0.5
        if terminal_ok
        else 0.0
    )

    incomplete_coverage = [
        {
            "task_id": item.get("task_id"),
            "requested": coverage.get("requested"),
            "covered": coverage.get("covered"),
            "missing": list(_sequence(coverage.get("missing"))),
        }
        for item in outcomes
        if isinstance(item.get("coverage"), Mapping)
        for coverage in (_mapping(item.get("coverage")),)
        if coverage.get("complete") is not True
    ]
    outcomes_without_coverage = [
        {"task_id": item.get("task_id")}
        for item in outcomes
        if not isinstance(item.get("coverage"), Mapping)
    ]
    incomplete_coverage.extend(outcomes_without_coverage)
    require_complete_coverage = bool(
        expected.get("require_complete_coverage", True)
    )
    coverage_ok = (
        not require_complete_coverage
        or (bool(outcomes) and not incomplete_coverage)
    )
    missing_typed_outcomes = (
        not outcomes and actual_status == "completed"
    )
    if missing_typed_outcomes:
        # A completed no-tool general response still has one typed terminal
        # outcome in the unified pipeline. Missing outcomes is an audit gap.
        coverage_ok = False
    coverage_score = (
        1.0
        if coverage_ok
        else 0.0
        if not outcomes
        else max(
            0.0,
            1.0 - len(incomplete_coverage) / max(1, len(outcomes)),
        )
    )

    evidence_count = sum(
        _bounded_int(item.get("evidence_count"))
        for item in outcomes
    )
    artifact_count = _bounded_int(projection.get("artifact_count"))
    minimum_evidence = _bounded_int(
        expected.get("minimum_evidence_items"),
        0,
    )
    minimum_artifacts = _bounded_int(
        expected.get("minimum_artifacts"),
        0,
    )
    evidence_ok = (
        evidence_count >= minimum_evidence
        and artifact_count >= minimum_artifacts
    )
    evidence_score = 1.0 if evidence_ok else (
        (
            min(1.0, evidence_count / max(1, minimum_evidence))
            + min(1.0, artifact_count / max(1, minimum_artifacts))
        )
        / 2
    )

    required_terms = _string_set(expected.get("required_answer_terms"))
    forbidden_terms = _string_set(expected.get("forbidden_answer_terms"))
    missing_terms = sorted(
        term for term in required_terms if term not in final_text
    )
    present_forbidden_terms = sorted(
        term for term in forbidden_terms if term in final_text
    )
    answer_ok = not missing_terms and not present_forbidden_terms
    answer_score = 1.0 if answer_ok else max(
        0.0,
        1.0
        - (
            len(missing_terms) + len(present_forbidden_terms)
        )
        / max(1, len(required_terms) + len(forbidden_terms)),
    )

    budget_limits = {
        "provider_call_count": _bounded_int(
            expected.get("maximum_provider_calls"),
            0,
        ),
        "tool_call_count": _bounded_int(
            expected.get("maximum_tool_calls"),
            0,
        ),
        "estimated_token_count": _bounded_int(
            expected.get("maximum_estimated_tokens"),
            0,
        ),
        "estimated_cost_micros": _bounded_int(
            expected.get("maximum_estimated_cost_micros"),
            0,
        ),
    }
    exceeded_budgets = {
        key: {
            "actual": _bounded_int(run.get(key)),
            "maximum": limit,
        }
        for key, limit in budget_limits.items()
        if limit and _bounded_int(run.get(key)) > limit
    }
    budget_score = 1.0 if not exceeded_budgets else max(
        0.0,
        1.0 - len(exceeded_budgets) / len(budget_limits),
    )

    dimensions = (
        QualityDimension(
            "planning",
            planning_score,
            0.20,
            {
                "actual_capabilities": sorted(actual_capabilities),
                "missing_required": missing_capabilities,
                "present_forbidden": present_forbidden,
                "node_count": len(tasks),
                "maximum_nodes": maximum_nodes or None,
            },
        ),
        QualityDimension(
            "execution",
            execution_score,
            0.25,
            {
                "status": actual_status,
                "allowed_statuses": sorted(allowed_statuses),
                "failed_steps": [
                    {
                        "task_id": item.get("task_id"),
                        "step_id": item.get("step_id"),
                        "tool_name": item.get("tool_name"),
                        "status": item.get("status"),
                        "error_code": item.get("error_code"),
                    }
                    for item in failed_steps
                ],
            },
        ),
        QualityDimension(
            "coverage",
            coverage_score,
            0.25,
            {
                "outcome_count": len(outcomes),
                "incomplete": incomplete_coverage,
            },
        ),
        QualityDimension(
            "evidence",
            evidence_score,
            0.15,
            {
                "evidence_count": evidence_count,
                "minimum_evidence_items": minimum_evidence,
                "artifact_count": artifact_count,
                "minimum_artifacts": minimum_artifacts,
            },
        ),
        QualityDimension(
            "answer_contract",
            answer_score,
            0.10,
            {
                "missing_required_terms": missing_terms,
                "present_forbidden_terms": present_forbidden_terms,
            },
        ),
        QualityDimension(
            "budget",
            budget_score,
            0.05,
            {"exceeded": exceeded_budgets},
        ),
    )
    total_score = sum(
        dimension.score * dimension.weight
        for dimension in dimensions
    )
    minimum_score = float(expected.get("minimum_score") or 0.85)
    minimum_score = max(0.0, min(1.0, minimum_score))
    critical_violations: list[dict[str, Any]] = []
    for code, values in (
        ("missing_required_capability", missing_capabilities),
        ("forbidden_capability", present_forbidden),
        ("incomplete_coverage", incomplete_coverage),
        ("required_answer_term_missing", missing_terms),
        ("forbidden_answer_term", present_forbidden_terms),
    ):
        if values:
            critical_violations.append({"code": code, "details": values})
    if not terminal_ok:
        critical_violations.append(
            {
                "code": "terminal_status_mismatch",
                "details": {
                    "actual": actual_status,
                    "allowed": sorted(allowed_statuses),
                },
            }
        )
    if missing_typed_outcomes:
        critical_violations.append(
            {
                "code": "missing_typed_outcomes",
                "details": {
                    "status": actual_status,
                    "outcome_count": 0,
                },
            }
        )
    if require_all_steps and failed_steps:
        critical_violations.append(
            {
                "code": "step_execution_failed",
                "details": [
                    str(item.get("step_id") or "")
                    for item in failed_steps
                ],
            }
        )
    if exceeded_budgets:
        critical_violations.append(
            {"code": "budget_exceeded", "details": exceeded_budgets}
        )

    passed = total_score >= minimum_score and not critical_violations
    return {
        "evaluator_version": EVALUATOR_VERSION,
        "status": "passed" if passed else "failed",
        "passed": passed,
        "total_score": round(total_score, 6),
        "minimum_score": minimum_score,
        "dimensions": {
            dimension.name: dimension.as_dict()
            for dimension in dimensions
        },
        "violations": critical_violations,
        "feedback": dict(feedback),
    }


__all__ = [
    "EVALUATOR_VERSION",
    "score_agent_run_snapshot",
]
