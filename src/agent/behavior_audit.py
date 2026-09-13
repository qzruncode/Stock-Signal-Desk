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

from src.tools.base import classify_result_semantics, evidence_record_is_eligible

from .claim_validation import claim_checks_pass
from .reference_access import reference_access_status


CONTENT_READER_TOOLS = frozenset(
    {
        "read_web_source",
        "read_text_document",
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
        "content_length",
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
_RESULT_COLLECTION_KEYS = (
    "items",
    "results",
    "rows",
    "records",
    "entries",
    "data",
    "documents",
    "articles",
    "result_items",
    "resultItems",
)
_RECORD_IDENTITY_KEYS = (
    "url",
    "link",
    "id",
    "code",
    "symbol",
    "title",
    "name",
    "date",
    "published",
    "published_at",
    "publishedAt",
    "report_date",
    "reportDate",
    "trade_date",
    "tradeDate",
    "period",
)
_MAX_LINKS = 20
_MAX_FINDING_LINKS = 12
_MAX_TOOL_CHAIN = 80
_EVIDENCE_REFERENCE = re.compile(r"\bev_[A-Za-z0-9_-]+\b")
INSPECTION_SCHEMA_VERSION = "agent-run-inspection-v1"
ACTION_REQUIRED = "action_required"
ADVISORY = "advisory"
_DATA_TIME_NOT_APPLICABLE_TOOLS = frozenset(
    {
        "search_stocks",
        "read_company_profile_cninfo",
        "read_consensus_metric_ths",
        "read_consensus_financial_estimates_ths",
    }
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _observation_with_result_fields(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Expose canonical result fields while preserving outer execution facts."""
    item = dict(value or {})
    display_result = _mapping(item.get("display_result"))
    raw_result = _mapping(item.get("result"))
    # ``display_result`` is the existing bounded projection used by the
    # inspection UI.  Quality checks must inspect the same semantic rows as
    # that projection; otherwise a provider's raw rows can collapse distinct
    # segments/periods into false duplicate warnings.  Raw fields remain a
    # fallback for legacy records and execution metadata not present in the
    # projection.
    for key, nested_value in display_result.items():
        item.setdefault(str(key), nested_value)
    for key, nested_value in raw_result.items():
        item.setdefault(str(key), nested_value)
    return item


def _semantic_result_payload(item: Mapping[str, Any]) -> Mapping[str, Any]:
    """Expose a legacy observation payload through the common result contract."""
    raw_result = _mapping(item.get("result"))
    if not raw_result:
        return item
    payload = dict(raw_result)
    if "success" not in payload and item.get("success") is True:
        payload["success"] = True
    for key in (
        "partial",
        "data_time",
        "data_time_provenance",
        "data_time_note",
        "data_time_applicable",
        "is_stale",
        "freshness_unknown",
        "fallback_used",
        "fallback_provider",
        "evidence_eligible",
    ):
        if key not in payload and key in item:
            payload[key] = item[key]
    return payload


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
                # Keep HTTP and HTTPS as aliases for the same public page. The
                # persisted audit still retains the original URLs elsewhere.
                "https",
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


def _result_collection_state(payload: Mapping[str, Any]) -> tuple[list[Any], bool]:
    """Return one useful result collection and whether an empty one exists."""
    first_collection: list[Any] = []
    has_empty_collection = False
    for key in _RESULT_COLLECTION_KEYS:
        value = payload.get(key)
        if not isinstance(value, list):
            continue
        if value:
            if not first_collection:
                first_collection = value
        else:
            has_empty_collection = True
    if not first_collection:
        for key in ("item", "calculation"):
            value = payload.get(key)
            if isinstance(value, Mapping) and value:
                first_collection = [value]
                has_empty_collection = False
                break
            if value not in (None, "", [], {}):
                first_collection = [value]
                has_empty_collection = False
                break
    return first_collection, has_empty_collection


def _record_identity(record: Mapping[str, Any]) -> str:
    """Build a conservative identity for one projected result item.

    A number of providers put the actual row discriminator in a generic
    ``attributes`` list.  Looking only at a title/code therefore turns a
    valid multi-period or multi-segment response into a duplicate warning.
    Include those named attributes in the identity while keeping the logic
    provider-independent.
    """
    parts = [
        f"{key}={value}"
        for key in _RECORD_IDENTITY_KEYS
        if (value := _text(record.get(key), 160))
    ]
    attributes = record.get("attributes")
    attribute_parts: list[str] = []
    if isinstance(attributes, Mapping):
        attribute_parts.extend(
            f"{key}={_text(value, 500)}"
            for key, value in sorted(attributes.items(), key=lambda item: str(item[0]))
            if _text(key, 160) and _text(value, 500)
        )
    elif isinstance(attributes, Sequence) and not isinstance(attributes, (str, bytes, bytearray)):
        for attribute in attributes:
            item = _mapping(attribute)
            if not item:
                continue
            name = _text(_field(item, "name", "key", "label", "field", "attribute"), 160)
            value = _text(_field(item, "value", "text", "content"), 500)
            if name and value:
                attribute_parts.append(f"{name}={value}")
    if attribute_parts:
        parts.append("attributes=" + ";".join(sorted(attribute_parts)))
    return "|".join(parts)


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
    item = _observation_with_result_fields(observation)
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
    item = _observation_with_result_fields(observation)
    payload = _mapping(item.get("display_result")) or _mapping(item.get("result")) or item
    semantic_payload = _semantic_result_payload(item)
    semantics = classify_result_semantics(semantic_payload)
    collections, has_empty_collection = _result_collection_state(payload)
    result_count = _field(item, "result_count", "resultCount", "item_count", "itemCount", "count", "total")
    try:
        result_count_int = int(result_count) if result_count is not None else None
    except (TypeError, ValueError):
        result_count_int = None
    success_value = item["success"] if "success" in item else payload.get("success")
    success = success_value is True
    warnings = _field(item, "warnings", "warning")
    errors = _field(item, "errors", "error")
    warning_count = len(_sequence(warnings)) if isinstance(warnings, list) else int(bool(warnings))
    error_count = len(_sequence(errors)) if isinstance(errors, list) else int(bool(errors))
    duplicate_count = 0
    identities: set[str] = set()
    for raw in collections[:200]:
        record = _mapping(raw)
        identity = _record_identity(record)
        if identity and identity in identities:
            duplicate_count += 1
        elif identity:
            identities.add(identity)
    fallback_used = _truthy(_field(item, "fallback_used", "fallbackUsed"))
    stale = _truthy(_field(item, "is_stale", "isStale"))
    partial = _truthy(_field(item, "partial", "partial_result", "partialResult"))
    freshness_unknown = _truthy(_field(item, "freshness_unknown", "freshnessUnknown"))
    source_refs = _sequence(_field(item, "source_refs", "sourceRefs"))
    if not source_refs:
        source_refs = _sequence(_field(payload, "source_refs", "sourceRefs"))
    source_present = bool(
        _field(item, "source", "sources", "provider", "data_source", "dataSource")
        or any(
            not _text(reference, 1_000).lower().startswith("tool:")
            for reference in source_refs
            if _text(reference, 1_000)
        )
    )
    entity_uncertain = _field(item, "unverified_entity_mention_count", "unverifiedEntityMentionCount")
    try:
        entity_uncertain_int = int(entity_uncertain or 0)
    except (TypeError, ValueError):
        entity_uncertain_int = 0
    persisted_outcome = _mapping(item.get("outcome"))
    persisted_data_status = _text(persisted_outcome.get("data_status"), 64)
    persisted_nonempty_status = persisted_data_status in {
        "usable",
        "stale",
        "partial",
        "fallback",
        "freshness_unknown",
    }
    checks: list[dict[str, Any]] = []
    if not success:
        checks.append({"code": "tool_failed", "status": "danger", "detail": "工具返回失败。"})
    if success and not semantics["has_data"] and not persisted_nonempty_status:
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
    if success and (result_count_int or collections) and freshness_unknown and _data_time_applicable(item, tool_name=tool_name):
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


def describe_tool_outcome(tool_name: str, observation: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the single semantic outcome used by the run record and audit.

    Execution, source access, and data quality are different facts.  Keeping
    them in one bounded envelope prevents consumers from presenting a
    successful tool call together with a contradictory generic "read failed"
    badge.  ``usable`` means usable within the operation's declared scope; a
    reference-only result can therefore be usable as an index without proving
    that its linked document was read.
    """
    item = _observation_with_result_fields(observation)
    raw_result = _mapping(item.get("result"))
    semantic_payload = _semantic_result_payload(item)
    semantics = classify_result_semantics(semantic_payload)
    persisted_outcome = _mapping(item.get("outcome"))
    persisted_data_status = _text(persisted_outcome.get("data_status"), 64)
    success_value = item["success"] if "success" in item else raw_result.get("success")
    success = success_value is True
    access = describe_tool_access(tool_name, item)
    quality = describe_tool_quality(tool_name, item)
    result_count = _field(
        item,
        "result_count",
        "resultCount",
        "count",
        "item_count",
        "itemCount",
    )
    try:
        result_count_int = int(result_count) if result_count is not None else None
    except (TypeError, ValueError):
        result_count_int = None
    stale = _truthy(_field(item, "is_stale", "isStale"))
    partial = _truthy(_field(item, "partial", "partial_result", "partialResult"))
    fallback = _truthy(_field(item, "fallback_used", "fallbackUsed"))
    freshness_unknown = _truthy(_field(item, "freshness_unknown", "freshnessUnknown"))
    empty = not semantics["has_data"] or (
        access.get("mode") == "content_read"
        and access.get("content_extracted") is not True
    )
    if not success:
        data_status = "error"
    elif empty:
        data_status = "empty"
    elif stale:
        data_status = "stale"
    elif partial:
        data_status = "partial"
    elif fallback:
        data_status = "fallback"
    elif freshness_unknown and _data_time_applicable(item, tool_name=tool_name):
        data_status = "freshness_unknown"
    else:
        data_status = "usable"

    if access.get("mode") == "content_read" and access.get("content_extracted") is not True:
        access_status = "content_unavailable"
    elif access.get("content_extracted") is True:
        access_status = "content_extracted"
    else:
        access_status = str(access.get("mode") or "structured_data")
    quality_status = quality["status"]
    usable = bool(semantics["usable"])
    persisted_execution_status = _text(persisted_outcome.get("execution_status"), 64).lower()
    if (
        success
        and persisted_execution_status == "completed"
        and persisted_data_status in {
            "error",
            "empty",
            "stale",
            "partial",
            "fallback",
            "freshness_unknown",
            "usable",
        }
        and isinstance(persisted_outcome.get("usable"), bool)
    ):
        # ``quality_projection.tool_results`` is intentionally compact.  Its
        # explicit outcome is computed from the full tool record before scalar
        # values and document bodies are removed, so it is authoritative for
        # whether a successful result is usable.
        data_status = persisted_data_status
        usable = bool(persisted_outcome["usable"])
        access_status = _text(persisted_outcome.get("access_status"), 80) or access_status
        quality_status = _text(persisted_outcome.get("quality_status"), 80) or quality_status
    return {
        "execution_status": "completed" if success else "failed",
        "access_status": access_status,
        "data_status": data_status,
        "usable": usable,
        "quality_status": quality_status,
    }


def _camel_key(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _status_for_check(findings: Sequence[Mapping[str, Any]]) -> str:
    actionable = [
        item for item in findings
        if _text(item.get("disposition"), 32) == ACTION_REQUIRED
    ]
    if any(_text(item.get("severity"), 20) == "danger" for item in actionable):
        return "danger"
    if any(_text(item.get("severity"), 20) == "warning" for item in actionable):
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
    disposition: str | None = None,
) -> dict[str, Any]:
    normalized_disposition = disposition if disposition in {ACTION_REQUIRED, ADVISORY} else (
        ACTION_REQUIRED if severity in {"danger", "warning"} else ADVISORY
    )
    return {
        "code": code,
        "severity": severity,
        "disposition": normalized_disposition,
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


def _has_terminal_explanation(
    run: Mapping[str, Any],
    trace: Mapping[str, Any],
    execution_trace: Mapping[str, Any],
) -> bool:
    """Whether an incomplete run already records a concrete terminal cause."""
    for record in (run, trace, execution_trace):
        if any(
            _text(_field(record, key), 500)
            for key in ("error_code", "errorCode", "error_detail", "errorDetail")
        ):
            return True
    stages = _sequence(_field(execution_trace, "stages"))
    if not stages:
        stages = _sequence(_field(trace, "stages"))
    for raw_stage in stages:
        stage = _mapping(raw_stage)
        stage_status = _text(_field(stage, "status", "stage_status", "stageStatus"), 40).lower()
        if stage_status in {"failed", "cancelled"}:
            return True
        if any(
            _text(_field(stage, key), 500)
            for key in ("error_code", "errorCode", "error_detail", "errorDetail")
        ):
            return True
    return False


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
        if action_id and evidence_record_is_eligible(item):
            evidence_by_action[action_id] += 1
        evidence_id = _text(_field(item, "evidence_id", "evidenceId", "id"), 160)
        if evidence_id:
            evidence_by_id[evidence_id] = item

    findings: list[dict[str, Any]] = []
    tool_chain: list[dict[str, Any]] = []
    candidate_links: dict[str, dict[str, Any]] = {}
    failed_tools: list[tuple[str, str, str, str]] = []
    empty_tools: list[tuple[str, str]] = []
    fallback_tools: list[tuple[str, str]] = []
    stale_tools: list[tuple[str, str]] = []
    unknown_time_tools: list[str] = []
    partial_tools: list[tuple[str, str]] = []
    missing_evidence_tools: list[tuple[str, str]] = []
    quality_by_action: dict[str, Mapping[str, Any]] = {}
    usable_action_ids: set[str] = set()
    content_read_calls = 0
    content_extracted_calls = 0
    reference_only_tool_count = 0
    read_urls: set[str] = set()
    read_attempt_urls: set[str] = set()
    unmatched_content_reads: list[str] = []
    repeated_calls: list[str] = []
    quality_issue_groups: defaultdict[str, list[tuple[str, str, str]]] = defaultdict(list)
    previous_call: tuple[str, str] | None = None

    normalized_results = [
        _observation_with_result_fields(raw)
        for raw in raw_results[:_MAX_TOOL_CHAIN]
    ]
    for item in normalized_results:
        tool_name = _text(_field(item, "tool_name", "toolName"), 160) or "未指定工具"
        action_id = _text(_field(item, "action_id", "actionId", "tool_call_id", "toolCallId"), 160)
        outcome = describe_tool_outcome(tool_name, item)
        success = outcome["execution_status"] == "completed"
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
        if success and outcome["data_status"] == "empty":
            empty_tools.append((tool_name, action_id))
        if _truthy(_field(item, "partial", "partial_result", "partialResult")):
            partial_tools.append((tool_name, action_id))
        if _truthy(_field(item, "fallback_used", "fallbackUsed")):
            fallback_tools.append((tool_name, action_id))
        if _truthy(_field(item, "is_stale", "isStale")):
            stale_tools.append((tool_name, action_id))
        if success and _truthy(_field(item, "freshness_unknown", "freshnessUnknown")) and _data_time_applicable(item, tool_name=tool_name):
            unknown_time_tools.append(tool_name)
        collections, _has_empty_collection = _result_collection_state(item)
        has_result_items = bool(collections) or (
            result_count_int is not None and result_count_int > 0
        )
        if success and has_result_items and not evidence_by_action.get(action_id):
            missing_evidence_tools.append((tool_name, action_id))
        if not success:
            errors = _field(item, "errors", "error")
            error_text = (
                "；".join(filter(None, (_text(error, 500) for error in _sequence(errors))))[:500]
                if isinstance(errors, (list, tuple)) else _text(errors, 500)
            ) or _text(_field(item, "error_code", "errorCode"), 160) or "未提供具体错误"
            failed_tools.append((tool_name, action_id, error_text, _arguments_fingerprint(item)))
        else:
            if action_id and outcome.get("usable"):
                usable_action_ids.add(action_id)

        # Re-run the deterministic checks against the current compact
        # projection.  Persisted ``data_quality`` is useful display metadata,
        # but keeping it as the audit authority would freeze old heuristics in
        # historical runs (for example, a multi-segment row misread as a
        # duplicate after its discriminator became available in the projection).
        quality = describe_tool_quality(tool_name, item)
        if action_id:
            quality_by_action[action_id] = quality
        for quality_check in _sequence(quality.get("checks")):
            check = _mapping(quality_check)
            code = _text(check.get("code"), 80)
            if code and _text(check.get("status"), 20) != "clear":
                quality_issue_groups[code].append((tool_name, action_id, _text(check.get("detail"), 240)))

        fingerprint = (tool_name, _arguments_fingerprint(item))
        if previous_call == fingerprint and not _truthy(_field(item, "reused")):
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
                    outcome["access_status"]
                ),
                "outcome": dict(outcome),
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
        for item in normalized_results
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
    claim_action_ids = {
        action_id
        for claim in claims
        for evidence_id in _sequence(_field(claim, "evidence_ids", "evidenceIds"))
        for evidence_item in [evidence_by_id.get(_text(evidence_id, 160))]
        for action_id in [_text(_field(evidence_item or {}, "action_id", "actionId"), 160)]
        if action_id
    }
    cited_action_ids = set(cited_reference_actions) | claim_action_ids
    # A recovery must be chronological and tied to the failed read. Merely
    # finding a successful search elsewhere in the run proves neither.
    same_call_recovered_action_ids: set[str] = set()
    retained_evidence_action_ids: set[str] = set()
    web_fallback_recovered_action_ids: set[str] = set()
    for index, failed in enumerate(normalized_results):
        failed_id = _text(_field(failed, "action_id", "actionId", "tool_call_id", "toolCallId"), 160)
        if not any(action_id == failed_id for _, action_id, _, _ in failed_tools):
            continue
        failed_name = _text(_field(failed, "tool_name", "toolName"), 160)
        fingerprint = _arguments_fingerprint(failed)
        failed_urls = {_canonical_url(url) for url in _reference_urls(failed) + _read_urls(failed, {})}
        failed_urls.discard("")
        for candidate_index, candidate in enumerate(normalized_results):
            candidate_id = _text(_field(candidate, "action_id", "actionId", "tool_call_id", "toolCallId"), 160)
            if candidate_id not in usable_action_ids:
                continue
            candidate_name = _text(_field(candidate, "tool_name", "toolName"), 160)
            same_request = candidate_name == failed_name and _arguments_fingerprint(candidate) == fingerprint
            if same_request and candidate_index > index:
                same_call_recovered_action_ids.add(failed_id)
            elif same_request and candidate_index < index and candidate_id in cited_action_ids:
                retained_evidence_action_ids.add(failed_id)
            elif (
                candidate_index > index
                and candidate_name == "read_web_source"
                and candidate_id in cited_action_ids
                and evidence_by_action.get(candidate_id)
                and _content_extracted(candidate, {}, "content_read")
                and failed_urls.intersection(_canonical_url(url) for url in _read_urls(candidate, {}))
            ):
                web_fallback_recovered_action_ids.add(failed_id)
    # One failure has one resolution, never two counted recoveries.
    web_fallback_recovered_action_ids -= same_call_recovered_action_ids
    retained_evidence_action_ids -= same_call_recovered_action_ids | web_fallback_recovered_action_ids
    recovered_failed_action_ids = (
        same_call_recovered_action_ids | web_fallback_recovered_action_ids | retained_evidence_action_ids
    )
    cited_empty_action_ids = {
        action_id
        for _tool_name, action_id in empty_tools
        if action_id and action_id in cited_action_ids
    }
    empty_has_usable_alternative = bool(usable_action_ids)
    access_targets = [
        {
            "url": entry.get("url"),
            "kind": entry.get("kind"),
            "action_id": (entry.get("actions") or [""])[0],
            "action_ids": list((entry.get("actions") or [])[1:]),
        }
        for entry in candidate_links.values()
        if entry.get("actions")
    ]
    cited_access = reference_access_status(
        cited_action_ids=set(cited_reference_actions),
        targets=access_targets,
        selected_urls=read_attempt_urls,
        successful_urls=read_urls,
        url_normalizer=_canonical_url,
    )
    selection_required_actions = {
        action_id
        for action_id, access in cited_access.items()
        if access.get("selection_required") is True
    }
    cited_unread_links_by_action: dict[str, list[str]] = defaultdict(list)
    for action_id, access in cited_access.items():
        if access.get("selection_required") is True:
            unresolved_targets = access.get("candidate_targets") or []
        else:
            # A selected but failed URL is already represented by the more
            # specific pending-article/document finding below.  Keep this
            # finding for an unambiguous candidate that was never selected.
            unresolved_targets = [
                target
                for target in access.get("pending_targets") or []
                if _canonical_url(target.get("url")) not in read_attempt_urls
            ]
        for target in unresolved_targets:
            url = str(target.get("url") or "")
            if url and url not in cited_unread_links_by_action[action_id]:
                cited_unread_links_by_action[action_id].append(url)
    cited_action_ids = set(cited_reference_actions) | claim_action_ids
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
        for tool_name, action_id, error, _fingerprint in failed_tools:
            grouped[tool_name].append((action_id, error))
        for tool_name, failures in grouped.items():
            detail = failures[0][1]
            suffix = f"；共 {len(failures)} 次失败" if len(failures) > 1 else ""
            recovered_count = sum(
                action_id in recovered_failed_action_ids
                for action_id, _error in failures
                if action_id
            )
            unresolved_count = len(failures) - recovered_count
            same_count = sum(
                action_id in same_call_recovered_action_ids
                for action_id, _error in failures
                if action_id
            )
            web_fallback_count = sum(
                action_id in web_fallback_recovered_action_ids
                for action_id, _error in failures
                if action_id
            )
            retained_count = sum(action_id in retained_evidence_action_ids for action_id, _ in failures)
            recovery_details: list[str] = []
            if same_count:
                recovery_details.append(f"{same_count} 次随后由相同参数的成功调用恢复")
            if web_fallback_count:
                recovery_details.append(f"{web_fallback_count} 次随后由相同来源的正文读取恢复并进入引用")
            if retained_count:
                recovery_details.append(f"{retained_count} 次重复读取失败，但回答已引用此前相同请求的可用证据（不是随后恢复）")
            recovered_suffix = (
                "；其中 " + "，".join(recovery_details)
                if recovery_details
                else ""
            )
            title = (
                f"工具 {tool_name} 执行失败"
                if unresolved_count
                else (
                    f"工具 {tool_name} 曾失败但已由网页兜底恢复"
                    if web_fallback_count
                    else (
                        f"工具 {tool_name} 重复读取失败，已引用此前证据"
                        if retained_count else f"工具 {tool_name} 曾失败但已恢复"
                    )
                )
            )
            findings.append(
                _finding(
                    code="tool_execution_failed",
                    severity="danger" if unresolved_count else "info",
                    category="execution",
                    title=title,
                    detail=f"工具调用记录失败：{detail}{suffix}{recovered_suffix}。",
                    remediation=(
                        "查看失败调用的请求参数、来源尝试链路和服务端错误；"
                        "已恢复的调用保留为诊断记录，不再把它当作本轮未恢复故障。"
                        if unresolved_count
                        else "回答已有对应的可用证据；保留失败尝试和实际引用链路，不把它计为未恢复的取证故障。"
                    ),
                    tool_names=[tool_name],
                    action_ids=[item[0] for item in failures],
                    disposition=ACTION_REQUIRED if unresolved_count else ADVISORY,
                )
            )

    if pending_documents:
        cited = any(
            _text(action, 160) in cited_action_ids
            for entry in pending_documents
            for action in entry.get("actions") or []
        )
        findings.append(
            _finding(
                code="content_read_incomplete_document",
                severity="warning" if cited else "info",
                category="content_access",
                title="已引用文档，但正文读取未成功" if cited else "已选择文档，但正文读取未成功",
                detail=(
                    f"有 {len(pending_documents)} 个已选择的 PDF/文档链接没有成功提取正文；"
                    "这些链接只能证明存在来源引用，不能证明模型已经阅读过文档。"
                ),
                remediation="对已选择的文档重试网页/PDF读取工具；若仍失败，回答中明确标注正文未核验。",
                tool_names=sorted({tool for entry in pending_documents for tool in entry["tools"]}),
                action_ids=[action for entry in pending_documents for action in entry["actions"]],
                links=[entry["url"] for entry in pending_documents],
                disposition=ACTION_REQUIRED if cited else ADVISORY,
            )
        )
    if unselected_documents:
        findings.append(
            _finding(
                code="reference_not_selected_document",
                severity="info",
                category="content_access",
                title="仍有文档候选未选择读取",
                detail=(
                    f"工具返回了 {len(unselected_documents)} 个未选择的 PDF/文档链接；"
                    + (
                        "本次已有其他文档正文成功读取，未选择的候选不代表执行失败。"
                        if has_document_read
                        else "本次没有发起这些候选的正文读取，未选择本身不代表执行失败。"
                    )
                ),
                remediation="仅在结论需要这些候选时再读取；未读取来源只能作为链接/索引展示。",
                tool_names=sorted({tool for entry in unselected_documents for tool in entry["tools"]}),
                action_ids=[action for entry in unselected_documents for action in entry["actions"]],
                links=[entry["url"] for entry in unselected_documents],
                confidence=0.98,
            )
        )
    if pending_articles:
        cited = any(
            _text(action, 160) in cited_action_ids
            for entry in pending_articles
            for action in entry.get("actions") or []
        )
        findings.append(
            _finding(
                code="content_read_incomplete_article",
                severity="warning" if cited else "info",
                category="content_access",
                title="已引用文章，但正文读取未成功" if cited else "已选择文章，但正文读取未成功",
                detail=(
                    f"有 {len(pending_articles)} 个已选择的新闻/文章链接没有成功提取正文；"
                    "文章标题或摘要不等于已经核对过正文。"
                ),
                remediation="对已选择的文章重试网页读取工具；若仍失败，回答中明确标注正文未核验。",
                tool_names=sorted({tool for entry in pending_articles for tool in entry["tools"]}),
                action_ids=[action for entry in pending_articles for action in entry["actions"]],
                links=[entry["url"] for entry in pending_articles],
                disposition=ACTION_REQUIRED if cited else ADVISORY,
            )
        )
    if unselected_articles:
        findings.append(
            _finding(
                code="reference_not_selected_article",
                severity="info",
                category="content_access",
                title="仍有文章候选未选择读取",
                detail=(
                    f"工具返回了 {len(unselected_articles)} 个未选择的新闻/文章链接；"
                    + (
                        "本次已有其他文章正文成功读取，未选择的候选不代表执行失败。"
                        if has_article_read
                        else "本次没有发起这些候选的正文读取，未选择本身不代表执行失败。"
                    )
                ),
                remediation="仅在结论需要这些候选时再读取；未读取来源只能作为链接/索引展示。",
                tool_names=sorted({tool for entry in unselected_articles for tool in entry["tools"]}),
                action_ids=[action for entry in unselected_articles for action in entry["actions"]],
                links=[entry["url"] for entry in unselected_articles],
                confidence=0.98,
            )
        )
    if unmatched_content_reads:
        findings.append(
            _finding(
                code="content_read_unmapped",
                severity="info",
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
                    + (
                        f"其中 {len(selection_required_actions)} 次多链接来源尚未选择要核验的正文 URL；"
                        "候选索引不要求全部读取，但至少要读取实际用于结论的相关链接。"
                        if selection_required_actions
                        else f"其中 {len(cited_links)} 个被要求核验的链接尚未成功提取正文；引用索引不能替代正文核验。"
                    )
                ),
                remediation="按工具调用选择并读取实际支撑结论的相关正文 URL；未选择的候选不要求全部读取，若只使用索引信息则明确降低结论确定性。",
                tool_names=cited_tool_names,
                action_ids=list(cited_unread_links_by_action),
                links=cited_links,
            )
        )

    if empty_tools:
        empty_requires_action = (
            not empty_has_usable_alternative or bool(cited_empty_action_ids)
        )
        findings.append(
            _finding(
                code="successful_empty_result",
                severity="warning" if empty_requires_action else "info",
                category="data_quality",
                title="部分工具成功返回但没有数据",
                detail=(
                    f"{len(empty_tools)} 个成功调用返回 0 条结果："
                    f"{', '.join(sorted({item[0] for item in empty_tools})[:5])}。"
                    + (
                        "本次没有其他可用观察，结果可能无法支撑完整回答。"
                        if empty_requires_action
                        else "本次存在其他可用观察，且空结果没有被结论引用，作为已保留的诊断信息。"
                    )
                ),
                remediation=(
                    "确认查询范围和数据源状态，并在必要时使用 search_web_source(source_id=auto) 或其他可用来源。"
                    if empty_requires_action
                    else "保留空结果记录；当前结论没有依赖它，不需要单独重试。"
                ),
                tool_names=sorted({item[0] for item in empty_tools}),
                action_ids=[item[1] for item in empty_tools],
                disposition=ACTION_REQUIRED if empty_requires_action else ADVISORY,
            )
        )
    if fallback_tools:
        untraceable_fallbacks = [
            item for item in fallback_tools
            if not quality_by_action.get(item[1], {}).get("source_present", False)
        ]
        findings.append(
            _finding(
                code="fallback_used",
                severity="warning" if untraceable_fallbacks else "info",
                category="data_quality",
                title="部分资料使用了降级来源",
                detail=(
                    f"{len(fallback_tools)} 次工具调用发生来源降级："
                    f"{', '.join(sorted({item[0] for item in fallback_tools})[:6])}。"
                    + (
                        "部分降级结果没有可追溯来源，可能影响结论。"
                        if untraceable_fallbacks
                        else "降级结果仍保留来源标识，作为可用结果记录，不单独判定为运行失败。"
                    )
                ),
                remediation=(
                    "核对降级来源是否仍满足主体、时间和字段要求，并补充可追溯来源。"
                    if untraceable_fallbacks
                    else "核对降级来源的主体、时间和字段范围；若满足要求，可继续使用并保留降级标记。"
                ),
                tool_names=sorted({item[0] for item in fallback_tools}),
                action_ids=[item[1] for item in fallback_tools],
                disposition=ACTION_REQUIRED if untraceable_fallbacks else ADVISORY,
            )
        )
    if stale_tools:
        used_stale = [item for item in stale_tools if item[1] and item[1] in cited_action_ids]
        findings.append(
            _finding(
                code="stale_data",
                severity="warning" if used_stale else "info",
                category="data_quality",
                title="发现过期资料",
                detail=(
                    f"{len(stale_tools)} 次工具调用返回的数据被标记为过期。"
                    + ("其中部分已进入结论引用。" if used_stale else "当前没有发现它们进入结论引用。")
                ),
                remediation="不要把过期观察表述为当前事实；若该观察支撑结论，应重新获取带有效时间的数据。",
                tool_names=sorted({item[0] for item in stale_tools}),
                action_ids=[item[1] for item in stale_tools],
                disposition=ACTION_REQUIRED if used_stale else ADVISORY,
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
        used_partial = [item for item in partial_tools if item[1] and item[1] in cited_action_ids]
        findings.append(
            _finding(
                code="partial_result",
                severity="warning" if used_partial else "info",
                category="data_quality",
                title="部分资料返回不完整",
                detail=(
                    f"{len(partial_tools)} 次工具调用返回了 partial 结果。"
                    + ("其中部分已进入结论引用。" if used_partial else "当前没有发现它们进入结论引用。")
                ),
                remediation="检查是否需要继续分页、读取下一段或补充来源；不要把不完整观察当成完整覆盖。",
                tool_names=sorted({item[0] for item in partial_tools}),
                action_ids=[item[1] for item in partial_tools],
                disposition=ACTION_REQUIRED if used_partial else ADVISORY,
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
        cited = any(action_id and action_id in cited_action_ids for _tool, action_id, _detail in records)
        # A warning is a non-fatal diagnostic in the common tool contract.  It
        # must remain visible, but it cannot by itself make a cited result an
        # action item; tools use partial/errors/stale markers for that.
        warning_is_advisory = code == "provider_warning"
        findings.append(
            _finding(
                code=f"data_quality_{code}",
                severity="info" if warning_is_advisory else "warning" if cited else "info",
                category="data_quality",
                title=title,
                detail=f"{len(records)} 个工具调用触发了该检查；示例：{records[0][2] or '未提供详细说明'}。",
                remediation=remediation,
                tool_names=sorted({record[0] for record in records}),
                action_ids=[record[1] for record in records],
                confidence=0.9,
                disposition=(
                    ADVISORY if warning_is_advisory else ACTION_REQUIRED if cited else ADVISORY
                ),
            )
        )
    if missing_evidence_tools:
        findings.append(
            _finding(
                code="successful_result_without_evidence",
                severity="info",
                category="evidence",
                title="成功返回的数据没有进入证据台账",
                detail=f"{len(missing_evidence_tools)} 个返回数据的工具调用没有关联证据记录。",
                remediation="确认这些数据是否真的被模型使用；如果支撑回答，应生成并关联 evidence_id。",
                tool_names=sorted({item[0] for item in missing_evidence_tools}),
                action_ids=[item[1] for item in missing_evidence_tools],
                confidence=0.88,
                disposition=ADVISORY,
            )
        )
    if repeated_calls:
        findings.append(
            _finding(
                code="repeated_identical_tool_call",
                severity="info",
                category="model_behavior",
                title="发现连续重复的工具调用",
                detail=f"至少有一次工具在相同参数下被连续调用：{', '.join(sorted(set(repeated_calls)))}。",
                remediation="检查是否因为模型没有理解返回结果、工具结果未进入上下文或重试去重失效。",
                tool_names=sorted(set(repeated_calls)),
                confidence=0.8,
                disposition=ADVISORY,
            )
        )

    failed_claims = []
    unsupported_claims = []
    for claim in claims:
        evidence_ids = _sequence(_field(claim, "evidence_ids", "evidenceIds"))
        requires_evidence = _field(claim, "requires_evidence", "requiresEvidence") is not False
        if requires_evidence and not evidence_ids:
            unsupported_claims.append(claim)
        if not claim_checks_pass(_mapping(claim)):
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
                    f"{len(failed_claims)} 个结论的引用、主体、来源或时间检查未通过。"
                ),
                remediation="打开结论与证据映射，补充取证或降低回答中的确定性表述。",
                confidence=0.95,
                disposition=ACTION_REQUIRED,
            )
        )

    run = _mapping(root.get("run"))
    trace = _mapping(root.get("trace"))
    execution_trace = _mapping(projection.get("execution_trace"))
    loop = _mapping(projection.get("budgets")) or _mapping(_field(execution_trace, "loop"))
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
    if (
        status in {"failed", "blocked", "partial", "cancelled"}
        and not failed_tools
        and not _has_terminal_explanation(run, trace, execution_trace)
    ):
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
    action_required_count = sum(
        item.get("disposition") == ACTION_REQUIRED for item in findings
    )
    advisory_count = sum(
        item.get("disposition") == ADVISORY for item in findings
    )
    actionable_danger_count = sum(
        item["severity"] == "danger" and item.get("disposition") == ACTION_REQUIRED
        for item in findings
    )
    actionable_warning_count = sum(
        item["severity"] == "warning" and item.get("disposition") == ACTION_REQUIRED
        for item in findings
    )
    risk_score = min(100, actionable_danger_count * 35 + actionable_warning_count * 12)
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
        "schema_version": INSPECTION_SCHEMA_VERSION,
        "status": audit_status,
        "attention_level": "urgent" if actionable_danger_count else "review" if actionable_warning_count else "none",
        "risk_score": risk_score,
        "issue_count": len(findings),
        "action_required_count": action_required_count,
        "advisory_count": advisory_count,
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
    "ACTION_REQUIRED",
    "ADVISORY",
    "CONTENT_READER_TOOLS",
    "INSPECTION_SCHEMA_VERSION",
    "REFERENCE_ONLY_TOOL_KINDS",
    "build_behavior_audit",
    "describe_tool_access",
    "describe_tool_outcome",
    "describe_tool_quality",
]
