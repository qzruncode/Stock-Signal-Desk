"""Shared validation contract for runtime and persisted claim projections."""

from collections.abc import Mapping
from typing import Any


def claim_checks_pass(claim: Mapping[str, Any]) -> bool:
    """Include reference integrity, including projections predating that check."""
    # Older projections may omit checks entirely; absence is not a recorded
    # failure, but their required/invalid references must still be inspected.
    checks = claim.get("checks") or {}
    return (
        isinstance(checks, Mapping)
        and all(value is True for value in checks.values())
        and not claim.get("issues")
        and not (claim.get("unresolved_evidence_ids") or claim.get("unresolvedEvidenceIds"))
        and (
            claim.get("requires_evidence", claim.get("requiresEvidence")) is False
            or bool(claim.get("evidence_ids") or claim.get("evidenceIds"))
        )
    )
