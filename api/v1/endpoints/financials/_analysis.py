# -*- coding: utf-8 -*-
"""Financial content provenance, coverage and freshness analysis."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from api.v1.endpoints.financials._symbol import _safe_str, _parse_date

def _latest_content_time(items: list[dict], keys: list[str]) -> str | None:
    latest: datetime | None = None
    for item in items:
        for key in keys:
            raw = item.get(key)
            if not raw:
                continue
            parsed = _parse_date(raw)
            if parsed is not None and (latest is None or parsed > latest):
                latest = parsed
                break
    return latest.isoformat() if latest is not None else None


def _content_is_stale(items: list[dict], max_age_days: int, keys: list[str]) -> bool:
    latest = _latest_content_time(items, keys)
    if not latest:
        return True
    parsed = _parse_date(latest)
    if parsed is None:
        return True
    return parsed < (datetime.now() - timedelta(days=max_age_days))


def _resolve_post_publish_time(update_time: str, now: datetime | None = None) -> datetime | None:
    if now is None:
        now = datetime.now()
    date_match = re.match(r'(\d{2})-(\d{2})\s+(\d{2}):(\d{2})', update_time)
    if date_match:
        month, day, hour, minute = date_match.groups()
        try:
            pub_dt = datetime(now.year, int(month), int(day), int(hour), int(minute))
        except ValueError:
            return None
        if pub_dt - now > timedelta(hours=12):
            pub_dt = pub_dt.replace(year=now.year - 1)
        return pub_dt
    return _parse_date(update_time)
