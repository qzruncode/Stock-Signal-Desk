from __future__ import annotations

from api.v1.endpoints.agent.tools import (
    MAX_COLLECTION_ITEMS,
    MAX_MAPPING_KEYS,
    MAX_NESTING_DEPTH,
    MAX_TEXT_CHARACTERS,
    _compact_tool_result,
    _format_result,
    _maybe_attach_search_fallback,
)


def test_format_result_preserves_unicode_json() -> None:
    output = _format_result({"value": "中文", "number": 1})
    assert "中文" in output
    assert '"number": 1' in output


def test_generic_projection_is_independent_of_tool_name() -> None:
    payload = {"success": True, "items": [{"value": 1}], "custom": {"x": 2}}
    first = _compact_tool_result("source_a", payload)
    second = _compact_tool_result("unseen_long_tail_tool", payload)

    assert {key: value for key, value in first.items() if key != "_tool_payload_meta"} == payload
    assert {key: value for key, value in second.items() if key != "_tool_payload_meta"} == payload
    assert first["_tool_payload_meta"]["payload_policy"] == "generic_bounded_observation"
    assert second["_tool_payload_meta"]["source_scope"] == "atomic_tool_result"


def test_generic_projection_bounds_collections_mappings_text_and_depth() -> None:
    payload = {
        "items": list(range(MAX_COLLECTION_ITEMS + 7)),
        "mapping": {f"key_{index}": index for index in range(MAX_MAPPING_KEYS + 3)},
        "text": "x" * (MAX_TEXT_CHARACTERS + 5),
        "nested": {},
    }
    cursor = payload["nested"]
    for _ in range(MAX_NESTING_DEPTH + 2):
        cursor["next"] = {}
        cursor = cursor["next"]

    projected = _compact_tool_result("any_tool", payload)
    assert projected["items"][-1]["_omitted_item_count"] == 7
    assert projected["mapping"]["_omitted_key_count"] == 3
    assert projected["text"].endswith("…")
    assert projected["_tool_payload_meta"]["compacted"] is True


def test_non_mapping_result_uses_the_same_bounded_policy() -> None:
    projected = _compact_tool_result("any_tool", list(range(MAX_COLLECTION_ITEMS + 1)))
    assert projected[-1]["_omitted_item_count"] == 1


def test_compatibility_fallback_hook_never_hides_failure_or_calls_another_source() -> None:
    failure = {
        "success": False,
        "errors": ["upstream unavailable"],
        "error_code": "provider_unavailable",
    }
    assert _maybe_attach_search_fallback("source_a", {"query": "x"}, failure) is failure
    assert "search_fallback" not in failure
