"""Company announcement evidence with explicit source provenance."""

from __future__ import annotations
import re
from datetime import datetime, timedelta
from typing import Any
import pandas as pd
from market_data_service.providers.common import (
    bare_local_symbol,
    cached_call,
    frame_records,
)


def _empty_notice_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["代码", "名称", "公告标题", "公告类型", "公告日期", "网址"]
    )


def _fetch_akshare(code: str, begin_date: str, end_date: str) -> pd.DataFrame:
    import akshare as ak

    try:
        return ak.stock_individual_notice_report(
            security=code, symbol="全部", begin_date=begin_date, end_date=end_date
        )
    except KeyError as exc:
        if str(exc).strip("'") == "代码":
            return _empty_notice_frame()
        raise


def _normalize_item(row: dict[str, Any], code: str) -> dict[str, Any] | None:
    title = re.sub("\\s+", " ", str(row.get("公告标题") or "")).strip()
    if not title:
        return None
    source_type = str(row.get("公告类型") or "").strip()
    raw_date = row.get("公告日期")
    if hasattr(raw_date, "isoformat"):
        publish_date = raw_date.isoformat()
    else:
        publish_date = str(raw_date or "").strip()[:10] or None
    return {
        "symbol": str(row.get("代码") or code),
        "name": str(row.get("名称") or "").strip() or None,
        "title": title,
        "notice_type": source_type or None,
        "source_notice_type": source_type or None,
        "publish_date": publish_date,
        "url": str(row.get("网址") or "").strip(),
        "source": "RSSHub/交易所官方披露"
        if row.get("_rss_route")
        else "AKShare/东方财富公司公告",
        "source_type": "announcement",
        "semantic_status": "model_required",
    }


def read_company_announcements_akshare(
    symbol: str, days: int = 30, limit: int = 30, *, use_cache: bool = True
) -> dict[str, Any]:
    """Read one formal-announcement source without an internal fallback."""
    code = bare_local_symbol(symbol)
    if not re.fullmatch("\\d{6}", code):
        raise ValueError(f"无法识别 A 股代码: {symbol}")
    days = int(days)
    limit = int(limit)
    if not 1 <= days <= 730:
        raise ValueError("days 必须在 1 到 730 之间")
    if not 1 <= limit <= 100:
        raise ValueError("limit 必须在 1 到 100 之间")
    now = datetime.now().astimezone()
    begin_date = (now - timedelta(days=days)).date().isoformat()
    end_date = now.date().isoformat()
    frame, cached = (
        cached_call(
            f"announcements:akshare:v1:{code}:{begin_date}:{end_date}",
            lambda: _fetch_akshare(code, begin_date, end_date),
            ttl_seconds=86400,
            attempts=1,
        )
        if use_cache
        else (_fetch_akshare(code, begin_date, end_date), False)
    )
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for row in frame_records(frame):
        item = _normalize_item(row, code)
        if item is None:
            continue
        key = re.sub(
            "\\s+", "", f"{item['title']}|{item.get('publish_date') or ''}"
        ).lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
    items.sort(
        key=lambda item: (item.get("publish_date") or "", item.get("title") or ""),
        reverse=True,
    )
    items = items[:limit]
    latest = next(
        (item.get("publish_date") for item in items if item.get("publish_date")), None
    )
    return {
        "symbol": code,
        "days": days,
        "limit": limit,
        "items": items,
        "item_count": len(items),
        "has_announcements": bool(items),
        "coverage_start": begin_date,
        "coverage_end": end_date,
        "source": "AKShare/东方财富公司公告",
        "source_scope": "formal_company_announcements",
        "success": True,
        "partial": False,
        "errors": [],
        "warnings": [],
        "data_time": latest,
        "retrieved_at": now.isoformat(),
        "is_stale": False if latest else None,
        "freshness_unknown": latest is None,
        "_cached": cached,
    }
