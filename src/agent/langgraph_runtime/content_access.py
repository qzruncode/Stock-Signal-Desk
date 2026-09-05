"""Deterministic reference-to-content access rules for the Agent loop.

The company news and research-report operations intentionally return source
references rather than article/report bodies. This module keeps that
distinction explicit and gives the runtime a provenance-preserving set of
candidate URLs plus the URLs the model chose to read.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..reference_access import canonical_url, reference_access_status
from .evidence_identity import evidence_ids_in_text, evidence_id_from_record, resolve_evidence_id


REFERENCE_ONLY_TOOL_KINDS: dict[str, str] = {
    "read_company_news_akshare": "article",
    "read_company_research_reports_akshare": "document",
}
CONTENT_READER_TOOLS = frozenset({"read_web_source"})
DEFAULT_CONTENT_ACCESS_REPAIR_LIMIT = 2

_CONTENT_FIELDS = frozenset(
    {
        "content",
        "content_text",
        "content_html",
        "content_chunks",
        "chunks",
    }
)


def _text(value: Any, limit: int = 2_000) -> str:
    return str(value or "").strip()[:limit]


def _cited_evidence_ids(
    answer: str,
    evidence: Sequence[Mapping[str, Any]],
) -> set[str]:
    canonical_ids = [
        evidence_id_from_record(item)
        for item in evidence
        if isinstance(item, Mapping) and evidence_id_from_record(item)
    ]
    resolved: set[str] = set()
    for raw_id in evidence_ids_in_text(answer):
        evidence_id = resolve_evidence_id(raw_id, canonical_ids)
        if evidence_id:
            resolved.add(evidence_id)
    return resolved


def _non_empty(value: Any, *, depth: int = 0) -> bool:
    if depth > 4 or value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return any(_non_empty(item, depth=depth + 1) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_non_empty(item, depth=depth + 1) for item in value)
    return bool(value)


def _append_url(urls: list[str], value: Any) -> None:
    text = _text(value)
    if canonical_url(text) and text not in urls:
        urls.append(text)


def _candidate_from_item(
    *,
    record: Mapping[str, Any],
    item: Mapping[str, Any] | None,
    url: str,
    kind: str,
) -> dict[str, Any]:
    item = item or {}
    return {
        "url": url,
        "kind": kind,
        "title": _text(item.get("title") or item.get("name"), 240),
        "tool_name": _text(record.get("tool_name"), 120),
        "action_id": _text(record.get("action_id") or record.get("id"), 120),
    }


def _result_mapping(record: Mapping[str, Any]) -> Mapping[str, Any]:
    result = record.get("result")
    return result if isinstance(result, Mapping) else {}


def _content_access_contract(record: Mapping[str, Any]) -> Mapping[str, Any]:
    direct = record.get("content_access")
    if isinstance(direct, Mapping):
        return direct
    nested = _result_mapping(record).get("content_access")
    return nested if isinstance(nested, Mapping) else {}


def _reference_kind(record: Mapping[str, Any]) -> str | None:
    tool_name = _text(record.get("tool_name"), 120)
    known_kind = REFERENCE_ONLY_TOOL_KINDS.get(tool_name)
    if known_kind:
        return known_kind
    access = _content_access_contract(record)
    mode = _text(access.get("mode") or access.get("access_mode"), 80).lower()
    required = access.get("content_read_required")
    if mode not in {"reference_only", "reference", "metadata_only"} or required is False:
        return None
    return "document" if tool_name.endswith(("report", "reports")) else "article"


def _reference_read_required(record: Mapping[str, Any]) -> bool:
    """Whether a successful reference result is body-backed before use."""
    kind = _reference_kind(record)
    if not kind:
        return False
    access = _content_access_contract(record)
    return access.get("content_read_required") is not False


def reference_candidates(tool_results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Extract candidate article/report URLs from successful reference tools."""
    candidates: list[dict[str, Any]] = []
    by_canonical: dict[str, dict[str, Any]] = {}
    for raw_record in tool_results:
        record = raw_record if isinstance(raw_record, Mapping) else {}
        kind = _reference_kind(record)
        if not kind or record.get("success") is not True or not _reference_read_required(record):
            continue
        result = _result_mapping(record)
        items = result.get("items")
        if isinstance(items, Sequence) and not isinstance(items, (str, bytes, bytearray)):
            for raw_item in items:
                item = raw_item if isinstance(raw_item, Mapping) else {}
                url = _text(item.get("url") or item.get("link"))
                canonical = canonical_url(url)
                if not canonical:
                    continue
                candidate = by_canonical.get(canonical)
                if candidate is None:
                    candidate = _candidate_from_item(record=record, item=item, url=url, kind=kind)
                    by_canonical[canonical] = candidate
                    candidates.append(candidate)
                elif str(record.get("action_id") or record.get("id") or ""):
                    action_id = _text(record.get("action_id") or record.get("id"), 120)
                    action_ids = candidate.get("action_ids") or []
                    if action_id and action_id != candidate.get("action_id") and action_id not in action_ids:
                        action_ids = candidate.setdefault("action_ids", action_ids)
                        action_ids.append(action_id)
        reference_links = result.get("reference_links")
        links = (
            reference_links
            if isinstance(reference_links, Sequence)
            and not isinstance(reference_links, (str, bytes, bytearray))
            else []
        )
        for raw_url in links:
            url = _text(raw_url)
            canonical = canonical_url(url)
            if not canonical:
                continue
            candidate = by_canonical.get(canonical)
            if candidate is None:
                candidate = _candidate_from_item(record=record, item=None, url=url, kind=kind)
                by_canonical[canonical] = candidate
                candidates.append(candidate)
            elif str(record.get("action_id") or record.get("id") or ""):
                action_id = _text(record.get("action_id") or record.get("id"), 120)
                action_ids = candidate.get("action_ids") or []
                if action_id and action_id != candidate.get("action_id") and action_id not in action_ids:
                    action_ids = candidate.setdefault("action_ids", action_ids)
                    action_ids.append(action_id)
    return candidates


