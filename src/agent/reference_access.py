"""Provider-independent reference-to-body access status helpers.

This module intentionally has no dependency on the LangGraph runtime or the
tool registry.  Both the live content-access gate and the persisted behaviour
audit use it so their per-source-action semantics cannot drift.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence, Set
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def canonical_url(value: Any) -> str:
    """Normalize a public URL for matching a reference to a body read."""
    text = str(value or "").strip()
    if not text.lower().startswith(("http://", "https://")):
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return text
    if not parts.netloc:
        return text
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/") or "/",
            parts.query,
            "",
        )
    )


def _text(value: Any, limit: int = 120) -> str:
    return str(value or "").strip()[:limit]


def _target_action_ids(target: Mapping[str, Any]) -> set[str]:
    action_ids = {_text(target.get("action_id"))}
    raw_action_ids = target.get("action_ids")
    if isinstance(raw_action_ids, Sequence) and not isinstance(
        raw_action_ids, (str, bytes, bytearray)
    ):
        action_ids.update(_text(value) for value in raw_action_ids)
    return {action_id for action_id in action_ids if action_id}


def _normalized_url_set(
    values: Any,
    *,
    url_normalizer: Callable[[Any], str],
) -> set[str]:
    if isinstance(values, (str, bytes, bytearray)):
        values = [values]
    if not isinstance(values, (Sequence, Set)):
        return set()
    return {
        normalized
        for normalized in (url_normalizer(value) for value in values)
        if normalized
    }


def reference_access_status(
    *,
    cited_action_ids: Sequence[str] | set[str],
    targets: Sequence[Mapping[str, Any]],
    selected_urls: Any,
    successful_urls: Any,
    url_normalizer: Callable[[Any], str] = canonical_url,
) -> dict[str, dict[str, Any]]:
    """Return body-access status independently for each cited source action.

    The same URL may be present in more than one source action, so the status
    is calculated from the action associations on each target rather than from
    a run-wide successful-read count.  A multi-link action requires a model
    selection but only selected candidates become mandatory body reads.
    """
    cited_actions = {
        _text(action_id)
        for action_id in cited_action_ids
        if _text(action_id)
    }
    if not cited_actions:
        return {}

    selected = _normalized_url_set(selected_urls, url_normalizer=url_normalizer)
    successful = _normalized_url_set(successful_urls, url_normalizer=url_normalizer)
    targets_by_action: dict[str, list[dict[str, Any]]] = {
        action_id: [] for action_id in sorted(cited_actions)
    }
    seen_by_action: dict[str, set[str]] = {
        action_id: set() for action_id in targets_by_action
    }
    for raw_target in targets:
        if not isinstance(raw_target, Mapping):
            continue
        target_url = url_normalizer(raw_target.get("url"))
        if not target_url:
            continue
        target_actions = _target_action_ids(raw_target) & cited_actions
        for action_id in target_actions:
            if target_url in seen_by_action[action_id]:
                continue
            seen_by_action[action_id].add(target_url)
            targets_by_action[action_id].append(dict(raw_target))

    statuses: dict[str, dict[str, Any]] = {}
    for action_id, action_targets in targets_by_action.items():
        selected_targets = [
            target
            for target in action_targets
            if url_normalizer(target.get("url")) in selected
        ]
        successful_targets = [
            target
            for target in action_targets
            if url_normalizer(target.get("url")) in successful
        ]
        required_targets = action_targets if len(action_targets) == 1 else selected_targets
        pending_targets = [
            target
            for target in required_targets
            if url_normalizer(target.get("url")) not in successful
        ]
        selection_required = len(action_targets) > 1 and not selected_targets
        statuses[action_id] = {
            "action_id": action_id,
            "candidate_count": len(action_targets),
            "candidate_targets": action_targets,
            "selected_targets": selected_targets,
            "successful_targets": successful_targets,
            "required_targets": required_targets,
            "pending_targets": pending_targets,
            "unselected_targets": [
                target
                for target in action_targets
                if url_normalizer(target.get("url")) not in selected
            ],
            "selection_required": selection_required,
            "satisfied": not selection_required and not pending_targets,
        }
    return statuses


__all__ = ["canonical_url", "reference_access_status"]
