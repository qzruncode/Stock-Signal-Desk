# -*- coding: utf-8 -*-
"""Shared helpers for macro endpoint modules."""

from __future__ import annotations

import math
import threading
from datetime import datetime, timedelta
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


def is_fresh_today(records: list[dict]) -> bool:
    """Check if DB records contain today's data (already refreshed)."""
    if not records:
        return False
    today = datetime.now().strftime("%Y-%m-%d")
    return records[0].get("date") == today


def latest_series_date(records: list[dict], key: str = "date") -> str | None:
    if not records:
        return None
    return records[-1].get(key) or records[0].get(key)


def is_series_stale(records: list[dict], key: str, max_days: int = 7) -> bool:
    latest = latest_series_date(records, key)
    if not latest:
        return True
    try:
        latest_dt = datetime.fromisoformat(str(latest)[:10])
    except ValueError:
        return False
    return latest_dt < (datetime.now() - timedelta(days=max_days))