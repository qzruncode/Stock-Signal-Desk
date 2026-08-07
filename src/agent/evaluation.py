"""Deterministic quality scoring for the generic LangGraph control loop.

The evaluator reads only server-owned terminal state: dynamic actions, atomic
tool results, evidence records, Claim-Evidence verification, budgets and the
final answer.  It has no domain capability catalog and does not grade with an
LLM.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


EVALUATOR_VERSION = "langgraph-agent-quality-2.0"


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


def _ratio(passed: int, total: int) -> float:
    return 1.0 if total == 0 else passed / total


def score_agent_run_snapshot(
    snapshot: Mapping[str, Any],
    expectations: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score one terminal run against a domain-neutral acceptance contract."""

    expected = dict(expectations or {})
    run = _mapping(snapshot.get("run"))
    projection = _mapping(snapshot.get("quality_projection"))
    steps = tuple(
        _mapping(item)
        for item in _sequence(snapshot.get("steps"))
        if isinstance(item, Mapping)
    )
    final_text = str(run.get("final_text") or "").strip()

    actions = tuple(
        _mapping(item)
        for item in _sequence(projection.get("actions"))
        if isinstance(item, Mapping)
    )
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
    verification = _mapping(projection.get("verification"))
    budgets = _mapping(projection.get("budgets"))

    actual_tools = {
        str(item.get("tool_name") or "").strip()
        for item in (*actions, *tool_results)
        if str(item.get("tool_name") or "").strip()
    }
    required_tools = _string_set(expected.get("required_tools"))
    forbidden_tools = _string_set(expected.get("forbidden_tools"))
    missing_tools = sorted(required_tools - actual_tools)
    present_forbidden_tools = sorted(forbidden_tools & actual_tools)
    maximum_actions = _bounded_int(expected.get("maximum_actions"), 0)
    invalid_actions = [
        str(item.get("action_id") or "")
        for item in actions
        if not str(item.get("action_id") or "").strip()
        or not str(item.get("tool_name") or "").strip()
        or not isinstance(item.get("arguments"), Mapping)
    ]
    plan_round = _bounded_int(budgets.get("plan_round"))
    max_plan_rounds = _bounded_int(budgets.get("max_plan_rounds"))
    control_issues = len(missing_tools) + len(present_forbidden_tools) + len(invalid_actions)
    if maximum_actions and len(actions) > maximum_actions:
        control_issues += 1
    if max_plan_rounds and plan_round > max_plan_rounds:
        control_issues += 1
    control_score = max(0.0, 1.0 - control_issues / max(1, len(required_tools) + 1))

    actual_status = str(run.get("status") or "")
    allowed_statuses = _string_set(expected.get("allowed_statuses") or ["completed"])
    terminal_ok = actual_status in allowed_statuses
    failed_steps = [
        item
        for item in steps
        if str(item.get("status") or "") not in {"completed", "skipped"}
    ]
    failed_results = [item for item in tool_results if item.get("success") is not True]
    require_all_tools = bool(expected.get("require_all_tools_succeeded", True))
    execution_ok = terminal_ok and (
        not require_all_tools or (not failed_steps and not failed_results)
    )
    execution_score = 1.0 if execution_ok else 0.5 if terminal_ok else 0.0

    evidence_by_id = {
        str(item.get("evidence_id") or item.get("id") or ""): item
        for item in evidence
        if item.get("success") is True
        and str(item.get("evidence_id") or item.get("id") or "")
    }
    claims = tuple(
        _mapping(item)
        for item in _sequence(verification.get("claims"))
        if isinstance(item, Mapping)
    )
    material_claims = [item for item in claims if item.get("material") is not False]
    unsupported_claims: list[dict[str, Any]] = []
    for claim in material_claims:
        evidence_ids = [str(item) for item in _sequence(claim.get("evidence_ids")) if str(item)]
        if (
            claim.get("supported") is not True
            or not evidence_ids
            or any(item not in evidence_by_id for item in evidence_ids)
        ):
            unsupported_claims.append(
                {
                    "claim": str(claim.get("claim") or "")[:240],
                    "evidence_ids": evidence_ids,
                }
            )
    evidence_without_source = [
        evidence_id
        for evidence_id, item in evidence_by_id.items()
        if not [
            ref
            for ref in _sequence(item.get("source_refs"))
            if not str(ref).startswith("tool:")
        ]
    ]
    minimum_evidence = _bounded_int(expected.get("minimum_evidence_items"), 0)
    verified = verification.get("accepted") is True
    require_verified = bool(expected.get("require_claim_evidence_verified", True))
    evidence_checks = [
        len(evidence_by_id) >= minimum_evidence,
        not evidence_without_source,
        not unsupported_claims,
        not require_verified or verified,
    ]
    evidence_score = _ratio(sum(evidence_checks), len(evidence_checks))

    required_terms = _string_set(expected.get("required_answer_terms"))
    forbidden_terms = _string_set(expected.get("forbidden_answer_terms"))
    missing_terms = sorted(term for term in required_terms if term not in final_text)
    present_forbidden_terms = sorted(term for term in forbidden_terms if term in final_text)
    answer_checks = [bool(final_text), not missing_terms, not present_forbidden_terms]
    answer_score = _ratio(sum(answer_checks), len(answer_checks))

    budget_limits = {
        "provider_call_count": _bounded_int(expected.get("maximum_provider_calls"), 0),
        "tool_call_count": _bounded_int(expected.get("maximum_tool_calls"), 0),
        "estimated_token_count": _bounded_int(expected.get("maximum_estimated_tokens"), 0),
        "estimated_cost_micros": _bounded_int(expected.get("maximum_estimated_cost_micros"), 0),
    }
    exceeded_budgets = {
        key: {"actual": _bounded_int(run.get(key)), "maximum": limit}
        for key, limit in budget_limits.items()
        if limit and _bounded_int(run.get(key)) > limit
    }
    budget_score = 1.0 if not exceeded_budgets else max(
        0.0,
        1.0 - len(exceeded_budgets) / len(budget_limits),
    )

    dimensions = (
        QualityDimension(
            "control_loop",
            control_score,
            0.15,
            {
                "actual_tools": sorted(actual_tools),
                "missing_required_tools": missing_tools,
                "present_forbidden_tools": present_forbidden_tools,
                "action_count": len(actions),
                "maximum_actions": maximum_actions or None,
                "invalid_actions": invalid_actions,
                "plan_round": plan_round,
                "max_plan_rounds": max_plan_rounds or None,
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
                "failed_action_ids": [str(item.get("action_id") or "") for item in failed_results],
            },
        ),
        QualityDimension(
            "claim_evidence",
            evidence_score,
            0.30,
            {
                "verification_accepted": verified,
                "evidence_count": len(evidence_by_id),
                "minimum_evidence_items": minimum_evidence,
                "material_claim_count": len(material_claims),
                "unsupported_claims": unsupported_claims,
                "evidence_without_source": evidence_without_source,
            },
        ),
        QualityDimension(
            "answer_contract",
            answer_score,
            0.20,
            {
                "has_answer": bool(final_text),
                "missing_required_terms": missing_terms,
                "present_forbidden_terms": present_forbidden_terms,
            },
        ),
        QualityDimension(
            "budget",
            budget_score,
            0.15,
            {"exceeded": exceeded_budgets},
        ),
    )
    dimension_payload = {item.name: item.as_dict() for item in dimensions}
    total_score = round(
        sum(item.score * item.weight for item in dimensions)
        / sum(item.weight for item in dimensions),
        6,
    )

    violations: list[dict[str, Any]] = []
    violation_inputs = (
        ("control_loop_contract_failed", control_score < 1.0, dimension_payload["control_loop"]["details"]),
        ("execution_contract_failed", not execution_ok, dimension_payload["execution"]["details"]),
        ("claim_evidence_contract_failed", evidence_score < 1.0, dimension_payload["claim_evidence"]["details"]),
        ("answer_contract_failed", answer_score < 1.0, dimension_payload["answer_contract"]["details"]),
        ("budget_contract_failed", budget_score < 1.0, dimension_payload["budget"]["details"]),
    )
    for code, failed, details in violation_inputs:
        if failed:
            violations.append({"code": code, "details": details})

    legacy_expectation_keys = sorted(
        key
        for key in ("required_capabilities", "forbidden_capabilities", "maximum_nodes", "require_complete_coverage")
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
