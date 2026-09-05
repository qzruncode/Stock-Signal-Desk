"""Deterministic quality scoring for the generic message/tool Agent loop.

The evaluator deliberately reads the same durable facts that the runtime
publishes: terminal status, actual tool observations, evidence records,
evidence references in the answer, and work counters.  It does not reconstruct
a plan, a capability set, or a separate verification workflow.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from src.tools.base import evidence_record_is_eligible


EVALUATOR_VERSION = "langgraph-agent-loop-quality-3.2"
_EVIDENCE_REFERENCE = re.compile(r"\bev_[A-Za-z0-9_-]+\b")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, (list, tuple)) else ()


def _string_set(value: Any) -> set[str]:
    return {str(item).strip() for item in _sequence(value) if str(item).strip()}


def _bounded_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _ratio(passed: int, total: int) -> float:
    return 1.0 if total == 0 else passed / total


def _evidence_id(item: Mapping[str, Any]) -> str:
    return str(item.get("evidence_id") or item.get("id") or "").strip()


def _has_source_reference(item: Mapping[str, Any]) -> bool:
    return any(
        str(ref).strip() and not str(ref).strip().startswith("tool:")
        for ref in _sequence(item.get("source_refs"))
    )


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
    """Score one terminal run against a domain-neutral loop contract."""

    expected = dict(expectations or {})
    run = _mapping(snapshot.get("run"))
    projection = _mapping(snapshot.get("quality_projection"))
    steps = tuple(
        _mapping(item)
        for item in _sequence(snapshot.get("steps"))
        if isinstance(item, Mapping)
    )
    final_text = str(run.get("final_text") or "").strip()

    # Results are observations that actually reached the model, unlike an old
    # planned-action list which may never have been executed.
    tool_results = tuple(
        _mapping(item)
        for item in _sequence(projection.get("tool_results"))
        if isinstance(item, Mapping)
    )
    evidence = tuple(
        _mapping(item)
        for item in _sequence(projection.get("evidence"))
        if isinstance(item, Mapping)
    )
    claim_evidence = tuple(
        _mapping(item)
        for item in _sequence(projection.get("claim_evidence"))
        if isinstance(item, Mapping)
    )
    loop = _mapping(projection.get("loop")) or _mapping(projection.get("budgets"))

    actual_tools = {
        str(item.get("tool_name") or "").strip()
        for item in tool_results
        if str(item.get("tool_name") or "").strip()
    }
    required_tools = _string_set(expected.get("required_tools"))
    forbidden_tools = _string_set(expected.get("forbidden_tools"))
    missing_tools = sorted(required_tools - actual_tools)
    present_forbidden_tools = sorted(forbidden_tools & actual_tools)
    malformed_observations = [
        str(item.get("tool_call_id") or item.get("action_id") or item.get("id") or "")
        for item in tool_results
        if not str(item.get("tool_name") or "").strip()
        or not str(item.get("tool_call_id") or item.get("action_id") or item.get("id") or "").strip()
    ]
    maximum_tool_calls = _bounded_int(expected.get("maximum_tool_calls"), 0)
    observed_tool_calls = max(
        _bounded_int(run.get("tool_call_count")),
        _bounded_int(loop.get("tool_call_count")),
        len(tool_results),
    )
    control_issues = len(missing_tools) + len(present_forbidden_tools) + len(malformed_observations)
    if maximum_tool_calls and observed_tool_calls > maximum_tool_calls:
        control_issues += 1
    control_score = max(0.0, 1.0 - control_issues / max(1, len(required_tools) + 1))

    actual_status = str(run.get("status") or "")
    allowed_statuses = _string_set(expected.get("allowed_statuses") or ["completed"])
    terminal_ok = actual_status in allowed_statuses
    failed_steps = [
        item
        for item in steps
        if str(item.get("status") or "") not in {"completed", "skipped", "reused"}
    ]
    failed_results = [item for item in tool_results if item.get("success") is not True]
    # A failed read is an observation, not necessarily a failed run: the
    # model may inspect it and deliberately use another source.  A strict
    # evaluation case can opt into requiring every attempted operation to pass.
    require_all_tools = bool(expected.get("require_all_tools_succeeded", False))
    execution_ok = terminal_ok and (
        not require_all_tools or (not failed_steps and not failed_results)
    )
    execution_score = 1.0 if execution_ok else 0.5 if terminal_ok else 0.0

    factual_evidence = [
        item
        for item in evidence
        if evidence_record_is_eligible(item)
        and str(item.get("effect") or "read") != "side_effect"
    ]
    evidence_by_id = {
        _evidence_id(item): item
        for item in factual_evidence
        if _evidence_id(item)
    }
    cited_ids = set(_EVIDENCE_REFERENCE.findall(final_text))
    unknown_citations = sorted(cited_ids - set(evidence_by_id))
    cited_evidence_without_source = sorted(
        evidence_id
        for evidence_id in cited_ids & set(evidence_by_id)
        if not _has_source_reference(evidence_by_id[evidence_id])
    )
    evidence_without_source = sorted(
        evidence_id
        for evidence_id, item in evidence_by_id.items()
        if not _has_source_reference(item)
    )
    minimum_evidence = _bounded_int(expected.get("minimum_evidence_items"), 0)
    require_citations = bool(expected.get("require_evidence_citations", True))
    require_sources = bool(expected.get("require_evidence_sources", True))
    ledger_cited_ids = {
        evidence_id
        for claim in claim_evidence
        for evidence_id in _string_set(claim.get("evidence_ids"))
    }
    failed_claim_checks = []
    for index, claim in enumerate(claim_evidence):
        checks = _mapping(claim.get("checks"))
        if not checks or not all(value is True for value in checks.values()):
            failed_claim_checks.append(str(claim.get("claim_id") or f"claim-{index + 1}"))
    ledger_required = (
        str(projection.get("engine") or "") == "langgraph_agent_loop"
        and bool(factual_evidence)
    )
    unmapped_citations = sorted(cited_ids - ledger_cited_ids)
    evidence_checks = [
        len(evidence_by_id) >= minimum_evidence,
        not require_sources or not evidence_without_source,
        not unknown_citations,
        not cited_evidence_without_source,
        not require_citations or not factual_evidence or bool(cited_ids),
        not ledger_required or bool(claim_evidence),
        not unmapped_citations,
        not failed_claim_checks,
    ]
    evidence_score = _ratio(sum(evidence_checks), len(evidence_checks))

    required_terms = _string_set(expected.get("required_answer_terms"))
    forbidden_terms = _string_set(expected.get("forbidden_answer_terms"))
    missing_terms = sorted(term for term in required_terms if term not in final_text)
    present_forbidden_terms = sorted(term for term in forbidden_terms if term in final_text)
    terminal_failure_statuses = {"failed", "blocked", "cancelled", "partial"}
    answer_contract_applicable = bool(final_text) or actual_status not in terminal_failure_statuses
    answer_checks = (
        [bool(final_text), not missing_terms, not present_forbidden_terms]
        if answer_contract_applicable
        else [True]
    )
    answer_score = _ratio(sum(answer_checks), len(answer_checks))

    budget_limits = {
        "provider_call_count": _bounded_int(expected.get("maximum_provider_calls"), 0),
        "tool_call_count": maximum_tool_calls,
        "estimated_token_count": _bounded_int(expected.get("maximum_estimated_tokens"), 0),
        "estimated_cost_micros": _bounded_int(expected.get("maximum_estimated_cost_micros"), 0),
    }
    budget_actual = {
        "provider_call_count": _bounded_int(run.get("provider_call_count")),
        "tool_call_count": observed_tool_calls,
        "estimated_token_count": _bounded_int(run.get("estimated_token_count")),
        "estimated_cost_micros": _bounded_int(run.get("estimated_cost_micros")),
    }
    exceeded_budgets = {
        key: {"actual": budget_actual[key], "maximum": limit}
        for key, limit in budget_limits.items()
        if limit and budget_actual[key] > limit
    }
    work_budget_exhausted = bool(loop.get("work_budget_exhausted"))
    budget_score = 1.0 if not exceeded_budgets and not work_budget_exhausted else 0.0

    dimensions = (
        QualityDimension(
            "control_loop",
            control_score,
            0.20,
            {
                "actual_tools": sorted(actual_tools),
                "missing_required_tools": missing_tools,
                "present_forbidden_tools": present_forbidden_tools,
                "observed_tool_calls": observed_tool_calls,
                "maximum_tool_calls": maximum_tool_calls or None,
                "malformed_observations": malformed_observations,
            },
        ),
        QualityDimension(
            "execution",
            execution_score,
            0.20,
            {
                "status": actual_status,
                "allowed_statuses": sorted(allowed_statuses),
                "failed_step_ids": [str(item.get("step_id") or "") for item in failed_steps],
                "failed_tool_call_ids": [
                    str(item.get("tool_call_id") or item.get("action_id") or item.get("id") or "")
                    for item in failed_results
                ],
            },
        ),
        QualityDimension(
            "evidence_links",
            evidence_score,
            0.30,
            {
                "evidence_count": len(evidence_by_id),
                "minimum_evidence_items": minimum_evidence,
                "cited_evidence_ids": sorted(cited_ids),
                "unknown_citations": unknown_citations,
                "cited_evidence_without_source": cited_evidence_without_source,
                "evidence_without_source": evidence_without_source,
                "claim_count": len(claim_evidence),
                "ledger_required": ledger_required,
                "unmapped_citations": unmapped_citations,
                "failed_claim_checks": failed_claim_checks,
            },
        ),
        QualityDimension(
            "answer_contract",
            answer_score,
            0.15,
            {
                "has_answer": bool(final_text),
                "not_applicable": not answer_contract_applicable,
                "missing_required_terms": missing_terms,
                "present_forbidden_terms": present_forbidden_terms,
            },
        ),
        QualityDimension(
            "budget",
            budget_score,
            0.15,
            {
                "exceeded": exceeded_budgets,
                "work_budget_exhausted": work_budget_exhausted,
                "work_budget_detail": str(loop.get("work_budget_detail") or "")[:500] or None,
            },
        ),
    )
    dimension_payload = {item.name: item.as_dict() for item in dimensions}
    total_score = round(
        sum(item.score * item.weight for item in dimensions) / sum(item.weight for item in dimensions),
        6,
    )

    violations: list[dict[str, Any]] = []
    violation_inputs = (
        ("control_loop_contract_failed", control_score < 1.0, dimension_payload["control_loop"]["details"]),
        ("execution_contract_failed", not execution_ok, dimension_payload["execution"]["details"]),
        ("evidence_link_contract_failed", evidence_score < 1.0, dimension_payload["evidence_links"]["details"]),
        ("answer_contract_failed", answer_score < 1.0, dimension_payload["answer_contract"]["details"]),
        ("budget_contract_failed", budget_score < 1.0, dimension_payload["budget"]["details"]),
    )
    for code, failed, details in violation_inputs:
        if failed:
            violations.append({"code": code, "details": details})

    legacy_expectation_keys = sorted(
        key
        for key in (
            "required_capabilities",
            "forbidden_capabilities",
            "maximum_nodes",
            "maximum_actions",
            "require_complete_coverage",
            "require_claim_evidence_verified",
        )
        if key in expected
    )
    if legacy_expectation_keys:
        violations.append(
            {
                "code": "legacy_expectation_not_executable",
                "details": {"keys": legacy_expectation_keys},
            }
        )

    minimum_score = float(expected.get("minimum_score", 0.85) or 0.85)
    passed = total_score >= minimum_score and not violations
    return {
        "evaluator_version": EVALUATOR_VERSION,
        "status": "passed" if passed else "failed",
        "passed": passed,
        "total_score": total_score,
        "minimum_score": minimum_score,
        "dimensions": dimension_payload,
        "violations": violations,
        "feedback": dict(_mapping(snapshot.get("feedback"))),
    }


__all__ = ["EVALUATOR_VERSION", "score_agent_run_snapshot"]
