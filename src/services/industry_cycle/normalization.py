# -*- coding: utf-8 -*-
"""Symbol normalization, type coercion and provider-text helpers."""

from __future__ import annotations

import re
from typing import Any, Optional

def _normalize_symbol(symbol: str) -> str:
    code = str(symbol or "").strip().upper()
    if "." in code:
        code = code.split(".", 1)[0]
    for prefix in ("SH", "SZ", "BJ"):
        if code.startswith(prefix):
            code = code[2:]
    return code


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _prune_none(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            normalized = _prune_none(item)
            if normalized is None:
                continue
            if isinstance(normalized, dict) and not normalized:
                continue
            cleaned[key] = normalized
        return cleaned
    if isinstance(value, list):
        cleaned_list: list[Any] = []
        for item in value:
            normalized = _prune_none(item)
            if normalized is None:
                continue
            if isinstance(normalized, dict) and not normalized:
                continue
            cleaned_list.append(normalized)
        return cleaned_list
    return value


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in _as_list(value) if isinstance(item, dict)]


def _first_dict(value: Any) -> dict[str, Any]:
    for item in _as_list(value):
        if isinstance(item, dict):
            return item
    return {}


def _compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", _normalize_text(value)).lower()


def _summarize_text_items(
    items: list[dict[str, Any]],
    *,
    title_key: str = "title",
    summary_key: str = "summary",
    limit: int = 6,
    summary_limit: int = 90,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in _list_of_dicts(items)[:limit]:
        title = _normalize_text(item.get(title_key))
        summary = _normalize_text(item.get(summary_key))
        row: dict[str, str] = {}
        if title:
            row["title"] = title
        if summary:
            row["summary"] = summary[:summary_limit]
        if row:
            rows.append(row)
    return rows
