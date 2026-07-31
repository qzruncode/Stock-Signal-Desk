"""Shared normalization helpers for Stock Agent macro tools."""

from __future__ import annotations

import math
import re
from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Any


def number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip().replace(",", "").replace("%", "")
        if not value or value.lower() in {"none", "nan", "nat", "-", "false"}:
            return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(result) or math.isinf(result) else result


_CN_QUARTER = {"一": 1, "二": 2, "三": 3, "四": 4}


def period_date(value: Any) -> date | None:
    """Normalize AKShare's month, quarter and ISO period labels."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", text)
    if match:
        year, month = map(int, match.groups())
        return date(year, month, monthrange(year, month)[1]) if 1 <= month <= 12 else None
    match = re.search(
        r"(\d{4}).*?(?:第\s*)?([一二三四1-4])(?:\s*[-~至]\s*([一二三四1-4]))?\s*季度",
        text,
    ) or re.search(r"(\d{4})\s*[Qq]\s*([1-4])", text)
    if match:
        year = int(match.group(1))
        raw = (match.group(3) if len(match.groups()) >= 3 else None) or match.group(2)
        quarter = _CN_QUARTER.get(raw, int(raw) if raw.isdigit() else 0)
        if 1 <= quarter <= 4:
            month = quarter * 3
            return date(year, month, monthrange(year, month)[1])
    normalized = text.replace("/", "-").replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        pass
    for fmt in ("%Y%m%d", "%Y%m", "%Y-%m"):
        try:
            parsed = datetime.strptime(text, fmt).date()
            return (
                date(parsed.year, parsed.month, monthrange(parsed.year, parsed.month)[1]) if fmt != "%Y%m%d" else parsed
            )
        except ValueError:
            continue
    return None


def ordered(records: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return sorted(
        records or [],
        key=lambda row: (period_date(row.get(key)) is not None, period_date(row.get(key)) or date.min),
    )


def latest_date(records: list[dict[str, Any]], key: str) -> date | None:
    values = [period_date(row.get(key)) for row in records or []]
    return max((value for value in values if value is not None), default=None)


def get_db():
    from src.storage import DatabaseManager

    return DatabaseManager.get_instance()


def prior_month_end(today: date) -> date:
    return today.replace(day=1) - timedelta(days=1)


def _months_back_end(today: date, months: int) -> date:
    cursor = today.replace(day=1)
    for _ in range(max(0, months - 1)):
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    return cursor - timedelta(days=1)


def expected_indicator_period(indicator: str, today: date | None = None) -> date:
    """Expected latest observation under normal mainland release calendars."""
    today = today or datetime.now().date()
    if indicator == "PMI":
        # NBS PMI is normally published on the final calendar day of the
        # observation month. Before that, the prior month is still current.
        if today.day == monthrange(today.year, today.month)[1]:
            return date(today.year, today.month, today.day)
        return prior_month_end(today)
    if indicator in {"CPI", "PPI"}:
        # These releases are usually around the 9th. Use the 10th as a
        # conservative availability boundary to avoid false stale warnings.
        return prior_month_end(today) if today.day >= 10 else _months_back_end(today, 2)
    if indicator in {"M2", "社融"}:
        # PBOC monthly aggregates commonly arrive in the middle of the next
        # month and can be delayed. Do not expect them before the 15th.
        return prior_month_end(today) if today.day >= 15 else _months_back_end(today, 2)
    if indicator == "LPR":
        if today.day >= 20:
            return date(today.year, today.month, monthrange(today.year, today.month)[1])
        return prior_month_end(today)

    # GDP is released around the middle of Jan/Apr/Jul/Oct. A conservative
    # 20th-day gate prevents Q2/Q3/Q4 from being declared missing before the
    # official release. Determine the most recent release event, then map it
    # to the quarter that event publishes.
    release_months = (1, 4, 7, 10)
    eligible = [month for month in release_months if month < today.month or (month == today.month and today.day >= 20)]
    if eligible:
        release_month = max(eligible)
        release_year = today.year
    else:
        release_month = 10
        release_year = today.year - 1
    if release_month == 1:
        year, quarter = release_year - 1, 4
    elif release_month == 4:
        year, quarter = release_year, 1
    elif release_month == 7:
        year, quarter = release_year, 2
    else:
        year, quarter = release_year, 3
    month = quarter * 3
    return date(year, month, monthrange(year, month)[1])


__all__ = [
    "expected_indicator_period",
    "get_db",
    "latest_date",
    "number",
    "ordered",
    "period_date",
]
