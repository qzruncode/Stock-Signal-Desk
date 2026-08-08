"""Small, provenance-preserving projections for model context.

Raw tool results remain in the LangGraph checkpoint. These helpers only build
bounded copies for model calls so a verbose source cannot crowd out the goal,
answer, or evidence-reference requirements.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence


_TRANSPORT_METADATA_FIELDS = frozenset(
    {
        "_cached",
        "_fetched_at",
        "_stale",
        "completed_at",
        "observed_at",
        "retrieved_at",
        "retrieval_time",
    }
)


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str))


def _collection_priority(value: Any) -> int:
    """Rank values that carry answerable rows ahead of envelope metadata.

    Tool outputs commonly put status, cache and routing fields before the
    actual collection.  The projection must not mistake that source ordering
    for importance when it later reduces the number of keys.  This is purely
    structural: it knows nothing about a provider, a field name, or a domain.
    """
    if not isinstance(value, (list, tuple)) or not value:
        return 0
    if any(isinstance(item, Mapping) for item in value[:16]):
        return 2
    return 1


def _compact(
    value: Any,
    *,
    depth: int = 0,
    omit_transport_metadata: bool = False,
    collection_limit: int = 16,
    mapping_limit: int = 32,
    text_limit: int = 1_600,
) -> Any:
    if depth >= 6:
        return "[depth omitted]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= text_limit else value[:text_limit] + "…[truncated]"
    if isinstance(value, Mapping):
        items = list(value.items())
        visible_items = [
            (key, item)
            for key, item in items
            if not (
                omit_transport_metadata
                and str(key).strip().lower() in _TRANSPORT_METADATA_FIELDS
            )
        ]
        # Preserve source order within each tier.  Structured record
        # collections go first so a reduced mapping budget cannot retain only
        # envelope metadata and discard the answerable rows.
        ranked_items = sorted(
            enumerate(visible_items),
            key=lambda indexed: (-_collection_priority(indexed[1][1]), indexed[0]),
        )
        selected_items = [item for _, item in ranked_items[:mapping_limit]]
        compact = {
            str(key): _compact(
                item,
                depth=depth + 1,
                omit_transport_metadata=omit_transport_metadata,
                collection_limit=collection_limit,
                mapping_limit=mapping_limit,
                text_limit=text_limit,
            )
            for key, item in selected_items
        }
        if len(visible_items) > mapping_limit:
            compact["_omitted_key_count"] = len(visible_items) - mapping_limit
        return compact
    if isinstance(value, (list, tuple)):
        compact = [
            _compact(
                item,
                depth=depth + 1,
                omit_transport_metadata=omit_transport_metadata,
                collection_limit=collection_limit,
                mapping_limit=mapping_limit,
                text_limit=text_limit,
            )
            for item in value[:collection_limit]
        ]
        if len(value) > collection_limit:
            compact.append(
                {
                    "_omitted_item_count": len(value) - collection_limit,
                    "_original_item_count": len(value),
                }
            )
        return compact
    return str(value)[:text_limit]


def _contains_omitted_collection(value: Any) -> bool:
    """Whether a first-pass compact view dropped rows for presentation only."""
    if isinstance(value, Mapping):
        if "_omitted_item_count" in value:
            return True
        return any(_contains_omitted_collection(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_omitted_collection(item) for item in value)
    return False


def _projection_marker(
    *,
    result_omitted: bool,
    collection_rows_omitted: bool,
) -> dict[str, bool]:
    """Describe the model-context copy without making a source-data claim."""
    return {
        "context_compacted": True,
        "result_omitted": result_omitted,
        "collection_rows_omitted": collection_rows_omitted,
    }


def _fit_record(
    record: dict[str, Any],
    *,
    max_chars: int,
    result_source: Any,
) -> dict[str, Any]:
    """Fit one record without flattening structured source rows prematurely."""
    first_pass_dropped_rows = _contains_omitted_collection(record.get("result"))
    if _json_size(record) <= max_chars and not first_pass_dropped_rows:
        return record

    # Preserve record boundaries before falling back to a text preview.  An
    # answer request for a small collection must not look incomplete merely
    # because the evidence envelope exceeded its first compact budget.
    profiles = (
        # A collection can be too large because every row carries one verbose
        # text field, not because the requested rows themselves are too many.
        # Before dropping rows, make a dense, row-preserving view: keep more
        # records but shorten each field.  This is intentionally schema- and
        # domain-neutral, so a request for a table of news, prices, filings or
        # any other structured collection can still be answered from every
        # returned row without putting its full documents in model context.
        (48, 16, 240),
        (32, 12, 160),
        (24, 12, 120),
        (16, 10, 96),
        (8, 8, 80),
        (3, 6, 64),
        (1, 4, 48),
    )
    for collection_limit, mapping_limit, text_limit in profiles:
        compact = dict(record)
        compact["result"] = _compact(
            result_source,
            omit_transport_metadata=True,
            collection_limit=collection_limit,
            mapping_limit=mapping_limit,
            text_limit=text_limit,
        )
        compact["entities"] = _compact(
            record.get("entities") or {},
            collection_limit=collection_limit,
            mapping_limit=mapping_limit,
            text_limit=text_limit,
        )
        compact["source_refs"] = [
            str(item)[:text_limit]
            for item in list(record.get("source_refs") or [])[:collection_limit]
        ]
        compact["projection"] = _projection_marker(
            result_omitted=False,
            collection_rows_omitted=_contains_omitted_collection(compact["result"]),
        )
        if _json_size(compact) <= max_chars:
            return compact

    # The conventional first pass might fit only by omitting collection rows.
    # It is still a better fallback than losing the whole result when no dense
    # row-preserving projection fits the configured model-context budget.
    if _json_size(record) <= max_chars:
        return record

    compact = dict(record)
    compact["result"] = "[result omitted; raw value retained in checkpoint]"
    compact["projection"] = _projection_marker(
        result_omitted=True,
        collection_rows_omitted=True,
    )
    compact["entities"] = _compact(
        compact.get("entities"),
        collection_limit=4,
        mapping_limit=6,
        text_limit=240,
    )
    compact["source_refs"] = [
        str(item)[:500] for item in list(compact.get("source_refs") or [])[:8]
    ]
    if _json_size(compact) <= max_chars:
        return compact

    # Extremely large provenance fields are still represented, but bounded.
    compact["entities"] = str(compact.get("entities") or "")[:400]
    compact["source_refs"] = [
        str(item)[:240] for item in list(compact.get("source_refs") or [])[:4]
    ]
    return compact


def project_evidence_for_model(
    evidence: Sequence[Mapping[str, Any]],
    *,
    max_total_chars: int = 24_000,
    max_item_chars: int = 12_000,
) -> list[dict[str, Any]]:
    """Return a bounded evidence view while retaining every evidence ID."""
    successful = [item for item in evidence if item.get("success") is True]
    if not successful:
        return []

    total_limit = max(2_000, int(max_total_chars))
    item_limit = max(600, int(max_item_chars))
    fair_share = max(600, (total_limit - 2) // len(successful) - 2)
    effective_item_limit = min(item_limit, fair_share)
    projected: list[dict[str, Any]] = []

    for item in successful:
        record = {
            "evidence_id": str(item.get("evidence_id") or item.get("id") or ""),
            "action_id": str(item.get("action_id") or ""),
            "tool_name": str(item.get("tool_name") or ""),
            "success": True,
            "partial": bool(item.get("partial")),
            "entities": _compact(item.get("entities") or {}),
            # data_time is source-provided only. observed_at is deliberately
            # omitted so it cannot be restated as a data date in an answer.
            "data_time": item.get("data_time"),
            "data_time_provenance": str(
                item.get("data_time_provenance")
                or ("source" if item.get("data_time") else "unavailable")
            ),
            "data_time_note": item.get("data_time_note"),
            "is_stale": item.get("is_stale"),
            "freshness_unknown": item.get("freshness_unknown"),
            "source_refs": [
                str(ref)[:1_000] for ref in list(item.get("source_refs") or [])[:12]
            ],
            "result": _compact(
                item.get("result"),
                omit_transport_metadata=True,
            ),
        }
        projected.append(
            _fit_record(
                record,
                max_chars=effective_item_limit,
                # ``record["result"]`` is the safe first-pass view. Keep the
                # original only inside this fitting function so it can choose
                # a denser row-preserving representation before rows vanish.
                result_source=item.get("result"),
            )
        )

    # Account for JSON list punctuation and unusually large mandatory headers.
    for record in reversed(projected):
        if _json_size(projected) <= total_limit:
            break
        record["result"] = "[result omitted; raw value retained in checkpoint]"
        record["projection"] = _projection_marker(
            result_omitted=True,
            collection_rows_omitted=True,
        )
    if _json_size(projected) > total_limit:
        projected = [
            {
                "evidence_id": str(item.get("evidence_id") or "")[:96],
                "tool_name": str(item.get("tool_name") or "")[:80],
                "data_time": str(item.get("data_time") or "")[:80],
                "data_time_provenance": str(
                    item.get("data_time_provenance") or "unavailable"
                )[:24],
                "data_time_note": str(item.get("data_time_note") or "")[:240],
                "source_refs": [
                    str(ref)[:160] for ref in list(item.get("source_refs") or [])[:1]
                ],
                "projection": _projection_marker(
                    result_omitted=True,
                    collection_rows_omitted=True,
                ),
            }
            for item in projected
        ]
    if _json_size(projected) > total_limit:
        projected = [
            {
                "evidence_id": str(item.get("evidence_id") or "")[:96],
                "source_refs": [
                    str(ref)[:64] for ref in list(item.get("source_refs") or [])[:1]
                ],
                "projection": _projection_marker(
                    result_omitted=True,
                    collection_rows_omitted=True,
                ),
            }
            for item in projected
        ]
    if _json_size(projected) > total_limit:
        projected = [
            {
                "evidence_id": str(item.get("evidence_id") or "")[:96],
                "projection": _projection_marker(
                    result_omitted=True,
                    collection_rows_omitted=True,
                ),
            }
            for item in projected
        ]
    return projected


def project_tool_observations_for_model(
    results: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Keep execution status and failure details; successful values live in evidence."""
    observations: list[dict[str, Any]] = []
    for item in list(results)[:80]:
        nested = item.get("result") if isinstance(item.get("result"), Mapping) else {}
        observations.append(
            {
                "action_id": str(item.get("action_id") or item.get("id") or ""),
                "tool_name": str(item.get("tool_name") or ""),
                "success": item.get("success") is True,
                "partial": bool(item.get("partial")),
                "error_code": str(item.get("error_code") or nested.get("error_code") or ""),
                "errors": [str(error)[:800] for error in list(item.get("errors") or [])[:8]],
                "data_time": item.get("data_time"),
                "source_refs": [
                    str(ref)[:500] for ref in list(item.get("source_refs") or [])[:8]
                ],
            }
        )
    return observations


__all__ = ["project_evidence_for_model", "project_tool_observations_for_model"]
