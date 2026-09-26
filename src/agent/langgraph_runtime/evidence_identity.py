"""Shared evidence-id identity and answer-publication helpers.

Evidence ids are generated from a stable action fingerprint and are therefore
longer than the abbreviated ids models sometimes emit in a citation.  The
runtime keeps the canonical id as the source of truth, while accepting a
prefix only when it maps to exactly one successful evidence record.  This
module is deliberately independent from claim validation so every evidence
consumer uses the same identity rule.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from src.tools.base import citation_scoped_evidence_records


EVIDENCE_REFERENCE = re.compile(r"\bev_[A-Za-z0-9_-]+\b")
_CITATION_MARKER = re.compile(
    r"【(?P<prefix>\s*(?:证据\s*[：:]?\s*)?)"
    r"(?P<evidence_id>ev_[^\s【】\[\]()（）,，。！？!?；;：:、]+)"
    r"(?P<suffix>\s*)】"
)
_INTERNAL_EVIDENCE_DIAGNOSTIC = re.compile(
    r"\n\s*\[本轮外部证据关联未能完整通过：.*?"
    r"以上内容应视为未完全核验的部分结论。\]\s*$",
    re.DOTALL,
)


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def evidence_id_from_record(item: Mapping[str, Any]) -> str:
    """Return the canonical id field used by an evidence envelope."""
    return str(item.get("evidence_id") or item.get("id") or "").strip()


def canonical_evidence_ids(values: Iterable[Any]) -> list[str]:
    """Extract unique canonical ids from records or already-normalized ids."""
    ids: list[str] = []
    records = list(values)
    ids.extend(
        str(value or "").strip()
        for value in records
        if not isinstance(value, Mapping)
    )
    for value in citation_scoped_evidence_records(
        item for item in records if isinstance(item, Mapping)
    ):
        if isinstance(value, Mapping):
            ids.append(evidence_id_from_record(value))
        else:
            ids.append(str(value or "").strip())
    return _unique(ids)


def resolve_evidence_id(
    raw_id: Any,
    canonical_ids: Iterable[str],
    *,
    min_prefix_length: int = 8,
) -> str | None:
    """Resolve an exact id or an unambiguous long prefix to its canonical id.

    Exact matches always win, including short ids used by compatibility tests
    and older records.  Prefix matching is intentionally conservative: a
    prefix must contain at least ``min_prefix_length`` characters after
    ``ev_`` and may resolve to one canonical id only.
    """
    candidate = str(raw_id or "").strip()
    available = canonical_evidence_ids(canonical_ids)
    if not candidate:
        return None
    if candidate in available:
        return candidate
    if not candidate.startswith("ev_") or len(candidate[3:]) < min_prefix_length:
        return None
    matches = [value for value in available if value.startswith(candidate)]
    return matches[0] if len(matches) == 1 else None


def evidence_ids_in_text(value: Any) -> list[str]:
    """Extract citation ids, including malformed/non-ASCII ids inside markers."""
    text = str(value or "")
    marker_spans: list[tuple[int, int]] = []
    matches: list[tuple[int, str]] = []
    for marker in _CITATION_MARKER.finditer(text):
        marker_spans.append(marker.span())
        matches.append((marker.start("evidence_id"), marker.group("evidence_id")))
    for reference in EVIDENCE_REFERENCE.finditer(text):
        if any(start <= reference.start() < end for start, end in marker_spans):
            continue
        matches.append((reference.start(), reference.group(0)))
    matches.sort(key=lambda item: item[0])
    return _unique(value for _, value in matches)


def contains_evidence_reference(value: Any) -> bool:
    """Whether text contains a normal or marker-wrapped evidence reference."""
    text = str(value or "")
    return bool(_CITATION_MARKER.search(text) or EVIDENCE_REFERENCE.search(text))


def canonicalize_evidence_markers(
    answer: Any,
    evidence: Iterable[Any],
) -> tuple[str, list[str]]:
    """Replace resolvable marker ids and remove unresolvable marker ids.

    The returned unresolved ids are for the internal audit trail and repair
    feedback.  They are deliberately not left in the published answer, where
    they would become misleading footnotes or leak validator internals.
    """
    text = str(answer or "")
    canonical_ids = canonical_evidence_ids(evidence)
    unresolved: list[str] = []

    def replace(marker: re.Match[str]) -> str:
        raw_id = marker.group("evidence_id")
        resolved = resolve_evidence_id(raw_id, canonical_ids)
        if resolved is None:
            unresolved.append(raw_id)
            return ""
        return marker.group(0).replace(raw_id, resolved, 1)

    normalized = _CITATION_MARKER.sub(replace, text)

    # Structured answers use a typed evidence_ids field, while legacy/plain
    # answers may contain a bare ``ev_…`` token.  Leaving an invalid bare token
    # in the published text is still a false citation even when the marker
    # form is sanitized, so apply the same run-local resolver to both forms.
    def replace_bare(reference: re.Match[str]) -> str:
        raw_id = reference.group(0)
        resolved = resolve_evidence_id(raw_id, canonical_ids)
        if resolved is None:
            unresolved.append(raw_id)
            return ""
        return resolved

    normalized = EVIDENCE_REFERENCE.sub(replace_bare, normalized).strip()
    return normalized, _unique(unresolved)


def strip_internal_evidence_diagnostic(value: Any) -> str:
    """Remove the legacy validator suffix from a persisted answer copy."""
    return _INTERNAL_EVIDENCE_DIAGNOSTIC.sub("", str(value or "")).rstrip()


def prepare_answer_for_client(
    answer: Any,
    evidence: Iterable[Any] = (),
) -> tuple[str, list[str]]:
    """Clean legacy internal diagnostics and normalize visible evidence ids."""
    cleaned = strip_internal_evidence_diagnostic(answer)
    evidence_records = list(evidence)
    return canonicalize_evidence_markers(cleaned, evidence_records)


__all__ = [
    "EVIDENCE_REFERENCE",
    "canonical_evidence_ids",
    "canonicalize_evidence_markers",
    "contains_evidence_reference",
    "evidence_id_from_record",
    "evidence_ids_in_text",
    "prepare_answer_for_client",
    "resolve_evidence_id",
    "strip_internal_evidence_diagnostic",
]
