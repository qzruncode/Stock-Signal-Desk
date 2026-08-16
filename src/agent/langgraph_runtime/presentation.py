"""Bounded, user-safe projections for the execution timeline.

The graph checkpoint retains canonical arguments and tool results.  The
browser only needs a small, truthful explanation of what was requested and
what came back.  These helpers derive that explanation structurally, without
knowing a business domain, choosing a tool, or prescribing a workflow.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


_SECRET_KEY_PARTS = (
    "api_key",
    "apikey",
    "access_key",
    "secret",
    "password",
    "passwd",
    "authorization",
    "auth_token",
    "access_token",
    "refresh_token",
    "cookie",
)
_COLLECTION_KEYS = (
    "results",
    "items",
    "records",
    "rows",
    "entries",
    "documents",
    "articles",
    "data",
)
_NON_RESULT_COLLECTION_KEYS = {
    "errors",
    "warnings",
    "attempts",
    "attachments",
    "source_refs",
    "references",
}
_COUNT_KEYS = (
    "result_count",
    "item_count",
    "returned_count",
    "record_count",
    "row_count",
    "total_count",
    "count",
)
_TITLE_KEYS = ("title", "name", "headline", "label", "subject")
_IDENTIFIER_KEYS = ("code", "symbol", "ticker")
_URL_KEYS = ("url", "link", "source_url", "final_url")
_SOURCE_KEYS = ("source", "publisher", "provider", "site", "feed_name", "data_source")
_DATE_KEYS = (
    "published_date",
    "published_at",
    "published",
    "pub_date",
    "report_date",
    "date",
    "data_time",
)
_SUMMARY_KEYS = ("snippet", "summary", "description", "abstract", "content_preview")
_ATTRIBUTE_EXCLUSIONS = {
    *_TITLE_KEYS,
    *_IDENTIFIER_KEYS,
    *_URL_KEYS,
    *_SOURCE_KEYS,
    *_DATE_KEYS,
    *_SUMMARY_KEYS,
    "content",
    "content_html",
    "output",
    "success",
    "partial",
    "errors",
    "warnings",
    "attachments",
    "item_ref",
    "content_hash",
    "result_type",
    "search_provider",
}


def _text(value: Any, limit: int) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (str, int, float)):
        rendered = str(value).strip()
        return rendered[:limit]
    return ""


def _is_secret_key(key: str, configured: set[str]) -> bool:
    normalized = key.strip().lower()
    return normalized in configured or any(part in normalized for part in _SECRET_KEY_PARTS)


def _argument_value(
    value: Any,
    *,
    configured_sensitive: set[str],
    depth: int = 0,
) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:1_000]
    if depth >= 4:
        return "[嵌套参数已折叠]"
    if isinstance(value, Mapping):
        projected: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:20]:
            key = str(raw_key)[:96]
            projected[key] = (
                "***"
                if _is_secret_key(key, configured_sensitive)
                else _argument_value(
                    item,
                    configured_sensitive=configured_sensitive,
                    depth=depth + 1,
                )
            )
        return projected
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        projected = [
            _argument_value(
                item,
                configured_sensitive=configured_sensitive,
                depth=depth + 1,
            )
            for item in list(value)[:20]
        ]
        if len(value) > len(projected):
            projected.append(f"[其余 {len(value) - len(projected)} 项已折叠]")
        return projected
    return str(value)[:1_000]


def project_arguments_for_timeline(
    arguments: Mapping[str, Any] | None,
    *,
    sensitive_fields: Sequence[str] = (),
    server_controlled_fields: Sequence[str] = ("confirmed",),
) -> dict[str, Any]:
    """Return bounded authored arguments with secrets and server fields hidden."""
    if not isinstance(arguments, Mapping):
        return {}
    sensitive = {str(item).strip().lower() for item in sensitive_fields}
    controlled = {str(item).strip() for item in server_controlled_fields}
    projected: dict[str, Any] = {}
    for raw_key, value in list(arguments.items())[:24]:
        key = str(raw_key)[:96]
        if key in controlled:
            continue
        projected[key] = (
            "***"
            if _is_secret_key(key, sensitive)
            else _argument_value(value, configured_sensitive=sensitive)
        )
    return projected


def _collection_candidates(
    value: Mapping[str, Any],
    *,
    depth: int = 0,
) -> list[tuple[int, int, Sequence[Any]]]:
    candidates: list[tuple[int, int, Sequence[Any]]] = []
    if depth > 2:
        return candidates
    for order, (raw_key, item) in enumerate(value.items()):
        key = str(raw_key).strip().lower()
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            sequence = list(item)
            if not sequence or key in _NON_RESULT_COLLECTION_KEYS:
                continue
            preferred = 120 - _COLLECTION_KEYS.index(key) if key in _COLLECTION_KEYS else 0
            structured = 20 if any(isinstance(entry, Mapping) for entry in sequence[:12]) else 0
            candidates.append((preferred + structured - depth * 10, -order, sequence))
        elif isinstance(item, Mapping):
            candidates.extend(_collection_candidates(item, depth=depth + 1))
    return candidates


def _result_collection(result: Mapping[str, Any]) -> list[Any]:
    candidates = _collection_candidates(result)
    if not candidates:
        return []
    return list(max(candidates, key=lambda item: (item[0], item[1]))[2])


def _first(record: Mapping[str, Any], keys: Sequence[str], limit: int) -> str:
    for key in keys:
        rendered = _text(record.get(key), limit)
        if rendered:
            return rendered
    return ""


def _source_text(value: Any) -> str:
    if isinstance(value, Mapping):
        values = [
            _text(value.get(key), 160)
            for key in ("provider", "publisher", "name", "feed_name", "catalog_name", "id")
        ]
        return " · ".join(dict.fromkeys(item for item in values if item))[:320]
    return _text(value, 320)


def _result_item(value: Any, index: int) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"title": _text(value, 360) or f"结果 {index}"}
    title = _first(value, _TITLE_KEYS, 360)
    identifier = _first(value, _IDENTIFIER_KEYS, 80)
    if identifier and title and identifier not in title:
        title = f"{identifier} {title}"
    elif not title:
        title = identifier
    url = _first(value, _URL_KEYS, 1_000)
    source = ""
    for key in _SOURCE_KEYS:
        source = _source_text(value.get(key))
        if source:
            break
    published_at = _first(value, _DATE_KEYS, 160)
    summary = _first(value, _SUMMARY_KEYS, 500)
    attributes: list[dict[str, str]] = []
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip()
        normalized = key.lower()
        if (
            normalized in _ATTRIBUTE_EXCLUSIONS
            or normalized.startswith("_")
            or _is_secret_key(normalized, set())
        ):
            continue
        rendered = _text(raw_value, 160)
        if rendered:
            attributes.append({"name": key[:80], "value": rendered})
        if len(attributes) >= 5:
            break
    if not title:
        fallback = " · ".join(
            f"{item['name']}={item['value']}" for item in attributes[:3]
        )
        title = fallback or f"结果 {index}"
    projected: dict[str, Any] = {"title": title}
    if url:
        projected["url"] = url
    if source:
        projected["source"] = source
    if published_at:
        projected["published_at"] = published_at
    if summary:
        projected["summary"] = summary
    if attributes:
        projected["attributes"] = attributes
    return projected


def _append_unique(values: list[str], value: Any, *, limit: int = 320) -> None:
    rendered = _source_text(value)[:limit]
    if rendered and rendered not in values:
        values.append(rendered)


def _source_labels(result: Mapping[str, Any], items: Sequence[Mapping[str, Any]]) -> list[str]:
    labels: list[str] = []
    for key in ("provider", "publisher", "data_source"):
        _append_unique(labels, result.get(key))
    source = result.get("source")
    if isinstance(source, Mapping):
        combined = " · ".join(
            dict.fromkeys(
                item
                for key in ("provider", "name", "feed_name", "catalog_name", "id")
                if (item := _text(source.get(key), 160))
            )
        )
        _append_unique(labels, combined)
    else:
        _append_unique(labels, source)
    for item in items:
        _append_unique(labels, item.get("source"))
        if len(labels) >= 16:
            break
    return labels[:16]


def _is_url(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered.startswith("https://") or lowered.startswith("http://")


def project_tool_result_for_timeline(
    result: Mapping[str, Any] | None,
    *,
    source_refs: Sequence[Any] = (),
    max_items: int = 12,
) -> dict[str, Any]:
    """Build a compact result ledger for live and hydrated timeline rows."""
    if not isinstance(result, Mapping):
        return {}
    collection = _result_collection(result)
    item_limit = max(1, min(int(max_items), 20))
    items = [_result_item(item, index + 1) for index, item in enumerate(collection[:item_limit])]

    explicit_count: int | None = None
    for key in _COUNT_KEYS:
        value = result.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            explicit_count = value
            break
    result_count = explicit_count if explicit_count is not None else (len(collection) if collection else None)
    omitted = max(0, (result_count or len(collection)) - len(items))

    links: list[str] = []
    for raw in source_refs:
        rendered = _text(raw, 1_000)
        if rendered and _is_url(rendered) and rendered not in links:
            links.append(rendered)
    for item in items:
        rendered = _text(item.get("url"), 1_000)
        if rendered and rendered not in links:
            links.append(rendered)

    summary = ""
    for key in ("message", "summary", "output", "content"):
        summary = _text(result.get(key), 800)
        if summary:
            break

    projected: dict[str, Any] = {
        "source_labels": _source_labels(result, items),
        "result_items": items,
        "reference_links": links[:16],
    }
    access = result.get("content_access") or result.get("retrieval_audit")
    if isinstance(access, Mapping):
        compact_access: dict[str, Any] = {}
        for key in (
            "mode",
            "content_read",
            "content_extracted",
            "content_read_required",
            "reference_link_count",
            "content_length",
            "extraction_method",
            "requested_url",
            "final_url",
            "note",
        ):
            if key in access and access[key] not in (None, ""):
                compact_access[key] = access[key]
        if compact_access:
            projected["content_access"] = compact_access
    if result_count is not None:
        projected["result_count"] = result_count
    if omitted:
        projected["omitted_result_count"] = omitted
    if summary and not items:
        projected["result_summary"] = summary
    return projected


__all__ = ["project_arguments_for_timeline", "project_tool_result_for_timeline"]
