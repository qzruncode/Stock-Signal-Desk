# -*- coding: utf-8 -*-
"""Agent data health assessment — staleness checks and fallback decisions."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _comparison_datetime(value: datetime) -> datetime:
    """Normalize mixed ISO timestamps for safe ordering.

    Tool payloads legitimately contain both local timestamps without an
    offset (AKShare/pages) and offset-aware timestamps (RSSHub/ISO).  Treat a
    naive timestamp as local time and convert aware values to the same local
    zone before comparing them.
    """
    local_tz = datetime.now().astimezone().tzinfo
    if value.tzinfo is None:
        return value.replace(tzinfo=local_tz)
    return value.astimezone(local_tz)


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    for candidate in (text, text[:10]):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    for candidate, fmt in ((text[:10], "%Y-%m-%d"), (text[:8], "%Y%m%d")):
        try:
            return datetime.strptime(candidate, fmt)
        except ValueError:
            continue
    return None


def _latest_date_from_items(items: Any, keys: List[str]) -> datetime | None:
    if not isinstance(items, list):
        return None
    latest: datetime | None = None
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in keys:
            dt = _parse_iso_datetime(item.get(key))
            if dt and (
                latest is None
                or _comparison_datetime(dt) > _comparison_datetime(latest)
            ):
                latest = dt
    return latest


def _assess_tool_data_health(tool_name: str, result: Any) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {"should_fallback": False, "reason": None}

    if result.get("error"):
        return {"should_fallback": True, "reason": "tool_error"}

    if result.get("is_stale") is True:
        return {
            "should_fallback": True,
            "reason": f"stale_{tool_name}",
            "latest_date": result.get("data_time"),
        }

    if tool_name == "get_realtime_quotes":
        items = result.get("items") or []
        if not items:
            return {"should_fallback": True, "reason": "empty_quotes"}
        return {"should_fallback": False, "reason": None}

    if tool_name in {"get_kline", "get_history_data"}:
        series = result.get("recent") or result.get("data") or []
        if not series:
            return {"should_fallback": True, "reason": "empty_kline"}
        if result.get("data_time"):
            latest = _parse_iso_datetime(result.get("data_time"))
            if latest and latest.date() < (datetime.now().date() - timedelta(days=7)):
                return {"should_fallback": True, "reason": "stale_kline", "latest_date": latest.date().isoformat()}
        latest = _latest_date_from_items(series, ["date"])
        if latest and latest.date() < (datetime.now().date() - timedelta(days=7)):
            return {"should_fallback": True, "reason": "stale_kline", "latest_date": latest.date().isoformat()}
        return {"should_fallback": False, "reason": None}

    if tool_name in {"search_news", "get_announcements", "get_risk_events", "get_research_report", "get_social_sentiment"}:
        items = result.get("items") or []
        if not items:
            return {"should_fallback": True, "reason": "empty_news_family"}
        if result.get("data_time"):
            latest = _parse_iso_datetime(result.get("data_time"))
            days = int(result.get("days") or 30)
            cutoff = datetime.now().astimezone() - timedelta(days=max(days, 1))
            if latest and _comparison_datetime(latest) < cutoff:
                return {"should_fallback": True, "reason": "stale_news_family", "latest_date": latest.date().isoformat()}
        latest = _latest_date_from_items(items, ["publish_time", "publish_date", "date_str"])
        days = int(result.get("days") or 30)
        cutoff = datetime.now().astimezone() - timedelta(days=max(days, 1))
        if latest and _comparison_datetime(latest) < cutoff:
            return {"should_fallback": True, "reason": "stale_news_family", "latest_date": latest.date().isoformat()}
        return {"should_fallback": False, "reason": None}

    return {"should_fallback": False, "reason": None}
