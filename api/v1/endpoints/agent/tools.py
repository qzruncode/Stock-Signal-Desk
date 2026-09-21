"""Domain-neutral presentation helpers for atomic tool observations."""

from __future__ import annotations

import json
from typing import Any, Mapping


MAX_COLLECTION_ITEMS = 48
MAX_MAPPING_KEYS = 120
MAX_TEXT_CHARACTERS = 12_000
MAX_NESTING_DEPTH = 12
# Tool results cross the SSE/UI boundary and may be rendered while several
# actions are still running.  Per-field limits alone are insufficient: a
# result with many individually valid long fields can still freeze the
# browser.  This is a transport/presentation limit only; canonical results
# continue to live in the durable tool-step record.
MAX_SERIALIZED_RESULT_CHARACTERS = 48_000


def _format_result(result: Any) -> str:
    """Serialize a projected observation without silently changing it."""
    return json.dumps(result, ensure_ascii=False, default=str)


def _bounded_value(
    value: Any,
    *,
    depth: int = 0,
    collection_limit: int = MAX_COLLECTION_ITEMS,
    mapping_limit: int = MAX_MAPPING_KEYS,
    text_limit: int = MAX_TEXT_CHARACTERS,
) -> Any:
    """Apply one generic size policy independent of tool or business domain."""
    if depth >= MAX_NESTING_DEPTH:
        return {"_omitted": True, "reason": "maximum_nesting_depth"}
    if isinstance(value, Mapping):
        items = list(value.items())
        bounded = {
            str(key): _bounded_value(
                item,
                depth=depth + 1,
                collection_limit=collection_limit,
                mapping_limit=mapping_limit,
                text_limit=text_limit,
            )
            for key, item in items[:mapping_limit]
        }
        if len(items) > mapping_limit:
            bounded["_omitted_key_count"] = len(items) - mapping_limit
        return bounded
    if isinstance(value, (list, tuple)):
        bounded = [
            _bounded_value(
                item,
                depth=depth + 1,
                collection_limit=collection_limit,
                mapping_limit=mapping_limit,
                text_limit=text_limit,
            )
            for item in value[:collection_limit]
        ]
        if len(value) > collection_limit:
            bounded.append(
                {
                    "_omitted_item_count": len(value) - collection_limit,
                    "_original_item_count": len(value),
                }
            )
        return bounded
    if isinstance(value, str) and len(value) > text_limit:
        return value[:text_limit].rstrip() + "…"
    return value


def _serialized_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str))


def _fallback_payload(result: Mapping[str, Any], *, original_size: int) -> dict[str, Any]:
    """Return the smallest useful generic envelope for a pathological result.

    Keep common result-contract fields first so status, errors and source-time
    semantics remain visible even when every rich value must be reduced.  The
    remaining keys are sampled in their original order; no tool/domain names
    participate in this policy.
    """
    envelope_keys = (
        "success",
        "partial",
        "errors",
        "warnings",
        "error_code",
        "data_time",
        "data_time_provenance",
        "data_time_note",
        "is_stale",
        "freshness_unknown",
    )
    compact: dict[str, Any] = {}
    for key in envelope_keys:
        if key in result:
            compact[key] = _bounded_value(
                result[key],
                collection_limit=3,
                mapping_limit=6,
                text_limit=600,
            )
    omitted = 0
    for key, value in result.items():
        if key in compact:
            continue
        compact[str(key)] = _bounded_value(
            value,
            collection_limit=2,
            mapping_limit=6,
            text_limit=600,
        )
        if _serialized_size(compact) > MAX_SERIALIZED_RESULT_CHARACTERS - 1_200:
            compact.pop(str(key), None)
            omitted += 1
            break
    omitted += sum(1 for key in result if key not in compact)
    compact["_payload_truncated"] = True
    compact["_original_serialized_characters"] = original_size
    if omitted:
        compact["_omitted_top_level_key_count"] = omitted
    return compact


def _fit_payload_budget(result: Mapping[str, Any]) -> tuple[dict[str, Any], bool, int]:
    """Bound a result by total serialized size without tool-specific logic."""
    original_size = _serialized_size(result)
    # Progressively shrink every recursive shape in the same way.  This keeps
    # ordinary source rows usable before falling back to an envelope preview.
    profiles = (
        (MAX_COLLECTION_ITEMS, MAX_MAPPING_KEYS, MAX_TEXT_CHARACTERS),
        (24, 64, 6_000),
        (12, 32, 2_400),
        (6, 16, 1_200),
        (3, 8, 600),
    )
    for collection_limit, mapping_limit, text_limit in profiles:
        candidate = _bounded_value(
            result,
            collection_limit=collection_limit,
            mapping_limit=mapping_limit,
            text_limit=text_limit,
        )
        if _serialized_size(candidate) <= MAX_SERIALIZED_RESULT_CHARACTERS:
            return dict(candidate), candidate != dict(result), original_size
    return _fallback_payload(result, original_size=original_size), True, original_size


def _compact_tool_result(tool_name: str, result: Any) -> Any:
    """Project one atomic observation under the shared transport size policy."""
    if not isinstance(result, Mapping):
        return _bounded_value(result)
    projected, compacted, original_size = _fit_payload_budget(result)
    projected["_tool_payload_meta"] = {
        "tool_name": tool_name,
        "payload_policy": "generic_bounded_observation",
        "compacted": compacted,
        "payload_budget_characters": MAX_SERIALIZED_RESULT_CHARACTERS,
        "original_serialized_characters": original_size,
        "source_scope": "atomic_tool_result",
    }
    return projected


__all__ = [
    "_compact_tool_result",
    "_format_result",
]
