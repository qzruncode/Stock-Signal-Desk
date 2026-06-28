# -*- coding: utf-8 -*-
"""Symbol normalization, type coercion, text helpers and keyword dictionaries."""

from __future__ import annotations

import re
from typing import Any, Optional

_DRIVER_KEYWORDS = {
    "policy": ("政策", "补贴", "专项", "规划", "意见", "指导", "支持"),
    "technology": ("技术", "创新", "迭代", "突破", "验证", "国产化", "替代", "升级"),
    "demand": ("需求", "订单", "招标", "景气", "扩容", "渗透率", "开工", "装机"),
    "supply": ("供给", "扩产", "产能", "出清", "涨价", "库存", "资本开支"),
}

_CATALYST_KEYWORDS = ("订单", "招标", "扩产", "量产", "政策", "补贴", "新品", "投产", "装机", "回购")
_PRICE_WAR_KEYWORDS = ("价格战", "内卷", "降价", "竞争加剧", "同质化", "低价抢单")
_THREE_YEAR_SPACE_KEYWORDS = ("三年", "3年", "空间", "成长", "渗透率提升", "国产替代", "长期")
_FADING_STAGE_KEYWORDS = ("退潮", "高位分歧", "主升后段", "降温")


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


def _contains_any(text: str, keywords: tuple[str, ...] | list[str]) -> bool:
    haystack = _compact_text(text)
    return any(_compact_text(word) in haystack for word in keywords if _compact_text(word))


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


def _extract_driver_clues(*texts: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {key: [] for key in _DRIVER_KEYWORDS}
    for text in texts:
        normalized = _normalize_text(text)
        if not normalized:
            continue
        for key, keywords in _DRIVER_KEYWORDS.items():
            hits = [kw for kw in keywords if _contains_any(normalized, [kw])]
            if hits:
                merged = "、".join(dict.fromkeys(hits))
                snippet = normalized[:72]
                result[key].append(f"{merged}: {snippet}")
    return {key: values[:6] for key, values in result.items() if values}


def _extract_competition_clues(*texts: str) -> list[str]:
    clues: list[str] = []
    for text in texts:
        normalized = _normalize_text(text)
        if not normalized:
            continue
        if _contains_any(normalized, _PRICE_WAR_KEYWORDS):
            clues.append(normalized[:90])
    return clues[:6]
