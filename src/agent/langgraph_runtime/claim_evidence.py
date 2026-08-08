"""Domain-neutral claim-to-evidence ledger for final Agent answers.

The generic Agent loop does not need a second planner or a domain verifier to
keep factual output auditable.  Instead, every visible answer fragment that
uses an ``ev_...`` citation is projected into a durable claim record.  The
record keeps the exact supporting tool evidence, source provenance, requested
entity scope and time checks together.  Missing support is a normal model-loop
observation: middleware asks the model to revise in the same loop.

This module deliberately has no stock, industry, or provider vocabulary.  It
only understands the common result/evidence envelope produced by the atomic
tool executor.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence


_EVIDENCE_REFERENCE = re.compile(r"\bev_[A-Za-z0-9_-]+\b")
_SENTENCE_BOUNDARY = re.compile(
    r"(?<=[。！？!?])\s*(?![【\[]\s*(?:证据\s*)?ev_)|(?<=[】\]])\s*(?=[^\n])|\n+"
)
_EXPLICIT_DATE = re.compile(
    r"(?<!\d)(?:(?:19|20)\d{2}(?:[-/.]\d{1,2}(?:[-/.]\d{1,2})?)?|"
    r"(?:19|20)\d{2}年(?:\d{1,2}月(?:\d{1,2}日?)?)?)(?!\d)"
)
_RELATIVE_TIME = re.compile(
    r"(?:最新|当前|今日|今天|截至|本周|本月|latest|current|today|as\s+of)",
    re.IGNORECASE,
)
_COMPACT_IDENTIFIER = re.compile(r"(?<![A-Za-z0-9_])(?:[A-Za-z]+[A-Za-z0-9_-]*\d[A-Za-z0-9_-]*|\d{6,})(?![A-Za-z0-9_])")
_INFERENCE_MARKER = re.compile(r"(?:^|[\s：:])(?:推断|推测|inference|inferred)(?:[：:]|\s)", re.IGNORECASE)
_SENSITIVE_FIELDS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "cookie",
        "password",
        "secret",
        "token",
    }
)


def _id(item: Mapping[str, Any]) -> str:
    return str(item.get("evidence_id") or item.get("id") or "").strip()


def _short(value: Any, limit: int = 1_000) -> str:
    return str(value or "").strip()[:limit]


def _unique(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _actual_source_refs(item: Mapping[str, Any]) -> list[str]:
    """Return real provider/source references rather than local tool labels."""
    return [
        ref
        for ref in _unique([str(value) for value in list(item.get("source_refs") or [])])
        if not ref.startswith("tool:")
    ]


def _entity_fields(item: Mapping[str, Any]) -> list[str]:
    raw = item.get("entities")
    if not isinstance(raw, Mapping):
        return []
    return [
        str(key)[:96]
        for key, value in raw.items()
        if str(key).strip().lower() not in _SENSITIVE_FIELDS
        and value not in (None, "", [], {})
    ][:24]


def _support_text(item: Mapping[str, Any]) -> str:
    """Build an internal comparison view; it is never emitted to the client."""
    payload = {
        "entities": item.get("entities"),
        "data_time": item.get("data_time"),
        "data_time_note": item.get("data_time_note"),
        "source_refs": item.get("source_refs"),
        "result": item.get("result"),
    }
    return json.dumps(payload, ensure_ascii=False, default=str).casefold()


def _compact_time(value: str) -> str:
    return re.sub(r"[^0-9]", "", value)


def _time_mentions(claim: str) -> tuple[list[str], bool]:
    explicit = _unique(_EXPLICIT_DATE.findall(claim))
    return explicit, bool(_RELATIVE_TIME.search(claim))


def _supports_explicit_time(value: str, evidence: Sequence[Mapping[str, Any]]) -> bool:
    token = _compact_time(value)
    if not token:
        return True
    for item in evidence:
        support = _support_text(item)
        support_digits = _compact_time(support)
        if token in support_digits:
            return True
    return False


def _supports_relative_time(evidence: Sequence[Mapping[str, Any]]) -> bool:
    for item in evidence:
        if not item.get("data_time"):
            continue
        if item.get("freshness_unknown") is True or item.get("is_stale") is True:
            continue
        return True
    return False


def _explicit_identifiers(claim: str) -> list[str]:
    """Extract mechanically checkable identifiers, not ordinary quoted prose.

    Quotation marks frequently carry a source's wording or a model's label.
    Treating every quoted phrase as an entity makes the validator reject valid
    prose for a lexical mismatch rather than a provenance problem.  Compact
    alphanumeric IDs and numeric codes remain checkable across all domains.
    """
    without_citations = _EVIDENCE_REFERENCE.sub("", claim)
    compact = _COMPACT_IDENTIFIER.findall(without_citations)
    return _unique(compact)[:16]


def _claim_fragments(answer: str) -> list[str]:
    """Return auditable answer units without letting time claims borrow evidence.

    Citation-bearing fragments are always material claims.  A fragment that
    makes an explicit or relative time assertion is material as well, even if
    the model omitted a citation.  Keeping that fragment in the ledger is what
    prevents an uncited introduction such as ``基于最新资料`` from being
    accidentally validated by an unrelated dated citation later in the
    answer.
    """
    fragments: list[str] = []
    for raw in _SENTENCE_BOUNDARY.split(answer):
        candidate = raw.strip()
        explicit_times, relative_time = _time_mentions(candidate)
        if candidate and (
            _EVIDENCE_REFERENCE.search(candidate)
            or explicit_times
            or relative_time
        ):
            fragments.append(candidate)
    # A malformed answer can place a citation immediately after a heading or
    # in a line without a sentence boundary.  Preserve it as one auditable
    # fragment rather than silently dropping the citation from the ledger.
    if not fragments and _EVIDENCE_REFERENCE.search(answer):
        fragments.append(answer.strip())
    return fragments


def build_claim_evidence_ledger(
    answer: str,
    evidence: Sequence[Mapping[str, Any]],
    tool_results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Project citation-bearing answer fragments and validate their support.

    The validation is intentionally mechanical and provider-neutral:

    * cited IDs must come from successful factual evidence;
    * every cited evidence item must retain a real source and a successful
      matching tool result;
    * the evidence must retain request/entity scope;
    * explicit dates in the claim must occur in supporting evidence; and
    * current/latest claims require a non-stale source data timestamp.

    It does not attempt to infer a financial thesis or force an answer into a
    fixed domain template.  The mapping is retained for audit and passed back
    to the model only as concise repair feedback when one of these invariants
    is broken.
    """
    successful = {
        _id(item): item
        for item in evidence
        if item.get("success") is True and _id(item)
    }
    results_by_action = {
        str(item.get("action_id") or item.get("id") or "").strip(): item
        for item in tool_results
        if str(item.get("action_id") or item.get("id") or "").strip()
    }
    issues: list[str] = []
    claims: list[dict[str, Any]] = []
    cited_evidence_ids = _unique(_EVIDENCE_REFERENCE.findall(answer))
    cited_evidence = [
        successful[evidence_id]
        for evidence_id in cited_evidence_ids
        if evidence_id in successful
    ]
    for index, fragment in enumerate(_claim_fragments(answer), start=1):
        evidence_ids = _unique(_EVIDENCE_REFERENCE.findall(fragment))
        missing = [value for value in evidence_ids if value not in successful]
        if missing:
            issues.append("引用了不存在或失败的 evidence_id: " + ", ".join(missing))
        supporting = [successful[value] for value in evidence_ids if value in successful]
        source_refs = _unique(
            [ref for item in supporting for ref in _actual_source_refs(item)]
        )
        entity_fields = _unique(
            [field for item in supporting for field in _entity_fields(item)]
        )
        related_results = [
            results_by_action.get(str(item.get("action_id") or "").strip())
            for item in supporting
        ]
        tool_success = bool(supporting) and all(
            result is not None and result.get("success") is True
            for result in related_results
        )
        source_ok = bool(source_refs)
        entity_scope_ok = bool(entity_fields) or bool(source_refs)
        explicit_times, relative_time = _time_mentions(fragment)
        temporal_claim = bool(explicit_times) or relative_time
        if temporal_claim and not evidence_ids:
            issues.append(
                "带有明确时间口径的结论没有紧邻的 evidence_id"
            )
        unsupported_times = [
            value for value in explicit_times if not _supports_explicit_time(value, supporting)
        ]
        time_ok = bool(evidence_ids) if temporal_claim else True
        time_ok = time_ok and not unsupported_times and (
            not relative_time or _supports_relative_time(supporting)
        )
        identifiers = _explicit_identifiers(fragment)
        support_text = "\n".join(_support_text(item) for item in supporting)
        unsupported_identifiers = [
            value
            for value in identifiers
            if value.casefold() not in support_text
        ]
        entity_ok = entity_scope_ok and not unsupported_identifiers

        if supporting and not source_ok:
            issues.append(
                "引用证据缺少真实来源: " + ", ".join(evidence_ids)
            )
        if supporting and not tool_success:
            issues.append(
                "引用证据未关联到成功工具结果: " + ", ".join(evidence_ids)
            )
        if supporting and not entity_scope_ok:
            issues.append(
                "引用证据缺少实体范围: " + ", ".join(evidence_ids)
            )
        if unsupported_identifiers:
            issues.append(
                "结论中的显式标识未出现在引用证据中: " + ", ".join(unsupported_identifiers)
            )
        if unsupported_times:
            issues.append(
                "结论中的时间无法在引用证据中核对: " + ", ".join(unsupported_times)
            )
        if relative_time and not _supports_relative_time(supporting):
            issues.append("结论使用了当前/最新时间口径，但引用证据没有可用数据时间")

        claims.append(
            {
                "claim_id": f"claim_{index}",
                "text": fragment[:2_000],
                "kind": "inference" if _INFERENCE_MARKER.search(fragment) else "fact",
                "evidence_ids": evidence_ids,
                "entity_fields": entity_fields,
                "time_references": explicit_times,
                "uses_relative_time": relative_time,
                "checks": {
                    "tool_success": tool_success,
                    "source": source_ok,
                    "entity_scope": entity_ok,
                    "time": time_ok,
                },
                "evidence": [
                    {
                        "evidence_id": _id(item),
                        "tool_name": _short(item.get("tool_name"), 120),
                        "data_time": _short(item.get("data_time"), 160) or None,
                        "source_refs": _actual_source_refs(item)[:8],
                    }
                    for item in supporting
                ],
            }
        )

    return {
        "claims": claims,
        "issues": _unique(issues),
        "cited_evidence_ids": cited_evidence_ids,
        "fact_claim_count": sum(1 for item in claims if item["kind"] == "fact"),
        "inference_claim_count": sum(1 for item in claims if item["kind"] == "inference"),
    }


__all__ = ["build_claim_evidence_ledger"]
