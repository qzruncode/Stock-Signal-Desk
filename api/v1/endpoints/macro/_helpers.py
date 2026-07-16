# -*- coding: utf-8 -*-
"""Shared helpers for macro endpoint modules."""

from __future__ import annotations

import math
import re
import threading
from datetime import date, datetime, timedelta
from typing import Optional


def safe_float(val) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, str):
        val = val.strip().replace(",", "").replace("%", "")
        if not val or val.lower() in ("false", "none", "nan", "-"):
            return None
    try:
        v = float(val)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except (ValueError, TypeError):
        return None


def yiyuan_to_yuan(val) -> Optional[float]:
    """Convert 亿元 (hundred-million yuan) to yuan."""
    v = safe_float(val)
    if v is None:
        return None
    return v * 1e8


def get_db():
    from src.storage import DatabaseManager
    return DatabaseManager.get_instance()


_CHINESE_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4}


def parse_series_date(value) -> date | None:
    """Parse the heterogeneous period formats returned by AKShare macro APIs."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "nan", "nat"}:
        return None

    month_match = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", text)
    if month_match:
        year, month = map(int, month_match.groups())
        if 1 <= month <= 12:
            return date(year, month, 1)

    quarter_match = re.search(
        r"(\d{4}).*?(?:第\s*)?([一二三四1-4])(?:\s*[-~至]\s*([一二三四1-4]))?\s*季度",
        text,
    )
    if not quarter_match:
        quarter_match = re.search(r"(\d{4})\s*[Qq]\s*([1-4])", text)
    if quarter_match:
        year = int(quarter_match.group(1))
        raw_quarter = quarter_match.group(3) or quarter_match.group(2)
        quarter = _CHINESE_DIGITS.get(raw_quarter, int(raw_quarter) if raw_quarter.isdigit() else 0)
        if 1 <= quarter <= 4:
            return date(year, quarter * 3, 1)

    normalized = text.replace("/", "-")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        pass
    for fmt in ("%Y%m%d", "%Y-%m", "%Y%m"):
        try:
            return datetime.strptime(normalized, fmt).date()
        except ValueError:
            continue
    return None


def sort_series_records(records: list[dict], key: str) -> list[dict]:
    """Return oldest-to-newest records without mutating the storage result."""
    return sorted(
        records or [],
        key=lambda record: (
            parse_series_date(record.get(key)) is not None,
            parse_series_date(record.get(key)) or date.min,
        ),
    )


def is_fresh_today(records: list[dict], key: str = "date") -> bool:
    """Check if DB records contain today's data (already refreshed)."""
    latest = latest_series_parsed_date(records, key)
    return latest == datetime.now().date()


def latest_series_parsed_date(records: list[dict], key: str = "date") -> date | None:
    parsed = [parse_series_date(record.get(key)) for record in records or []]
    valid = [value for value in parsed if value is not None]
    return max(valid) if valid else None


def latest_series_date(records: list[dict], key: str = "date") -> str | None:
    ordered = sort_series_records(records, key)
    return ordered[-1].get(key) if ordered else None


def is_series_stale(records: list[dict], key: str, max_days: int = 7) -> bool:
    latest = latest_series_parsed_date(records, key)
    if latest is None:
        return True
    return latest < (datetime.now().date() - timedelta(days=max_days))
