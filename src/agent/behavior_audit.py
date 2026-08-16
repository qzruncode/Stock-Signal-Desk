"""Deterministic audit of observable agent behaviour.

The audit deliberately evaluates the execution record rather than attempting to
reconstruct hidden model reasoning.  It answers questions such as:

* did a tool return a source link or did a reader actually fetch its content?
* did a successful tool return usable data, or only an empty/partial response?
* did the execution leave evidence and answer claims that can be traced back to
  a tool call?

The input is the compact ``quality_projection`` persisted for a run.  Keeping
this module independent from the storage and HTTP layers lets it audit both
new and historical runs without a migration.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit


CONTENT_READER_TOOLS = frozenset(
    {
        "read_web_source",
        "read_text_document",
        "read_financial_article",
        "read_rss_item",
        "read_registered_rss_item",
    }
)

REFERENCE_ONLY_TOOL_KINDS: dict[str, str] = {
    "read_company_research_reports_akshare": "document",
    "read_company_news_akshare": "article",
}

_CONTENT_KEYS = frozenset(
    {
        "content",
        "content_text",
        "content_html",
        "chunks",
        "content_chunks",
        "resources",
        "resource",
        "evidence_collection",
        "extraction_method",
        "content_length",
        "coverage_digest",
    }
)
_URL_KEYS = frozenset(
    {
        "url",
        "link",
        "source_url",
        "final_url",
        "reference_links",
        "referenceLinks",
    }
)
_MAX_LINKS = 20
_MAX_FINDING_LINKS = 12
_MAX_TOOL_CHAIN = 80
_EVIDENCE_REFERENCE = re.compile(r"\bev_[A-Za-z0-9_-]+\b")
_DATA_TIME_NOT_APPLICABLE_TOOLS = frozenset(
    {
        "search_stocks",
        "read_company_profile_cninfo",
    }
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _field(value: Any, *names: str) -> Any:
    item = _mapping(value)
    for name in names:
        if name in item:
            return item[name]
    return None


def _text(value: Any, limit: int = 500) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _truthy(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() in {"true", "1", "yes"})


def _data_time_applicable(item: Mapping[str, Any], *, tool_name: str = "") -> bool:
    """Return whether missing source time is meaningful for this operation."""
    value = _field(item, "data_time_applicable", "dataTimeApplicable")
    if value is None:
        value = _field(_mapping(item.get("result")), "data_time_applicable", "dataTimeApplicable")
    if value is None and _text(tool_name, 160) in _DATA_TIME_NOT_APPLICABLE_TOOLS:
        return False
    return value is not False


def _canonical_url(value: Any) -> str:
    text = _text(value, 2_000)
    if not text.lower().startswith(("http://", "https://")):
        return ""
    try:
        parts = urlsplit(text)
        if not parts.netloc:
            return text
        # Query strings and fragments are usually tracking parameters for the
        # source links emitted by the market-data providers.  Removing them
        # makes a redirected/read URL comparable to the original reference.
        return urlunsplit(
            (
                parts.scheme.lower(),
                parts.netloc.lower(),
                parts.path.rstrip("/") or "/",
                "",
                "",
            )
        )
    except ValueError:
        return text


def _append_url(urls: list[str], value: Any) -> None:
    url = _text(value, 2_000)
    if not url.lower().startswith(("http://", "https://")):
        return
    if url not in urls:
        urls.append(url)


def _collect_urls(value: Any, urls: list[str], *, depth: int = 0) -> None:
    if len(urls) >= 100 or depth > 4:
        return
    if isinstance(value, str):
        _append_url(urls, value)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key)
            if normalized in _URL_KEYS or normalized.lower() in {key.lower() for key in _URL_KEYS}:
                _collect_urls(item, urls, depth=depth + 1)
            elif isinstance(item, (Mapping, list, tuple)):
                _collect_urls(item, urls, depth=depth + 1)
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in list(value)[:100]:
            _collect_urls(item, urls, depth=depth + 1)


def _reference_urls(item: Mapping[str, Any]) -> list[str]:
    urls: list[str] = []
    for key in ("reference_links", "referenceLinks", "source_refs", "sourceRefs"):
        _collect_urls(item.get(key), urls)
    result_items = _field(item, "result_items", "resultItems")
    for result in _sequence(result_items):
        _collect_urls(result, urls)
    return urls[:_MAX_LINKS]


def _read_urls(item: Mapping[str, Any], step: Mapping[str, Any]) -> list[str]:
    urls: list[str] = []
    for candidate in (item.get("arguments"), item.get("request"), step.get("arguments"), step.get("result"), item.get("result")):
        _collect_urls(candidate, urls)
    return urls[:_MAX_LINKS]


def _content_signal(value: Any, *, depth: int = 0) -> bool:
    if depth > 4:
        return False
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key)
            if normalized in _CONTENT_KEYS or normalized.lower() in {key.lower() for key in _CONTENT_KEYS}:
                if normalized in {"content_length", "contentLength"}:
                    try:
                        if int(item or 0) > 0:
                            return True
                    except (TypeError, ValueError):
                        pass
                elif item not in (None, "", [], {}, ()):  # non-empty extraction marker
                    return True
            if isinstance(item, (Mapping, list, tuple)) and _content_signal(item, depth=depth + 1):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_content_signal(item, depth=depth + 1) for item in list(value)[:50])
    return False


def _step_for_tool(
    steps: Sequence[Any],
    item: Mapping[str, Any],
    action_id: str,
) -> Mapping[str, Any]:
    tool_call_id = _text(_field(item, "tool_call_id", "toolCallId"), 160)
    for raw in steps:
        step = _mapping(raw)
        ids = {
            _text(_field(step, "step_id", "stepId"), 160),
            _text(_field(step, "idempotency_key", "idempotencyKey"), 160),
        }
        if action_id in ids or (tool_call_id and tool_call_id in ids):
            return step
    tool_name = _text(_field(item, "tool_name", "toolName"), 160)
    same_tool = [
        _mapping(raw)
        for raw in steps
        if _text(_field(_mapping(raw), "tool_name", "toolName"), 160) == tool_name
    ]
    return same_tool[0] if len(same_tool) == 1 else {}


def _access_mode(tool_name: str, item: Mapping[str, Any], step: Mapping[str, Any]) -> str:
    explicit = _mapping(_field(item, "content_access", "contentAccess"))
    explicit_mode = _text(_field(explicit, "mode", "access_mode", "accessMode"), 80).lower()
    if explicit_mode in {"content", "content_read", "read", "extracted"}:
        return "content_read"
    if explicit_mode in {"reference_only", "reference", "metadata_only"}:
        return "reference_only"
    if tool_name in REFERENCE_ONLY_TOOL_KINDS:
        return "reference_only"
    if tool_name in CONTENT_READER_TOOLS:
        return "content_read"
    if _content_signal(item) or _content_signal(step):
        return "content_read"
    return "structured_data"


def _content_extracted(item: Mapping[str, Any], step: Mapping[str, Any], mode: str) -> bool:
    explicit = _mapping(_field(item, "content_access", "contentAccess"))
    if "content_extracted" in explicit or "contentExtracted" in explicit:
        return _truthy(_field(explicit, "content_extracted", "contentExtracted"))
    # A reader tool call proves that an access attempt happened, not that a
    # non-empty body was extracted.  Keep the fallback conservative for old
    # records that predate the explicit content_access contract.
    return mode == "content_read" and _field(item, "success") is True and (
        _content_signal(item) or _content_signal(step)
    )


def describe_tool_access(tool_name: str, observation: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a bounded access contract for a live tool observation.

    This projection is persisted with new runs so the UI does not need to infer
    content access from arbitrary provider response fields.  It intentionally
    excludes the content body itself.
    """
    item = dict(observation or {})
    raw_result = _mapping(item.get("result"))
    for key, value in raw_result.items():
        item.setdefault(str(key), value)
    mode = _access_mode(tool_name, item, {})
    references = _reference_urls(item)
    extracted = _content_extracted(item, {}, mode)
    result: dict[str, Any] = {
        "mode": mode,
        "content_read": mode == "content_read" and item.get("success") is True,
        "content_extracted": extracted,
        "reference_link_count": len(references),
    }
    for key in ("extraction_method", "content_length", "requested_url", "final_url"):
        value = _field(item, key, _camel_key(key))
        if value not in (None, ""):
            result[key] = value
    return result


