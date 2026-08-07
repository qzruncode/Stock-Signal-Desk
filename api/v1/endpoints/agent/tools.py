"""Domain-neutral presentation helpers for atomic tool observations."""

from __future__ import annotations

import json
from typing import Any, Mapping


MAX_COLLECTION_ITEMS = 48
MAX_MAPPING_KEYS = 120
MAX_TEXT_CHARACTERS = 12_000
MAX_NESTING_DEPTH = 12


def _format_result(result: Any) -> str:
    """Serialize a projected observation without silently changing it."""
    return json.dumps(result, ensure_ascii=False, default=str)


def _bounded_value(value: Any, *, depth: int = 0) -> Any:
    """Apply one generic size policy independent of tool or business domain."""
    if depth >= MAX_NESTING_DEPTH:
        return {"_omitted": True, "reason": "maximum_nesting_depth"}
    if isinstance(value, Mapping):
        items = list(value.items())
        bounded = {
            str(key): _bounded_value(item, depth=depth + 1)
            for key, item in items[:MAX_MAPPING_KEYS]
        }
        if len(items) > MAX_MAPPING_KEYS:
            bounded["_omitted_key_count"] = len(items) - MAX_MAPPING_KEYS
        return bounded
    if isinstance(value, (list, tuple)):
        bounded = [
            _bounded_value(item, depth=depth + 1)
            for item in value[:MAX_COLLECTION_ITEMS]
        ]
        if len(value) > MAX_COLLECTION_ITEMS:
            bounded.append(
                {
                    "_omitted_item_count": len(value) - MAX_COLLECTION_ITEMS,
                    "_original_item_count": len(value),
                }
            )
        return bounded
    if isinstance(value, str) and len(value) > MAX_TEXT_CHARACTERS:
        return value[:MAX_TEXT_CHARACTERS].rstrip() + "…"
    return value


def _compact_tool_result(tool_name: str, result: Any) -> Any:
    """Project one atomic observation under the shared transport size policy."""
    if not isinstance(result, Mapping):
        return _bounded_value(result)
    projected = dict(_bounded_value(result))
    projected["_tool_payload_meta"] = {
        "tool_name": tool_name,
        "payload_policy": "generic_bounded_observation",
        "compacted": projected != dict(result),
        "source_scope": "atomic_tool_result",
    }
    return projected


def _maybe_attach_search_fallback(
    _tool_name: str,
    _arguments: dict[str, Any],
    result: Any,
) -> Any:
    """Compatibility hook that deliberately performs no hidden fallback.

    Tool failures must remain visible observations. The graph's reflection
    node may discover another source and create a new explicit action.
    """
    return result


__all__ = [
    "_compact_tool_result",
    "_format_result",
    "_maybe_attach_search_fallback",
]
