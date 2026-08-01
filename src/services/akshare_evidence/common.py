"""Common retrieval and normalization helpers for AKShare evidence."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable, Iterable

import pandas as pd

from src.tools._akshare import cached_call, frame_records

_SYMBOL_COLUMNS = (
    "股票代码",
    "证券代码",
    "代码",
    "stock_code",
    "symbol",
    "SECURITY_CODE",
)
_DATE_COLUMNS = (
    "date",
    "公告日期",
    "公告日",
    "上榜日期",
    "持股日期",
    "解禁时间",
    "交易日",
    "交易日期",
    "日期",
    "报告期",
    "截止时间",
    "调研日期",
    "接待日期",
    "提问时间",
    "问题时间",
    "回答时间",
    "更新时间",
    "变动日期",
    "变动截止日",
    "数据日期",
    "最新商誉报告期",
    "首次预约",
    "实际披露",
)


def compact_symbol(value: Any) -> str:
    text = str(value or "").strip().upper()
    for prefix in ("SH", "SZ", "BJ"):
        if text.startswith(prefix):
            text = text[2:]
    if "." in text:
        head, tail = text.split(".", 1)
        text = head if head.isdigit() else tail
    digits = "".join(character for character in text if character.isdigit())
    return digits[-6:].zfill(6) if digits else text


def row_matches_symbol(row: dict[str, Any], symbol: str) -> bool:
    code = compact_symbol(symbol)
    for column in _SYMBOL_COLUMNS:
        if column in row and compact_symbol(row.get(column)) == code:
            return True
    return False


def filter_symbol(items: Iterable[dict[str, Any]], symbol: str) -> list[dict[str, Any]]:
    return [item for item in items if row_matches_symbol(item, symbol)]


def fetch_frame(
    api_name: str,
    cache_key: str,
    factory: Callable[[], pd.DataFrame],
    *,
    ttl_seconds: int,
    symbol: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Fetch one frame and expose exact source coverage without semantic labels."""
    try:
        frame, cached = cached_call(cache_key, factory, ttl_seconds=ttl_seconds)
        if not isinstance(frame, pd.DataFrame):
            raise TypeError(f"{api_name} returned {type(frame).__name__}, expected DataFrame")
        items = frame_records(frame)
        source_row_count = len(items)
        if symbol is not None:
            items = filter_symbol(items, symbol)
        if limit is not None:
            items = items[: max(0, int(limit))]
        return {
            "success": True,
            "partial": False,
            "source_api": f"AKShare.{api_name}",
            "items": items,
            "item_count": len(items),
            "source_row_count": source_row_count,
            "cached": cached,
            "error": None,
        }
    except Exception as exc:
        return {
            "success": False,
            "partial": False,
            "source_api": f"AKShare.{api_name}",
            "items": [],
            "item_count": 0,
            "source_row_count": None,
            "cached": False,
            "error": f"{type(exc).__name__}: {str(exc)[:400]}",
        }


def recent_report_periods(*, count: int = 4, as_of: date | None = None, include_next: bool = False) -> list[str]:
    """Return quarter-end report periods as compact YYYYMMDD strings."""
    today = as_of or datetime.now().astimezone().date()
    candidates: list[date] = []
    for year in range(today.year - 3, today.year + 2):
        candidates.extend(date(year, month, day) for month, day in ((3, 31), (6, 30), (9, 30), (12, 31)))
    candidates.sort(reverse=True)
    if include_next:
        future = sorted(candidate for candidate in candidates if candidate > today)
        past = [candidate for candidate in candidates if candidate <= today]
        ordered = future[:1] + past
    else:
        ordered = [candidate for candidate in candidates if candidate <= today]
    return [candidate.strftime("%Y%m%d") for candidate in ordered[: max(1, count)]]


def disclosure_period_label(period: str) -> str:
    suffix = {
        "0331": "一季",
        "0630": "半年报",
        "0930": "三季",
        "1231": "年报",
    }.get(period[4:])
    if suffix is None:
        raise ValueError(f"unsupported report period: {period}")
    return f"{period[:4]}{suffix}"


def dataset_errors(datasets: dict[str, dict[str, Any]]) -> list[str]:
    return [
        f"{name}: {dataset.get('error')}"
        for name, dataset in datasets.items()
        if dataset.get("error")
    ]


def latest_data_time(datasets: dict[str, dict[str, Any]]) -> str | None:
    values: list[str] = []
    for dataset in datasets.values():
        for item in dataset.get("items") or []:
            if not isinstance(item, dict):
                continue
            for key in _DATE_COLUMNS:
                value = item.get(key)
                if value:
                    values.append(str(value)[:10])
    return max(values) if values else None


def section_envelope(
    name: str,
    symbol: str,
    datasets: dict[str, dict[str, Any]],
    *,
    units: dict[str, str] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    successful = [dataset for dataset in datasets.values() if dataset.get("success") is True]
    complete = [
        dataset
        for dataset in datasets.values()
        if dataset.get("success") is True and dataset.get("partial") is not True
    ]
    errors = dataset_errors(datasets)
    data_time = latest_data_time(datasets)
    return {
        "section": name,
        "symbol": compact_symbol(symbol),
        "datasets": datasets,
        "available_dataset_count": len(successful),
        "required_dataset_count": len(datasets),
        "coverage_complete": len(complete) == len(datasets),
        "success": bool(successful),
        "partial": bool(successful) and bool(errors),
        "errors": errors,
        "warnings": list(notes or []),
        "data_time": data_time,
        "is_stale": None if data_time is None else False,
        "freshness_unknown": data_time is None,
        "units": units or {},
    }
