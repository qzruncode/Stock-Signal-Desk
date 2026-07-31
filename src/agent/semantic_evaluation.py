# -*- coding: utf-8 -*-
"""Deterministic scoring for semantic-planner golden cases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class SemanticGoldenCase:
    case_id: str
    prompt: str
    required_capabilities: frozenset[str]
    forbidden_capabilities: frozenset[str]
    maximum_nodes: int = 12
    required_dependencies: frozenset[tuple[str, str]] = frozenset()
    expected_effects: tuple[tuple[str, str], ...] = ()
    required_scope_terms: frozenset[str] = frozenset()
    required_question_type: str | None = None
    required_evidence_dimensions: frozenset[str] = frozenset()
    required_artifact_resources: frozenset[str] = frozenset()
    maximum_plan_revisions: int = 2

    @classmethod
    def from_value(cls, value: Mapping[str, Any]) -> "SemanticGoldenCase":
        return cls(
            case_id=str(value["case_id"]),
            prompt=str(value["prompt"]),
            required_capabilities=frozenset(str(item) for item in value.get("required_capabilities", ())),
            forbidden_capabilities=frozenset(str(item) for item in value.get("forbidden_capabilities", ())),
            maximum_nodes=max(1, int(value.get("maximum_nodes", 12))),
            required_dependencies=frozenset(
                (str(item[0]), str(item[1]))
                for item in value.get("required_dependencies", ())
                if isinstance(item, (list, tuple)) and len(item) == 2
            ),
            expected_effects=tuple(
                (str(capability), str(effect))
                for capability, effect in dict(value.get("expected_effects") or {}).items()
            ),
            required_scope_terms=frozenset(str(item) for item in value.get("required_scope_terms", ())),
            required_question_type=(
                str(value["required_question_type"]) if value.get("required_question_type") else None
            ),
            required_evidence_dimensions=frozenset(
                str(item)
                for item in value.get(
                    "required_evidence_dimensions",
                    (),
                )
            ),
            required_artifact_resources=frozenset(
                str(item)
                for item in value.get(
                    "required_artifact_resources",
                    (),
                )
            ),
            maximum_plan_revisions=max(
                0,
                int(value.get("maximum_plan_revisions", 2)),
            ),
        )


def score_semantic_case(
    case: SemanticGoldenCase,
    capabilities: Sequence[str],
    *,
    dependencies: Sequence[tuple[str, str]] = (),
    effects: Mapping[str, str] | None = None,
    scope_text: str = "",
    question_type: str | None = None,
    evidence_dimensions: Sequence[str] = (),
    plan_revisions: int = 0,
    recovery_effects: Sequence[str] = (),
    artifact_resources: Sequence[str] = (),
) -> dict[str, Any]:
    actual = tuple(str(item) for item in capabilities)
    actual_set = set(actual)
    missing = sorted(case.required_capabilities - actual_set)
    forbidden = sorted(case.forbidden_capabilities & actual_set)
    duplicates = sorted({item for item in actual if actual.count(item) > 1})
    too_many_nodes = len(actual) > case.maximum_nodes
    missing_dependencies = sorted(
        case.required_dependencies - {(str(source), str(target)) for source, target in dependencies}
    )
    actual_effects = dict(effects or {})
    effect_mismatches = [
        {
            "capability": capability,
            "expected": expected,
            "actual": actual_effects.get(capability),
        }
        for capability, expected in case.expected_effects
        if actual_effects.get(capability) != expected
    ]
    missing_scope_terms = sorted(term for term in case.required_scope_terms if term not in scope_text)
    question_type_mismatch = case.required_question_type is not None and question_type != case.required_question_type
    missing_evidence_dimensions = sorted(
        case.required_evidence_dimensions - {str(item) for item in evidence_dimensions}
    )
    too_many_revisions = plan_revisions > case.maximum_plan_revisions
    unsafe_recovery_effects = sorted({str(item) for item in recovery_effects if str(item) != "read"})
    missing_artifact_resources = sorted(case.required_artifact_resources - {str(item) for item in artifact_resources})
    return {
        "case_id": case.case_id,
        "passed": not (
            missing
            or forbidden
            or duplicates
            or too_many_nodes
            or missing_dependencies
            or effect_mismatches
            or missing_scope_terms
            or question_type_mismatch
            or missing_evidence_dimensions
            or too_many_revisions
            or unsafe_recovery_effects
            or missing_artifact_resources
        ),
        "actual_capabilities": list(actual),
        "missing_required": missing,
        "present_forbidden": forbidden,
        "duplicates": duplicates,
        "too_many_nodes": too_many_nodes,
        "missing_dependencies": [list(item) for item in missing_dependencies],
        "effect_mismatches": effect_mismatches,
        "missing_scope_terms": missing_scope_terms,
        "question_type_mismatch": question_type_mismatch,
        "missing_evidence_dimensions": missing_evidence_dimensions,
        "too_many_revisions": too_many_revisions,
        "unsafe_recovery_effects": unsafe_recovery_effects,
        "missing_artifact_resources": missing_artifact_resources,
    }


def summarize_semantic_scores(
    scores: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    total = len(scores)
    passed = sum(bool(score.get("passed")) for score in scores)
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": (passed / total) if total else 0.0,
        "release_gate_passed": bool(total) and passed == total,
    }


__all__ = [
    "SemanticGoldenCase",
    "score_semantic_case",
    "summarize_semantic_scores",
]