def _read_call_urls(record: Mapping[str, Any]) -> list[str]:
    urls: list[str] = []
    arguments = record.get("arguments") if isinstance(record.get("arguments"), Mapping) else {}
    result = record.get("result") if isinstance(record.get("result"), Mapping) else {}
    for container in (arguments, result):
        for key in ("url", "link", "requested_url", "final_url"):
            _append_url(urls, container.get(key))
        source = container.get("source")
        if isinstance(source, Mapping):
            _append_url(urls, source.get("url"))
    return urls


def content_read_call_urls(tool_results: Sequence[Mapping[str, Any]]) -> set[str]:
    """Return every URL the model selected for a content-reader call."""
    selected_urls: set[str] = set()
    for raw_record in tool_results:
        record = raw_record if isinstance(raw_record, Mapping) else {}
        if _text(record.get("tool_name"), 120) not in CONTENT_READER_TOOLS:
            continue
        selected_urls.update(
            normalized
            for normalized in (canonical_url(url) for url in _read_call_urls(record))
            if normalized
        )
    return selected_urls


def successful_content_read_urls(tool_results: Sequence[Mapping[str, Any]]) -> set[str]:
    """Return URLs whose reader call succeeded and extracted non-empty content."""
    read_urls: set[str] = set()
    for raw_record in tool_results:
        record = raw_record if isinstance(raw_record, Mapping) else {}
        if (
            _text(record.get("tool_name"), 120) not in CONTENT_READER_TOOLS
            or record.get("success") is not True
        ):
            continue
        result = record.get("result") if isinstance(record.get("result"), Mapping) else {}
        access = result.get("content_access")
        if isinstance(access, Mapping) and "content_extracted" in access:
            if access.get("content_extracted") is not True:
                continue
            try:
                if int(access.get("content_length") or 0) <= 0:
                    continue
            except (TypeError, ValueError):
                continue
        else:
            # Keep compatibility with historical reader records that predate
            # the explicit content_access contract.
            has_content = any(
                _non_empty(result.get(field))
                for field in _CONTENT_FIELDS
            )
            if not has_content:
                continue
        read_urls.update(
            normalized
            for normalized in (canonical_url(url) for url in _read_call_urls(record))
            if normalized
        )
    return read_urls