def describe_tool_quality(tool_name: str, observation: Mapping[str, Any] | None) -> dict[str, Any]:
    """Run cheap, provider-independent checks on one tool response.

    These checks are deliberately conservative: a green result means that no
    structural anomaly was found, not that the external provider's facts are
    true.  Tool-specific validators can add stronger checks later without
    changing the run record contract.
    """
    item = dict(observation or {})
    payload = _mapping(item.get("result")) or item
    collections: list[Any] = []
    for key in ("items", "results", "rows", "records", "entries", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            collections = value
            break
    result_count = _field(payload, "result_count", "resultCount", "item_count", "itemCount", "count", "total")
    try:
        result_count_int = int(result_count) if result_count is not None else None
    except (TypeError, ValueError):
        result_count_int = None
    success = item.get("success") is True or payload.get("success") is True
    warnings = _field(payload, "warnings", "warning")
    errors = _field(payload, "errors", "error")
    warning_count = len(_sequence(warnings)) if isinstance(warnings, list) else int(bool(warnings))
    error_count = len(_sequence(errors)) if isinstance(errors, list) else int(bool(errors))
    duplicate_count = 0
    identities: set[str] = set()
    for raw in collections[:200]:
        record = _mapping(raw)
        identity = "|".join(
            _text(record.get(key), 160)
            for key in ("url", "link", "id", "code", "symbol", "title", "name", "date", "published")
            if _text(record.get(key), 160)
        )
        if identity and identity in identities:
            duplicate_count += 1
        elif identity:
            identities.add(identity)
    fallback_used = _truthy(_field(item, "fallback_used", "fallbackUsed") or _field(payload, "fallback_used", "fallbackUsed"))
    stale = _truthy(_field(item, "is_stale", "isStale") or _field(payload, "is_stale", "isStale"))
    partial = _truthy(_field(item, "partial") or _field(payload, "partial"))
    freshness_unknown = _truthy(_field(item, "freshness_unknown", "freshnessUnknown") or _field(payload, "freshness_unknown", "freshnessUnknown"))
    source_present = bool(
        _field(payload, "source", "sources", "provider", "data_source", "dataSource")
        or _field(item, "source_refs", "sourceRefs")
    )
    entity_uncertain = _field(payload, "unverified_entity_mention_count", "unverifiedEntityMentionCount")
    try:
        entity_uncertain_int = int(entity_uncertain or 0)
    except (TypeError, ValueError):
        entity_uncertain_int = 0
    checks: list[dict[str, Any]] = []
    if not success:
        checks.append({"code": "tool_failed", "status": "danger", "detail": "工具返回失败。"})
    if result_count_int == 0:
        checks.append({"code": "empty_result", "status": "warning", "detail": "成功调用没有返回可枚举的数据项。"})
    if result_count_int is not None and collections and result_count_int < len(collections):
        checks.append({"code": "count_inconsistent", "status": "warning", "detail": f"返回数量字段为 {result_count_int}，但响应中发现至少 {len(collections)} 项。"})
    if duplicate_count:
        checks.append({"code": "duplicate_items", "status": "warning", "detail": f"发现 {duplicate_count} 条可能重复的数据项。"})
    if warning_count:
        checks.append({"code": "provider_warning", "status": "warning", "detail": f"工具返回 {warning_count} 条警告。"})
    if error_count and success:
        checks.append({"code": "successful_response_with_errors", "status": "warning", "detail": f"工具成功返回但同时携带 {error_count} 条错误信息。"})
    if fallback_used:
        checks.append({"code": "fallback_used", "status": "warning", "detail": "结果使用了降级来源。"})
    if stale:
        checks.append({"code": "stale_data", "status": "warning", "detail": "结果被来源标记为过期。"})
    if partial:
        checks.append({"code": "partial_result", "status": "warning", "detail": "结果被标记为不完整。"})
    if success and (result_count_int or collections) and freshness_unknown and _data_time_applicable(payload, tool_name=tool_name):
        checks.append({"code": "unknown_data_time", "status": "warning", "detail": "结果没有可验证的数据时间。"})
    if (result_count_int or collections) and not source_present:
        checks.append({"code": "missing_source", "status": "warning", "detail": "结果没有明确来源标识。"})
    if entity_uncertain_int:
        checks.append({"code": "entity_match_uncertain", "status": "warning", "detail": f"有 {entity_uncertain_int} 条结果未完成主体逐字匹配。"})
    status = "danger" if any(check["status"] == "danger" for check in checks) else "warning" if checks else "clear"
    return {
        "status": status,
        "issue_count": len(checks),
        "duplicate_count": duplicate_count,
        "warning_count": warning_count,
        "error_count": error_count,
        "checks": checks,
        "source_present": source_present,
        "data_time_present": not freshness_unknown,
        "entity_match_uncertain_count": entity_uncertain_int,
    }


def _camel_key(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _status_for_check(findings: Sequence[Mapping[str, Any]]) -> str:
    if any(_text(item.get("severity"), 20) == "danger" for item in findings):
        return "danger"
    if any(_text(item.get("severity"), 20) == "warning" for item in findings):
        return "warning"
    if findings:
        return "info"
    return "clear"


def _check(code: str, label: str, status: str, detail: str) -> dict[str, str]:
    return {"code": code, "label": label, "status": status, "detail": detail}


def _finding(
    *,
    code: str,
    severity: str,
    category: str,
    title: str,
    detail: str,
    remediation: str,
    tool_names: Sequence[str] = (),
    action_ids: Sequence[str] = (),
    links: Sequence[str] = (),
    confidence: float = 1.0,
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "category": category,
        "title": title,
        "detail": detail,
        "remediation": remediation,
        "confidence": round(max(0.0, min(float(confidence), 1.0)), 2),
        "tool_names": list(dict.fromkeys(str(item) for item in tool_names if str(item))),
        "action_ids": list(dict.fromkeys(str(item) for item in action_ids if str(item)))[:20],
        "links": list(dict.fromkeys(str(item) for item in links if str(item)))[:_MAX_FINDING_LINKS],
    }


def _arguments_fingerprint(item: Mapping[str, Any]) -> str:
    arguments = _field(item, "arguments", "request", "input")
    try:
        return json.dumps(arguments or {}, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return _text(arguments, 1_000)


def build_behavior_audit(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    """Build a compact, actionable audit for a persisted run snapshot."""

    root = _mapping(snapshot)
    projection = _mapping(root.get("quality_projection"))
    raw_results = [item for item in _sequence(projection.get("tool_results")) if isinstance(item, Mapping)]
    steps = _sequence(root.get("steps"))
    evidence = [item for item in _sequence(projection.get("evidence")) if isinstance(item, Mapping)]
    claims = [item for item in _sequence(projection.get("claim_evidence")) if isinstance(item, Mapping)]
    run_snapshot = _mapping(root.get("run"))
    final_text = _text(
        _field(run_snapshot, "final_text", "finalText"),
        200_000,
    )
    evidence_by_action: defaultdict[str, int] = defaultdict(int)
    evidence_by_id: dict[str, Mapping[str, Any]] = {}
    for item in evidence:
        action_id = _text(_field(item, "action_id", "actionId"), 160)
        if action_id:
            evidence_by_action[action_id] += 1
        evidence_id = _text(_field(item, "evidence_id", "evidenceId", "id"), 160)
        if evidence_id:
            evidence_by_id[evidence_id] = item

    findings: list[dict[str, Any]] = []
    tool_chain: list[dict[str, Any]] = []
    candidate_links: dict[str, dict[str, Any]] = {}
    failed_tools: list[tuple[str, str, str]] = []
    empty_tools: list[tuple[str, str]] = []
    fallback_tools: list[str] = []
    stale_tools: list[str] = []
    unknown_time_tools: list[str] = []
    partial_tools: list[str] = []
    missing_evidence_tools: list[tuple[str, str]] = []
    content_read_calls = 0
    content_extracted_calls = 0
    reference_only_tool_count = 0
    read_urls: set[str] = set()
    read_attempt_urls: set[str] = set()
    unmatched_content_reads: list[str] = []
    repeated_calls: list[str] = []
    quality_issue_groups: defaultdict[str, list[tuple[str, str, str]]] = defaultdict(list)
    previous_call: tuple[str, str] | None = None

    for raw in raw_results[:_MAX_TOOL_CHAIN]:
        item = dict(raw)
        tool_name = _text(_field(item, "tool_name", "toolName"), 160) or "未指定工具"
        action_id = _text(_field(item, "action_id", "actionId", "tool_call_id", "toolCallId"), 160)
        success = _field(item, "success") is True
        step = _step_for_tool(steps, item, action_id)
        mode = _access_mode(tool_name, item, step)
        extracted = _content_extracted(item, step, mode)
        refs = _reference_urls(item)
        result_items = _sequence(_field(item, "result_items", "resultItems"))
        reference_kind = REFERENCE_ONLY_TOOL_KINDS.get(tool_name)
        if mode == "reference_only":
            reference_only_tool_count += 1
            for link in refs:
                kind = reference_kind or ("document" if link.lower().split("?", 1)[0].endswith(".pdf") else "article")
                key = _canonical_url(link)
                if not key:
                    continue
                entry = candidate_links.setdefault(key, {"url": link, "kind": kind, "tools": [], "actions": []})
                if tool_name not in entry["tools"]:
                    entry["tools"].append(tool_name)
                if action_id and action_id not in entry["actions"]:
                    entry["actions"].append(action_id)

        if mode == "content_read":
            content_read_calls += 1
            if extracted:
                content_extracted_calls += 1
            urls_for_read = _read_urls(item, step)
            matched = False
            for link in urls_for_read:
                canonical = _canonical_url(link)
                if canonical:
                    read_attempt_urls.add(canonical)
                    if success and extracted:
                        read_urls.add(canonical)
                    if canonical in candidate_links:
                        matched = True
            if candidate_links and not matched:
                unmatched_content_reads.append(tool_name)

        result_count = _field(item, "result_count", "resultCount", "count", "item_count", "itemCount")
        try:
            result_count_int = int(result_count) if result_count is not None else None
        except (TypeError, ValueError):
            result_count_int = None
        if success and result_count_int == 0:
            empty_tools.append((tool_name, action_id))
        if _truthy(_field(item, "partial")):
            partial_tools.append(tool_name)
        if _truthy(_field(item, "fallback_used", "fallbackUsed")):
            fallback_tools.append(tool_name)
        if _truthy(_field(item, "is_stale", "isStale")):
            stale_tools.append(tool_name)
        if success and _truthy(_field(item, "freshness_unknown", "freshnessUnknown")) and _data_time_applicable(item, tool_name=tool_name):
            unknown_time_tools.append(tool_name)
        if success and result_count_int is not None and result_count_int > 0 and not evidence_by_action.get(action_id):
            missing_evidence_tools.append((tool_name, action_id))
        if not success:
            errors = _field(item, "errors", "error")
            error_text = _text(errors, 500) or _text(_field(item, "error_code", "errorCode"), 160) or "未提供具体错误"
            failed_tools.append((tool_name, action_id, error_text))

        quality = _mapping(_field(item, "data_quality", "dataQuality"))
        for quality_check in _sequence(quality.get("checks")):
            check = _mapping(quality_check)
            code = _text(check.get("code"), 80)
            if code and _text(check.get("status"), 20) != "clear":
                quality_issue_groups[code].append((tool_name, action_id, _text(check.get("detail"), 240)))

        fingerprint = (tool_name, _arguments_fingerprint(item))
        if previous_call == fingerprint:
            repeated_calls.append(tool_name)
        previous_call = fingerprint
        evidence_count = evidence_by_action.get(action_id, 0)
        tool_chain.append(
            {
                "action_id": action_id,
                "tool_name": tool_name,
                "success": success,
                "behavior": mode,
                "access_status": (
                    "content_extracted" if extracted else "content_read"
                    if mode == "content_read" and success
                    else "reference_only" if mode == "reference_only"
                    else "failed" if not success
                    else "structured_data"
                ),
                "reference_link_count": len(refs),
                "content_extracted": extracted,
                "evidence_count": evidence_count,
                "result_count": result_count_int,
            }
        )

    unread = [entry for key, entry in candidate_links.items() if key not in read_urls]
    unread_documents = [entry for entry in unread if entry.get("kind") == "document"]
    unread_articles = [entry for entry in unread if entry.get("kind") == "article"]
    pending_documents = [
        entry
        for entry in unread_documents
        if _canonical_url(entry.get("url")) in read_attempt_urls
    ]
    pending_articles = [
        entry
        for entry in unread_articles
        if _canonical_url(entry.get("url")) in read_attempt_urls
    ]
    unselected_documents = [
        entry
        for entry in unread_documents
        if _canonical_url(entry.get("url")) not in read_attempt_urls
    ]
    unselected_articles = [
        entry
        for entry in unread_articles
        if _canonical_url(entry.get("url")) not in read_attempt_urls
    ]
    has_document_read = any(
        entry.get("kind") == "document" and _canonical_url(entry.get("url")) in read_urls
        for entry in candidate_links.values()
    )
    has_article_read = any(
        entry.get("kind") == "article" and _canonical_url(entry.get("url")) in read_urls
        for entry in candidate_links.values()
    )
    raw_results_by_action = {
        _text(_field(item, "action_id", "actionId", "tool_call_id", "toolCallId"), 160): item
        for item in raw_results
        if _text(_field(item, "action_id", "actionId", "tool_call_id", "toolCallId"), 160)
    }
    cited_reference_actions: dict[str, Mapping[str, Any]] = {}
    cited_evidence_ids = set(_EVIDENCE_REFERENCE.findall(final_text))
    for claim in claims:
        cited_evidence_ids.update(
            _text(raw_evidence_id, 160)
            for raw_evidence_id in _sequence(_field(claim, "evidence_ids", "evidenceIds"))
            if _text(raw_evidence_id, 160)
        )
    for evidence_id in cited_evidence_ids:
        evidence_item = evidence_by_id.get(evidence_id)
        if evidence_item is None:
            continue
        action_id = _text(_field(evidence_item, "action_id", "actionId"), 160)
        raw_result = raw_results_by_action.get(action_id)
        if not action_id or raw_result is None:
            continue
        tool_name = _text(_field(raw_result, "tool_name", "toolName"), 160)
        step = _step_for_tool(steps, raw_result, action_id)
        if _access_mode(tool_name, raw_result, step) == "reference_only":
            cited_reference_actions[action_id] = raw_result
    cited_unread_links_by_action: dict[str, list[str]] = defaultdict(list)
    for entry in candidate_links.values():
        canonical = _canonical_url(entry.get("url"))
        if not canonical or canonical in read_urls:
            continue
        for action_id in entry.get("actions") or []:
            action = _text(action_id, 160)
            if action in cited_reference_actions and entry.get("url") not in cited_unread_links_by_action[action]:
                cited_unread_links_by_action[action].append(str(entry.get("url") or ""))
    cited_action_ids = set(cited_reference_actions)
    unselected_documents = [
        entry
        for entry in unselected_documents
        if not cited_action_ids.intersection(
            _text(action_id, 160) for action_id in entry.get("actions") or []
        )
    ]
    unselected_articles = [
        entry
        for entry in unselected_articles
        if not cited_action_ids.intersection(
            _text(action_id, 160) for action_id in entry.get("actions") or []
        )
    ]

    if failed_tools:
        grouped: defaultdict[str, list[tuple[str, str]]] = defaultdict(list)
        for tool_name, action_id, error in failed_tools:
            grouped[tool_name].append((action_id, error))
        for tool_name, failures in grouped.items():
            detail = failures[0][1]
            suffix = f"；共 {len(failures)} 次失败" if len(failures) > 1 else ""
            findings.append(
                _finding(
                    code="tool_execution_failed",
                    severity="danger",
                    category="execution",
                    title=f"工具 {tool_name} 执行失败",
                    detail=f"运行记录标记为失败：{detail}{suffix}。",
                    remediation="查看请求参数、来源尝试链路和服务端错误；必要时更换来源或修复工具。",
                    tool_names=[tool_name],
                    action_ids=[item[0] for item in failures],
                )
            )

    if pending_documents:
        findings.append(
            _finding(
                code="content_read_incomplete_document",
                severity="warning",
                category="content_access",
                title="已选择文档，但正文读取未成功",
                detail=(
                    f"有 {len(pending_documents)} 个已选择的 PDF/文档链接没有成功提取正文；"
                    "这些链接只能证明存在来源引用，不能证明模型已经阅读过文档。"
                ),
                remediation="对已选择的文档重试网页/PDF读取工具；若仍失败，回答中明确标注正文未核验。",
                tool_names=sorted({tool for entry in pending_documents for tool in entry["tools"]}),
                action_ids=[action for entry in pending_documents for action in entry["actions"]],
                links=[entry["url"] for entry in pending_documents],
            )
        )
    elif unselected_documents and has_document_read:
        findings.append(
            _finding(
                code="reference_not_selected_document",
                severity="info",
                category="content_access",
                title="仍有文档候选未选择读取",
                detail=(
                    f"工具返回了 {len(unselected_documents)} 个未选择的 PDF/文档链接；"
                    "本次已有其他文档正文成功读取，未选择的候选不代表执行失败。"
                ),
                remediation="仅在结论需要这些候选时再读取；未读取来源只能作为链接/索引展示。",
                tool_names=sorted({tool for entry in unselected_documents for tool in entry["tools"]}),
                action_ids=[action for entry in unselected_documents for action in entry["actions"]],
                links=[entry["url"] for entry in unselected_documents],
                confidence=0.98,
            )
        )
    elif unselected_documents:
        findings.append(
            _finding(
                code="reference_only_document",
                severity="warning",
                category="content_access",
                title="发现文档来源，但没有正文读取",
                detail=(
                    f"工具返回了 {len(unselected_documents)} 个 PDF/文档链接，但本次没有选择任何文档正文读取。"
                    "这些链接只能证明存在来源引用，不能证明模型阅读过文档。"
                ),
                remediation="从候选中选择需要支撑结论的文档调用网页/PDF读取工具；若无需正文，应明确限定结论范围。",
                tool_names=sorted({tool for entry in unselected_documents for tool in entry["tools"]}),
                action_ids=[action for entry in unselected_documents for action in entry["actions"]],
                links=[entry["url"] for entry in unselected_documents],
            )
        )
    if pending_articles:
        findings.append(
            _finding(
                code="content_read_incomplete_article",
                severity="warning",
                category="content_access",
                title="已选择文章，但正文读取未成功",
                detail=(
                    f"有 {len(pending_articles)} 个已选择的新闻/文章链接没有成功提取正文；"
                    "文章标题或摘要不等于已经核对过正文。"
                ),
                remediation="对已选择的文章重试网页读取工具；若仍失败，回答中明确标注正文未核验。",
                tool_names=sorted({tool for entry in pending_articles for tool in entry["tools"]}),
                action_ids=[action for entry in pending_articles for action in entry["actions"]],
                links=[entry["url"] for entry in pending_articles],
            )
        )
    elif unselected_articles and has_article_read:
        findings.append(
            _finding(
                code="reference_not_selected_article",
                severity="info",
                category="content_access",
                title="仍有文章候选未选择读取",
                detail=(
                    f"工具返回了 {len(unselected_articles)} 个未选择的新闻/文章链接；"
                    "本次已有其他来源正文成功读取，未选择的候选不代表执行失败。"
                ),
                remediation="仅在结论需要这些候选时再读取；未读取来源只能作为链接/索引展示。",
                tool_names=sorted({tool for entry in unselected_articles for tool in entry["tools"]}),
                action_ids=[action for entry in unselected_articles for action in entry["actions"]],
                links=[entry["url"] for entry in unselected_articles],
                confidence=0.98,
            )
        )
    elif unselected_articles:
        findings.append(
            _finding(
                code="reference_only_article",
                severity="warning",
                category="content_access",
                title="发现文章来源，但没有正文读取",
                detail=(
                    f"工具返回了 {len(unselected_articles)} 个新闻/文章链接，但本次没有选择任何文章正文读取。"
                    "文章标题或摘要不等于已经核对过正文。"
                ),
                remediation="从候选中选择需要支撑结论的文章调用网页读取工具；若无需正文，应明确限定结论范围。",
                tool_names=sorted({tool for entry in unselected_articles for tool in entry["tools"]}),
                action_ids=[action for entry in unselected_articles for action in entry["actions"]],
                links=[entry["url"] for entry in unselected_articles],
            )
        )
    if unmatched_content_reads:
        findings.append(
            _finding(
                code="content_read_unmapped",
                severity="warning",
                category="content_access",
                title="正文读取调用无法关联到来源链接",
                detail=(
                    f"发现 {len(unmatched_content_reads)} 次正文读取调用，但无法把它们和前置来源链接一一对应。"
                    "这可能是资源 ID、重定向 URL 或来源记录缺少链路标识。"
                ),
                remediation="让读取工具返回原始 URL、最终 URL 或 resource_id 与来源的关联信息。",
                tool_names=sorted(set(unmatched_content_reads)),
                confidence=0.85,
            )
        )

    if cited_unread_links_by_action:
        cited_records = [cited_reference_actions[action] for action in cited_unread_links_by_action]
        cited_tool_names = [_text(_field(item, "tool_name", "toolName"), 160) for item in cited_records]
        cited_links = [
            link
            for links in cited_unread_links_by_action.values()
            for link in links
        ]
        findings.append(
            _finding(
                code="cited_reference_without_body",
                severity="warning",
                category="content_access",
                title="回答引用了未读取正文的来源索引",
                detail=(
                    f"最终结论引用了 {len(cited_unread_links_by_action)} 次 reference-only 工具调用，"
                    f"其中 {len(cited_links)} 个对应链接尚未成功提取正文；引用该次调用不能替代正文核验。"
                ),
                remediation="按工具调用逐一读取其返回链接的正文，或删除该次索引证据并降低结论确定性。",
                tool_names=cited_tool_names,
                action_ids=list(cited_unread_links_by_action),
                links=cited_links,
            )
        )

    if empty_tools:
        findings.append(
            _finding(
                code="successful_empty_result",
                severity="warning",
                category="data_quality",
                title="部分工具成功返回但没有数据",
                detail=f"{len(empty_tools)} 个成功调用返回 0 条结果：{', '.join(sorted({item[0] for item in empty_tools})[:5])}。",
                remediation="确认查询范围、数据源状态和空结果是否符合业务预期。",
                tool_names=sorted({item[0] for item in empty_tools}),
                action_ids=[item[1] for item in empty_tools],
            )
        )
    if fallback_tools:
        findings.append(
            _finding(
                code="fallback_used",
                severity="warning",
                category="data_quality",
                title="部分资料使用了降级来源",
                detail=f"{len(set(fallback_tools))} 个工具发生来源降级：{', '.join(sorted(set(fallback_tools))[:6])}。",
                remediation="核对降级来源是否仍满足主体、时间和字段要求，避免把降级结果当成首选来源。",
                tool_names=sorted(set(fallback_tools)),
            )
        )
    if stale_tools:
        findings.append(
            _finding(
                code="stale_data",
                severity="warning",
                category="data_quality",
                title="发现过期资料",
                detail=f"{len(set(stale_tools))} 个工具返回的数据被标记为过期。",
                remediation="明确回答中的数据截止时间，必要时重新获取。",
                tool_names=sorted(set(stale_tools)),
            )
        )
    if unknown_time_tools:
        findings.append(
            _finding(
                code="unknown_data_time",
                severity="info",
                category="data_quality",
                title="部分资料没有提供可验证的数据时间",
                detail=f"{len(unknown_time_tools)} 个成功工具调用没有可验证的数据时间；这不是工具失败，但不能据此表述为最新或当前。",
                remediation="不要把这些结果表述为最新或当前数据，必要时补充带时间字段的来源。",
                tool_names=sorted(set(unknown_time_tools)),
                confidence=0.9,
            )
        )
    if partial_tools:
        findings.append(
            _finding(
                code="partial_result",
                severity="warning",
                category="data_quality",
                title="部分资料返回不完整",
                detail=f"{len(set(partial_tools))} 个工具返回了 partial 结果。",
                remediation="检查是否需要继续分页、读取下一段或补充来源。",
                tool_names=sorted(set(partial_tools)),
            )
        )
    quality_titles = {
        "duplicate_items": ("发现可能重复的数据项", "合并或去重后再把结果用于结论。"),
        "count_inconsistent": ("返回数量与实际数据项不一致", "检查工具的数量字段和分页/截断逻辑。"),
        "missing_source": ("部分数据没有明确来源标识", "补充 source/source_refs，并确认该数据能被追溯。"),
        "entity_match_uncertain": ("部分结果的主体匹配需要复核", "核对股票代码、公司简称和返回内容是否属于同一主体。"),
        "provider_warning": ("来源返回了数据质量警告", "展开工具返回内容，确认警告是否影响结论。"),
        "successful_response_with_errors": ("成功返回中同时包含错误信息", "确认返回是否只是部分成功，避免忽略错误字段。"),
    }
    for code, records in quality_issue_groups.items():
        if code in {"tool_failed", "empty_result", "fallback_used", "stale_data", "partial_result", "unknown_data_time"}:
            continue
        title, remediation = quality_titles.get(code, ("发现工具返回质量风险", "检查工具返回和来源质量。"))
        findings.append(
            _finding(
                code=f"data_quality_{code}",
                severity="warning",
                category="data_quality",
                title=title,
                detail=f"{len(records)} 个工具调用触发了该检查；示例：{records[0][2] or '未提供详细说明'}。",
                remediation=remediation,
                tool_names=sorted({record[0] for record in records}),
                action_ids=[record[1] for record in records],
                confidence=0.9,
            )
        )
    if missing_evidence_tools:
        findings.append(
            _finding(
                code="successful_result_without_evidence",
                severity="warning",
                category="evidence",
                title="成功返回的数据没有进入证据台账",
                detail=f"{len(missing_evidence_tools)} 个返回数据的工具调用没有关联证据记录。",
                remediation="确认这些数据是否真的被模型使用；如果支撑回答，应生成并关联 evidence_id。",
                tool_names=sorted({item[0] for item in missing_evidence_tools}),
                action_ids=[item[1] for item in missing_evidence_tools],
                confidence=0.88,
            )
        )
    if repeated_calls:
        findings.append(
            _finding(
                code="repeated_identical_tool_call",
                severity="warning",
                category="model_behavior",
                title="发现连续重复的工具调用",
                detail=f"至少有一次工具在相同参数下被连续调用：{', '.join(sorted(set(repeated_calls)))}。",
                remediation="检查是否因为模型没有理解返回结果、工具结果未进入上下文或重试去重失效。",
                tool_names=sorted(set(repeated_calls)),
                confidence=0.8,
            )
        )

    failed_claims = []
    unsupported_claims = []
    for claim in claims:
        checks = _mapping(_field(claim, "checks"))
        evidence_ids = _sequence(_field(claim, "evidence_ids", "evidenceIds"))
        if not evidence_ids:
            unsupported_claims.append(claim)
        if checks and any(value is False for value in checks.values()):
            failed_claims.append(claim)
    if unsupported_claims or failed_claims:
        findings.append(
            _finding(
                code="claim_evidence_check_failed",
                severity="warning",
                category="evidence",
                title="部分回答结论没有通过证据检查",
                detail=(
                    f"{len(unsupported_claims)} 个结论没有关联证据，"
                    f"{len(failed_claims)} 个结论的主体、来源或时间检查未通过。"
                ),
                remediation="打开结论与证据映射，补充取证或降低回答中的确定性表述。",
                confidence=0.95,
            )
        )

    run = _mapping(root.get("run"))
    trace = _mapping(root.get("trace"))
    loop = _mapping(projection.get("budgets")) or _mapping(_field(projection.get("execution_trace"), "loop"))
    if not loop:
        loop = _mapping(_field(trace, "loop"))
    work_budget_exhausted = _truthy(_field(loop, "work_budget_exhausted", "workBudgetExhausted"))
    if work_budget_exhausted:
        findings.append(
            _finding(
                code="work_budget_exhausted",
                severity="danger",
                category="system",
                title="运行触及工作预算上限",
                detail=_text(_field(loop, "work_budget_detail", "workBudgetDetail"), 500) or "模型或工具工作预算已耗尽，结果可能不完整。",
                remediation="减少单次任务范围、提高预算或拆分分析任务。",
            )
        )

    status = _text(_field(run, "status"), 40).lower()
    if status in {"failed", "blocked", "partial", "cancelled"} and not failed_tools:
        findings.append(
            _finding(
                code="run_not_completed",
                severity="danger" if status in {"failed", "blocked"} else "warning",
                category="execution",
                title="运行状态不是完整完成",
                detail=f"运行状态为 {status or '未知'}，但工具账本中没有足够的具体失败信息。",
                remediation="检查运行事件、模型调用和终止阶段的记录是否完整。",
            )
        )

    execution_findings = [item for item in findings if item["category"] == "execution"]
    data_findings = [item for item in findings if item["category"] == "data_quality"]
    access_findings = [item for item in findings if item["category"] == "content_access"]
    evidence_findings = [item for item in findings if item["category"] == "evidence"]
    behavior_findings = [item for item in findings if item["category"] == "model_behavior"]
    system_findings = [item for item in findings if item["category"] == "system"]

    def check_for(category_findings: Sequence[Mapping[str, Any]], label: str, code: str, default: str) -> dict[str, str]:
        status_value = _status_for_check(category_findings)
        return _check(code, label, status_value, default if not category_findings else str(category_findings[0].get("detail") or default))

    checks = [
        check_for(execution_findings, "执行完整性", "execution", "所有工具调用都完成了可观察记录。"),
        check_for(data_findings, "数据质量", "data_quality", "没有发现明显的空结果、降级、过期或不完整标记。"),
        check_for(access_findings, "来源读取", "content_access", "来源引用与正文读取链路没有发现缺口。"),
        check_for(evidence_findings, "证据关联", "evidence", "成功返回的数据和回答结论都有可核对的证据关联。"),
        check_for(behavior_findings, "模型行为", "model_behavior", "没有发现连续重复工具调用。"),
        check_for(system_findings, "系统资源", "system", "没有发现运行预算或系统终止异常。"),
    ]
    danger_count = sum(1 for item in findings if item["severity"] == "danger")
    warning_count = sum(1 for item in findings if item["severity"] == "warning")
    info_count = sum(1 for item in findings if item["severity"] == "info")
    risk_score = min(100, danger_count * 35 + warning_count * 12)
    audit_status = _status_for_check(findings)
    model_turn_count = _field(loop, "model_turn_count", "modelTurnCount")
    tool_call_count = _field(loop, "tool_call_count", "toolCallCount")
    try:
        model_turn_count = int(model_turn_count or 0)
    except (TypeError, ValueError):
        model_turn_count = 0
    try:
        tool_call_count = int(tool_call_count or len(raw_results))
    except (TypeError, ValueError):
        tool_call_count = len(raw_results)
    sampling_targets: list[dict[str, Any]] = []
    sampled_keys: set[str] = set()
    # Prefer a mixed sample when a run contains both PDF/document and article
    # references; otherwise the first provider's result could hide an entire
    # class of unread sources from the developer's quick check.
    for preferred_kind in ("document", "article"):
        for entry in unread:
            if entry.get("kind") != preferred_kind:
                continue
            key = _canonical_url(entry.get("url"))
            if key and key not in sampled_keys:
                sampling_targets.append({"url": entry["url"], "kind": entry["kind"]})
                sampled_keys.add(key)
            break
    for entry in unread:
        if len(sampling_targets) >= 3:
            break
        key = _canonical_url(entry.get("url"))
        if key and key not in sampled_keys:
            sampling_targets.append({"url": entry["url"], "kind": entry["kind"]})
            sampled_keys.add(key)

    return {
        "status": audit_status,
        "attention_level": "urgent" if danger_count else "review" if warning_count else "none",
        "risk_score": risk_score,
        "issue_count": len(findings),
        "danger_count": danger_count,
        "warning_count": warning_count,
        "info_count": info_count,
        "model_turn_count": model_turn_count,
        "tool_call_count": tool_call_count,
        "tool_observation_count": len(raw_results),
        "content_read_call_count": content_read_calls,
        "content_extracted_call_count": content_extracted_calls,
        "reference_only_tool_count": reference_only_tool_count,
        "reference_link_count": len(candidate_links),
        "unread_reference_count": len(unread),
        "unread_document_count": len(unread_documents),
        "unread_article_count": len(unread_articles),
        "cited_reference_tool_count": len(cited_reference_actions),
        "cited_unread_reference_count": sum(len(links) for links in cited_unread_links_by_action.values()),
        "failed_tool_count": len(failed_tools),
        "evidence_count": len(evidence),
        "claim_count": len(claims),
        "checks": checks,
        "findings": findings,
        "tool_chain": tool_chain,
        "sampling": {
            "mode": "on_demand",
            "available": bool(unread),
            "sample_limit": min(3, len(sampling_targets)),
            "targets": sampling_targets,
            "note": "系统抽检只验证来源是否可访问和能否提取正文，不代表模型在原分析中阅读过。",
        },
    }


__all__ = [
    "CONTENT_READER_TOOLS",
    "REFERENCE_ONLY_TOOL_KINDS",
    "build_behavior_audit",
    "describe_tool_access",
    "describe_tool_quality",
]
