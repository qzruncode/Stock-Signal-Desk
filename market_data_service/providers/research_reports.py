"""Individual-stock broker research reports from structured public data."""

from __future__ import annotations
import re
from datetime import datetime, timedelta
from typing import Any
from market_data_service.providers.common import (
    bare_local_symbol,
    cached_call,
    frame_records,
)

_FORECAST_COLUMN = re.compile("^(\\d{4})-盈利预测-(收益|市盈率)$")


def _fetch_akshare(code: str):
    import akshare as ak

    return ak.stock_research_report_em(symbol=code)


def _date_text(value: Any) -> str | None:
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    text = str(value or "").strip()
    return text[:10] or None


def _within_days(value: Any, cutoff: datetime) -> bool:
    text = _date_text(value)
    if not text:
        return True
    try:
        return datetime.fromisoformat(text) >= cutoff
    except ValueError:
        return True


def _forecasts(row: dict[str, Any]) -> list[dict[str, Any]]:
    by_year: dict[int, dict[str, Any]] = {}
    for key, value in row.items():
        match = _FORECAST_COLUMN.fullmatch(str(key))
        if not match or value is None:
            continue
        year = int(match.group(1))
        record = by_year.setdefault(year, {"year": year, "eps": None, "pe": None})
        if match.group(2) == "收益":
            record["eps"] = value
        else:
            record["pe"] = value
    return [
        {**record, "eps_unit": "元/股", "pe_unit": "倍"}
        for year, record in sorted(by_year.items())
        if record.get("eps") is not None or record.get("pe") is not None
    ]


def _normalize_direct(row: dict[str, Any], code: str) -> dict[str, Any] | None:
    title = re.sub("\\s+", " ", str(row.get("报告名称") or "")).strip()
    if not title:
        return None
    return {
        "symbol": str(row.get("股票代码") or code),
        "name": str(row.get("股票简称") or "").strip() or None,
        "title": title,
        "org": str(row.get("机构") or "").strip() or None,
        "rating": str(row.get("东财评级") or "").strip() or None,
        "industry": str(row.get("行业") or "").strip() or None,
        "publish_date": _date_text(row.get("日期")),
        "url": str(row.get("报告PDF链接") or "").strip(),
        "profit_forecasts": _forecasts(row),
        "monthly_report_count": row.get("近一月个股研报数"),
        "source": "AKShare/东方财富个股研报",
        "source_type": "structured_research_report",
    }


def read_company_research_reports_akshare(
    symbol: str, days: int = 365, limit: int = 20, *, use_cache: bool = True
) -> dict[str, Any]:
    """Read one structured research-report source without RSS substitution."""
    code = bare_local_symbol(symbol)
    if not re.fullmatch("\\d{6}", code):
        raise ValueError(f"无法识别 A 股代码: {symbol}")
    days = int(days)
    limit = int(limit)
    if not 1 <= days <= 1825:
        raise ValueError("days 必须在 1 到 1825 之间")
    if not 1 <= limit <= 100:
        raise ValueError("limit 必须在 1 到 100 之间")
    now = datetime.now().astimezone()
    cutoff = now.replace(tzinfo=None) - timedelta(days=days)
    frame, cached = (
        cached_call(
            f"research-report:akshare:v1:{code}",
            lambda: _fetch_akshare(code),
            ttl_seconds=86400,
            attempts=1,
        )
        if use_cache
        else (_fetch_akshare(code), False)
    )
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in frame_records(frame):
        if not _within_days(row.get("日期"), cutoff):
            continue
        item = _normalize_direct(row, code)
        if item is None:
            continue
        key = re.sub(
            "\\s+",
            "",
            f"{item.get('title', '')}|{item.get('publish_date', '')}|{item.get('org', '')}",
        ).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        items.append(item)
    items.sort(key=lambda row: row.get("publish_date") or "", reverse=True)
    items = items[:limit]
    latest = next(
        (item.get("publish_date") for item in items if item.get("publish_date")), None
    )
    reference_links = [
        str(item.get("url") or "").strip()
        for item in items
        if str(item.get("url") or "").strip()
    ]
    return {
        "symbol": code,
        "days": days,
        "limit": limit,
        "items": items,
        "item_count": len(items),
        "has_reports": bool(items),
        "source": "AKShare/东方财富个股研报",
        "source_scope": "broker_individual_stock_research_reports",
        "content_access": {
            "mode": "reference_only",
            "content_read": False,
            "content_extracted": False,
            "content_read_required": bool(reference_links),
        },
        "reference_links": reference_links,
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
