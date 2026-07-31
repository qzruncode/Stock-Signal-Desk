# -*- coding: utf-8 -*-
"""Deterministic Goal Contract evaluation for the unified Agent runtime.

The planner decides *what* must be answered.  This module owns the
program-level completion rule after execution: evidence is normalized into a
ledger, every mandatory claim is evaluated against that ledger, and only
bounded read-only capability expansion may be proposed.
"""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
from typing import Iterable, Mapping, Sequence

from src.agent.orchestrator_v2.contracts import (
    Capability,
    ClaimAssessmentV2,
    ClaimStatus,
    EffectLevel,
    EvidenceDimension,
    EvidenceLedgerEntryV2,
    EvidenceQuality,
    GoalBudgetV2,
    GoalContractV2,
    GoalDisposition,
    GoalEvaluationV2,
    GoalRunStateV2,
    GoalTerminalReason,
    OutcomeStatus,
    TaskOutcomeV2,
)
from src.agent.orchestrator_v2.registry import capability_for


def _evidence_id(
    *,
    task_id: str,
    capability: Capability,
    source: str,
    locator: str | None,
    summary: str | None,
) -> str:
    payload = json.dumps(
        {
            "task_id": task_id,
            "capability": capability.value,
            "source": source,
            "locator": locator,
            "summary": summary,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "e_" + sha256(payload.encode("utf-8")).hexdigest()[:16]


def _quality(outcome: TaskOutcomeV2) -> EvidenceQuality:
    if outcome.status == OutcomeStatus.SUCCEEDED:
        return EvidenceQuality.AUTHORITATIVE
    if outcome.status == OutcomeStatus.PARTIAL and (outcome.evidence or outcome.result is not None):
        return EvidenceQuality.DEGRADED
    return EvidenceQuality.FAILED


def build_evidence_ledger_v2(
    task_outcomes: Sequence[tuple[Capability, TaskOutcomeV2]],
) -> tuple[EvidenceLedgerEntryV2, ...]:
    """Normalize task outcomes into stable evidence records.

    A successful structured task is itself evidence even when its adapter did
    not emit source rows.  Failed tasks are retained as negative operational
    evidence so the evaluator can explain why a claim stayed unresolved.
    """

    entries: list[EvidenceLedgerEntryV2] = []
    seen: set[str] = set()
    for capability, outcome in task_outcomes:
        spec = capability_for(capability)
        quality = _quality(outcome)
        errors = tuple(item.message for item in outcome.errors)
        source_rows = tuple(outcome.evidence)
        normalized_rows = [
            (
                row.source,
                row.locator,
                row.observed_at,
                row.summary,
            )
            for row in source_rows
        ]
        if not normalized_rows:
            # Keep the program-owned result boundary visible in the ledger
            # without pretending it is an external citation.
            normalized_rows.append(
                (
                    f"capability:{capability.value}",
                    None,
                    None,
                    ("结构化能力结果已形成" if quality != EvidenceQuality.FAILED else "能力执行未形成可用证据"),
                )
            )
        for source, locator, observed_at, summary in normalized_rows:
            evidence_id = _evidence_id(
                task_id=outcome.task_id,
                capability=capability,
                source=source,
                locator=locator,
                summary=summary,
            )
            if evidence_id in seen:
                continue
            seen.add(evidence_id)
            entries.append(
                EvidenceLedgerEntryV2(
                    evidence_id=evidence_id,
                    task_id=outcome.task_id,
                    capability=capability,
                    dimensions=tuple(
                        sorted(
                            spec.evidence_dimensions,
                            key=lambda item: item.value,
                        )
                    ),
                    quality=quality,
                    source=source,
                    locator=locator,
                    observed_at=observed_at,
                    summary=summary,
                    warnings=outcome.warnings,
                    errors=errors,
                )
            )
    return tuple(entries)


def _assess_claims(
    goal: GoalContractV2,
    evidence_ledger: Sequence[EvidenceLedgerEntryV2],
) -> tuple[ClaimAssessmentV2, ...]:
    usable_by_dimension: dict[
        EvidenceDimension,
        list[EvidenceLedgerEntryV2],
    ] = defaultdict(list)
    for entry in evidence_ledger:
        if entry.quality == EvidenceQuality.FAILED:
            continue
        for dimension in entry.dimensions:
            usable_by_dimension[dimension].append(entry)

    assessments: list[ClaimAssessmentV2] = []
    for claim in goal.claims:
        supported_dimensions = {
            dimension for dimension in claim.required_dimensions if usable_by_dimension.get(dimension)
        }
        missing = tuple(dimension for dimension in claim.required_dimensions if dimension not in supported_dimensions)
        supporting_entries = {
            entry.evidence_id: entry for dimension in supported_dimensions for entry in usable_by_dimension[dimension]
        }
        if not missing:
            has_degraded = any(entry.quality == EvidenceQuality.DEGRADED for entry in supporting_entries.values())
            status = ClaimStatus.SUPPORTED
            confidence = 0.68 if has_degraded else 0.9
            rationale = (
                "必需证据维度均已覆盖，其中部分来源处于降级状态。"
                if has_degraded
                else "必需证据维度均由成功的结构化能力结果覆盖。"
            )
        elif supported_dimensions:
            status = ClaimStatus.PARTIAL
            confidence = max(
                0.2,
                0.6 * (len(supported_dimensions) / len(claim.required_dimensions)),
            )
            rationale = "已有部分证据，但仍缺少完成该结论所需的证据维度。"
        else:
            status = ClaimStatus.UNKNOWN
            confidence = 0.05
            rationale = "当前运行尚未形成该结论所需的可用证据。"
        assessments.append(
            ClaimAssessmentV2(
                claim_id=claim.claim_id,
                status=status,
                confidence=round(confidence, 3),
                supported_by=tuple(sorted(supporting_entries)),
                missing_dimensions=missing,
                rationale=rationale,
            )
        )
    return tuple(assessments)


def _expansion_candidates(
    *,
    missing_dimensions: Iterable[EvidenceDimension],
    attempted_capabilities: frozenset[Capability],
    max_candidates: int,
) -> tuple[Capability, ...]:
    missing = frozenset(missing_dimensions)
    candidates: list[tuple[int, int, Capability]] = []
    fallback_rank: dict[Capability, int] = {}
    for attempted in attempted_capabilities:
        for index, fallback in enumerate(capability_for(attempted).fallback_capabilities):
            fallback_rank.setdefault(fallback, index)

    for capability in Capability:
        if capability in attempted_capabilities:
            continue
        spec = capability_for(capability)
        if spec.execution_policy.effect != EffectLevel.READ or not spec.auto_expandable or spec.input_resources:
            continue
        covered = len(missing & spec.evidence_dimensions)
        if not covered:
            continue
        # Every automatic expansion must improve typed evidence coverage.
        # Declared fallbacks only determine priority among real improvements;
        # they never justify unrelated retrieval.
        candidates.append(
            (
                -covered,
                fallback_rank.get(capability, 10_000),
                capability,
            )
        )
    candidates.sort(key=lambda item: (item[0], item[1], item[2].value))
    return tuple(item[2] for item in candidates[:max_candidates])


def evaluate_goal_v2(
    *,
    goal: GoalContractV2,
    task_outcomes: Sequence[tuple[Capability, TaskOutcomeV2]],
    attempted_capabilities: Iterable[Capability],
    budget: GoalBudgetV2,
    plan_revision: int,
    max_expansion_capabilities: int = 4,
) -> GoalRunStateV2:
    """Evaluate completion and propose at most one bounded read expansion."""

    attempted = tuple(dict.fromkeys(attempted_capabilities))
    evidence_ledger = build_evidence_ledger_v2(task_outcomes)
    assessments = _assess_claims(goal, evidence_ledger)
    mandatory = {claim.claim_id for claim in goal.claims if claim.mandatory}
    unresolved = [
        assessment
        for assessment in assessments
        if (
            assessment.claim_id in mandatory and assessment.status not in {ClaimStatus.SUPPORTED, ClaimStatus.CONTESTED}
        )
    ]
    missing_dimensions = tuple(
        sorted(
            {dimension for assessment in unresolved for dimension in assessment.missing_dimensions},
            key=lambda item: item.value,
        )
    )

    if not unresolved:
        has_degraded = any(entry.quality == EvidenceQuality.DEGRADED for entry in evidence_ledger)
        terminal_reason = (
            GoalTerminalReason.COMPLETED_WITH_UNCERTAINTY if has_degraded else GoalTerminalReason.GOAL_SATISFIED
        )
        evaluation = GoalEvaluationV2(
            disposition=GoalDisposition.COMPLETE,
            assessments=assessments,
            terminal_reason=terminal_reason,
            rationale=(
                "所有强制结论均已有对应证据支持。"
                if not has_degraded
                else "所有强制结论均已覆盖，部分证据降级，答案需保留不确定性。"
            ),
        )
    else:
        proposed = (
            _expansion_candidates(
                missing_dimensions=missing_dimensions,
                attempted_capabilities=frozenset(attempted),
                max_candidates=max_expansion_capabilities,
            )
            if budget.can_revise
            else ()
        )
        if proposed:
            evaluation = GoalEvaluationV2(
                disposition=GoalDisposition.EXPAND_READS,
                assessments=assessments,
                missing_dimensions=missing_dimensions,
                proposed_capabilities=proposed,
                rationale=("强制结论仍有证据缺口；只追加未执行过、无副作用且能改善覆盖的读取能力。"),
            )
        else:
            terminal_reason = (
                GoalTerminalReason.BUDGET_EXHAUSTED if not budget.can_revise else GoalTerminalReason.NO_SAFE_EXPANSION
            )
            evaluation = GoalEvaluationV2(
                disposition=GoalDisposition.BEST_EFFORT,
                assessments=assessments,
                missing_dimensions=missing_dimensions,
                terminal_reason=terminal_reason,
                rationale=(
                    "证据仍不完整，但已没有预算内、无副作用且能改善覆盖的新读取能力；"
                    "必须返回当前最佳可用答案并明确边界。"
                ),
            )

    return GoalRunStateV2(
        goal=goal,
        claim_ledger=assessments,
        evidence_ledger=evidence_ledger,
        attempted_capabilities=attempted,
        plan_revision=plan_revision,
        budget=budget,
        evaluation=evaluation,
        terminal_reason=evaluation.terminal_reason,
    )


__all__ = [
    "build_evidence_ledger_v2",
    "evaluate_goal_v2",
]
