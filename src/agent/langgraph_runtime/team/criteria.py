"""Server-owned completion-criteria validation for Team execution.

The model may propose a verdict for each criterion, but it cannot advance a
worker or the Team by itself.  This module canonicalizes criterion text from
the plan, rejects missing/duplicate/forged references, and computes the
published status from real successful operations and eligible evidence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.tools.base import citation_scoped_evidence_records, evidence_record_is_eligible

from .contracts import CriteriaAssessment


class CriteriaValidationError(ValueError):
    """A structured criterion proposal cannot be safely evaluated."""


def _record_id(record: Mapping[str, Any]) -> str:
    return str(record.get("evidence_id") or record.get("id") or "").strip()


def _criterion_texts(criteria: Sequence[Any]) -> list[str]:
    # Keep positional identity intact. Silently dropping an empty item would
    # let a malformed plan evade the exact one-check-per-criterion gate.
    return [str(value or "").strip() for value in list(criteria)[:8]]


def _unique(values: Sequence[Any], *, limit: int = 80) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))[:limit]


def _eligible_source_slots(evidence: Sequence[Mapping[str, Any]]) -> dict[int, str]:
    """Resolve model-facing source slots to canonical evidence ids.

    ``source_id`` is deliberately a short-lived, server-assigned slot.  A
    worker/reviewer may cite that slot, but it cannot manufacture the durable
    evidence hash used by the parent ledger.  ``_team_source_id`` is only used
    when the parent Team packet needs to preserve the answer contract's global
    slot numbering across the canonical merge.
    """
    slots: dict[int, str] = {}
    # Match the model-facing packet exactly: search result hits replace their
    # aggregate tool-call envelope before source slots are assigned. Numbering
    # the raw action records here makes valid per-hit citations appear unknown
    # (or, worse, resolve to a different action-level record).
    for position, record in enumerate(citation_scoped_evidence_records(evidence)[:80], start=1):
        if not isinstance(record, Mapping) or not evidence_record_is_eligible(record):
            continue
        evidence_id = _record_id(record)
        if not evidence_id:
            continue
        raw_slot = record.get("_team_source_id")
        try:
            slot = int(raw_slot) if raw_slot is not None else position
        except (TypeError, ValueError):
            slot = position
        if slot > 0:
            slots[slot] = evidence_id
    return slots


def blocked_criteria_evaluation(criteria: Sequence[Any], reason: str) -> dict[str, Any]:
    """Create a fail-closed result when the verifier contract is unavailable."""
    canonical = _criterion_texts(criteria)
    detail = str(reason or "条件校验器没有返回可用结果。")
    checks = [
        {
                "criterion_index": index,
                "criterion": criterion,
                "verdict": "unknown",
                "explanation": detail,
                "source_ids": [],
            }
        for index, criterion in enumerate(canonical, 1)
    ]
    return {
        "status": "blocked",
        "checks": checks[:8],
        "unmet_criteria": canonical[:8],
        "evidence_ids": [],
    }


def block_criteria_evaluation(evaluation: Mapping[str, Any], reason: str) -> dict[str, Any]:
    """Apply a deterministic Team-level guard to an otherwise valid result."""
    detail = str(reason or "服务端前置条件未满足。")
    checks = []
    for raw_check in list(evaluation.get("checks") or [])[:8]:
        if not isinstance(raw_check, Mapping):
            continue
        checks.append(
            {
                "criterion_index": int(raw_check.get("criterion_index") or 0),
                "criterion": str(raw_check.get("criterion") or ""),
                "verdict": "unknown",
                "explanation": f"{str(raw_check.get('explanation') or '').strip()} {detail}".strip(),
                "source_ids": [int(value) for value in list(raw_check.get("source_ids") or []) if str(value).isdigit()],
            }
        )
    unmet = [str(check.get("criterion") or "").strip() for check in checks if str(check.get("criterion") or "").strip()]
    return {
        "status": "blocked",
        "checks": checks,
        "unmet_criteria": unmet[:8],
        "evidence_ids": [],
    }


def validate_criteria_assessment(
    value: Any,
    *,
    criteria: Sequence[Any],
    evidence: Sequence[Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
    require_successful_operation: bool = True,
) -> dict[str, Any]:
    """Validate and evaluate one complete criteria assessment.

    ``value`` is the model's bounded proposal.  The returned criterion text,
    evidence set, and overall status are server-owned projections.  A natural
    language criterion still needs a model to judge semantic fulfillment, but
    the server controls coverage, evidence identity, and the lifecycle gate.
    """
    canonical = _criterion_texts(criteria)
    if not canonical or any(not criterion for criterion in canonical):
        detail = (
            "criteria must contain only non-empty criteria"
            if canonical
            else "criteria must contain at least one non-empty criterion"
        )
        raise CriteriaValidationError(detail)
    try:
        assessment = CriteriaAssessment.model_validate(value)
    except Exception as exc:
        raise CriteriaValidationError(f"invalid CriteriaAssessment: {exc}") from exc

    checks = [check.model_dump(mode="json") for check in assessment.checks]
    expected = list(range(1, len(canonical) + 1))
    actual = sorted(int(check["criterion_index"]) for check in checks)
    if actual != expected:
        raise CriteriaValidationError(f"criteria checks must assess each criterion_index exactly once: {expected}")

    eligible_by_id = {
        _record_id(record): record
        for record in evidence
        if isinstance(record, Mapping) and _record_id(record) and evidence_record_is_eligible(record)
    }
    known_ids = set(eligible_by_id)
    source_slots = _eligible_source_slots(evidence)
    cited_source_ids: list[int] = []
    for check in checks:
        for raw_source_id in list(check.get("source_ids") or []):
            try:
                source_id = int(raw_source_id)
            except (TypeError, ValueError):
                source_id = 0
            if source_id:
                cited_source_ids.append(source_id)
    unknown_source_ids = sorted(set(cited_source_ids) - set(source_slots))
    if unknown_source_ids:
        raise CriteriaValidationError(f"criteria cite unavailable evidence source ids: {unknown_source_ids}")
    has_successful_operation = bool(known_ids) or any(
        isinstance(record, Mapping)
        and record.get("success") is True
        and str(record.get("effect") or "read") != "side_effect"
        for record in records
    )
    normalized_checks: list[dict[str, Any]] = []
    for raw_check in sorted(checks, key=lambda item: int(item["criterion_index"])):
        index = int(raw_check["criterion_index"])
        verdict = str(raw_check.get("verdict") or "unknown")
        source_ids = []
        for raw_source_id in list(raw_check.get("source_ids") or []):
            try:
                source_id = int(raw_source_id)
            except (TypeError, ValueError):
                continue
            if source_id in source_slots and source_id not in source_ids:
                source_ids.append(source_id)
        evidence_ids = _unique(
            [source_slots[source_id] for source_id in source_ids],
            limit=80,
        )
        explanation = str(raw_check.get("explanation") or "").strip()
        if verdict == "pass" and require_successful_operation and not has_successful_operation:
            verdict = "unknown"
            explanation = f"{explanation} 服务端没有发现成功的实际取证操作。".strip()
        elif verdict == "pass" and require_successful_operation and not evidence_ids:
            verdict = "unknown"
            explanation = f"{explanation} 服务端没有发现可追溯的有效证据。".strip()
        normalized_checks.append(
            {
                "criterion_index": index,
                # Never display or persist model-supplied criterion text as the
                # authority; the plan is the only source of this field.
                "criterion": canonical[index - 1],
                "verdict": verdict,
                "explanation": explanation or "服务端没有收到该条件的有效判断。",
                "source_ids": source_ids[:80],
                "evidence_ids": evidence_ids,
            }
        )

    unmet = [check["criterion"] for check in normalized_checks if check["verdict"] != "pass"]
    verdicts = {check["verdict"] for check in normalized_checks}
    if not unmet:
        status = "passed"
    elif "unknown" in verdicts:
        status = "blocked"
    else:
        status = "partial"
    return {
        "status": status,
        "checks": normalized_checks[:8],
        "unmet_criteria": _unique(unmet, limit=8),
        "evidence_ids": _unique(evidence_id for check in normalized_checks for evidence_id in check["evidence_ids"]),
    }


__all__ = [
    "block_criteria_evaluation",
    "CriteriaValidationError",
    "blocked_criteria_evaluation",
    "validate_criteria_assessment",
]