def required_content_access_targets(
    *,
    answer: str,
    evidence: Sequence[Mapping[str, Any]],
    tool_results: Sequence[Mapping[str, Any]],
    targets: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return the body URLs required by cited reference-only actions.

    A reference-only operation can return a list of many candidate URLs.  The
    runtime must not turn that list into an implicit fan-out requirement: doing
    so caused unrelated pages to become false "missing data" errors.  A
    multi-link action contributes only URLs explicitly selected by the model;
    a single-link action remains unambiguous and is required directly.
    """
    access_by_action = cited_reference_access_status(
        answer=answer,
        evidence=evidence,
        tool_results=tool_results,
        targets=targets,
    )
    required_urls = {
        canonical_url(target.get("url"))
        for access in access_by_action.values()
        for target in access["required_targets"]
        if canonical_url(target.get("url"))
    }
    return [
        dict(target)
        for raw_target in targets
        if isinstance(raw_target, Mapping)
        for target in [raw_target]
        if canonical_url(target.get("url")) in required_urls
    ]


def cited_reference_action_ids(
    *,
    answer: str,
    evidence: Sequence[Mapping[str, Any]],
    tool_results: Sequence[Mapping[str, Any]],
) -> set[str]:
    """Return cited reference-only action ids without imposing a read queue."""
    cited_ids = _cited_evidence_ids(str(answer or ""), evidence)
    if not cited_ids:
        return set()
    records_by_action = {
        _text(record.get("action_id") or record.get("id"), 120): record
        for raw_record in tool_results
        if isinstance(raw_record, Mapping)
        for record in [raw_record]
        if _text(record.get("action_id") or record.get("id"), 120)
    }
    return {
        action_id
        for raw_evidence in evidence
        if isinstance(raw_evidence, Mapping)
        for evidence_id in [_text(raw_evidence.get("evidence_id") or raw_evidence.get("id"), 120)]
        for action_id in [_text(raw_evidence.get("action_id"), 120)]
        if evidence_id in cited_ids
        and action_id
        and action_id in records_by_action
        and _reference_read_required(records_by_action[action_id])
    }


def cited_reference_access_status(
    *,
    answer: str,
    evidence: Sequence[Mapping[str, Any]],
    tool_results: Sequence[Mapping[str, Any]],
    targets: Sequence[Mapping[str, Any]],
    selected_urls: Any = None,
    successful_urls: Any = None,
    cited_action_ids: Sequence[str] | set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return body-access status independently for each cited source action.

    The same URL may be present in more than one source action, so the status
    is calculated from the action associations on each target rather than from
    a run-wide successful-read count.  This is the shared contract used by
    the live gate and the persisted audit: one source's successful body read
    cannot satisfy a different cited source action.

    For a multi-link reference result, ``selection_required`` is true until
    the model selects at least one candidate.  Unselected candidates remain
    available as index metadata but do not become mandatory reads.
    """
    cited_actions = (
        cited_reference_action_ids(
            answer=answer,
            evidence=evidence,
            tool_results=tool_results,
        )
        if cited_action_ids is None
        else {
            _text(action_id, 120)
            for action_id in cited_action_ids
            if _text(action_id, 120)
        }
    )
    selected = (
        content_read_call_urls(tool_results)
        if selected_urls is None
        else selected_urls
    )
    successful = (
        successful_content_read_urls(tool_results)
        if successful_urls is None
        else successful_urls
    )
    return reference_access_status(
        cited_action_ids=cited_actions,
        targets=targets,
        selected_urls=selected,
        successful_urls=successful,
    )


def build_content_access_targets(
    *,
    tool_results: Sequence[Mapping[str, Any]],
    existing_targets: Sequence[Mapping[str, Any]] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep every candidate and return the model-selected reads still pending.

    A reference-only result creates candidates.  The final-answer gate adds
    the links belonging to each cited reference tool call to its own required
    queue; this helper's ``pending`` value remains the selected-read queue so
    independent tool calls are not merged into one global requirement.
    """
    targets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_target in existing_targets:
        target = dict(raw_target) if isinstance(raw_target, Mapping) else {}
        url = _text(target.get("url"))
        normalized = canonical_url(url)
        if not normalized or normalized in seen:
            continue
        target["url"] = url
        seen.add(normalized)
        targets.append(target)
    for candidate in reference_candidates(tool_results):
        normalized = canonical_url(candidate.get("url"))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        targets.append(candidate)

    target_urls = {canonical_url(target.get("url")) for target in targets}
    selected_urls = content_read_call_urls(tool_results)
    read_urls = successful_content_read_urls(tool_results)
    pending = [
        target
        for target in targets
        if canonical_url(target.get("url")) in (selected_urls & target_urls)
        and canonical_url(target.get("url")) not in read_urls
    ]
    return targets, pending


__all__ = [
    "CONTENT_READER_TOOLS",
    "DEFAULT_CONTENT_ACCESS_REPAIR_LIMIT",
    "REFERENCE_ONLY_TOOL_KINDS",
    "build_content_access_targets",
    "canonical_url",
    "cited_reference_action_ids",
    "cited_reference_access_status",
    "content_read_call_urls",
    "required_content_access_targets",
    "reference_candidates",
    "successful_content_read_urls",
]
