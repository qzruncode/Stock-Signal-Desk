"""Individual-stock broker research reports from structured public data."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from src.tools._akshare import bare_symbol, cached_call, frame_records
from src.tools.base import ToolSpec, object_schema


_FORECAST_COLUMN = re.compile(r"^(\d{4})-盈利预测-(收益|市盈率)$")


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
    title = re.sub(r"\s+", " ", str(row.get("报告名称") or "")).strip()
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


def _fetch_rss_fallback(
    code: str,
    name: str | None,
    *,
    days: int,
    limit: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    from api.v1.endpoints._rss_reader import read_feed

    result = read_feed(
        route_path="/eastmoney/report/:category",
        params={"category": "stock"},
        limit=min(max(limit * 3, 20), 50),
    )
    cutoff = datetime.now() - timedelta(days=days)
    terms = [code, *([name] if name else [])]
    items: list[dict[str, Any]] = []
    for raw in result.get("items") or []:
        title = re.sub(r"\s+", " ", str(raw.get("title") or "")).strip()
        summary = re.sub(r"\s+", " ", str(raw.get("summary") or "")).strip()
        haystack = f"{title} {summary}"
        if not any(term and term in haystack for term in terms):
            continue
        if not _within_days(raw.get("published"), cutoff):
            continue
        items.append(
            {
                "symbol": code,
                "name": name,
                "title": title,
                "org": str(raw.get("author") or raw.get("source") or "").strip() or None,
                "rating": None,
                "industry": None,
                "publish_date": _date_text(raw.get("published")),
                "url": str(raw.get("link") or "").strip(),
                "summary": summary,
                "profit_forecasts": [],
                "monthly_report_count": None,
                "source": "RSSHub/东方财富个股研报",
                "source_type": "rss_research_report",
            }
        )
    return items[:limit], [str(error) for error in result.get("errors") or []]


def get_research_report(
    symbol: str,
    days: int = 365,
    limit: int = 20,
    use_cache: bool = True,
) -> dict[str, Any]:
    code = bare_symbol(symbol)
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError(f"无法识别 A 股代码: {symbol}")
    days = int(days)
    limit = int(limit)
    if not 1 <= days <= 1825:
        raise ValueError("days 必须在 1 到 1825 之间")
    if not 1 <= limit <= 100:
        raise ValueError("limit 必须在 1 到 100 之间")

    now = datetime.now().astimezone()
    cutoff = datetime.now() - timedelta(days=days)
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []
    primary_available = False
    cached = False
    try:
        frame, cached = cached_call(
            f"research-report:{code}",
            lambda: _fetch_akshare(code),
            ttl_seconds=86400 if use_cache else 0,
            attempts=1,
        )
        primary_available = True
        rows = frame_records(frame)
    except Exception as exc:
        errors.append(f"AKShare/东方财富个股研报: {exc}")

    name = next((str(row.get("股票简称")) for row in rows if row.get("股票简称")), None)
    if not name:
        try:
            from src.data.stock_index_loader import get_index_stock_name

            name = get_index_stock_name(code)
        except Exception:
            name = None

    items: list[dict[str, Any]] = []
    for row in rows:
        if not _within_days(row.get("日期"), cutoff):
            continue
        item = _normalize_direct(row, code)
        if item:
            items.append(item)

    fallback_attempted = not primary_available
    fallback_used = False
    fallback_available = False
    if fallback_attempted:
        rss_items, rss_errors = _fetch_rss_fallback(code, name, days=days, limit=limit)
        if rss_items:
            items = rss_items
            fallback_used = True
        fallback_available = bool(rss_items) or not rss_errors
        if rss_errors:
            warnings.extend(f"RSSHub {error}" for error in rss_errors)

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in sorted(items, key=lambda row: row.get("publish_date") or "", reverse=True):
        key = re.sub(
            r"\s+", "", f"{item.get('title', '')}|{item.get('publish_date', '')}|{item.get('org', '')}"
        ).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    items = deduped[:limit]

    acquisition_succeeded = primary_available or fallback_available
    if not items and acquisition_succeeded:
        warnings.append(f"最近 {days} 天没有检索到该股票的个股研报")
    rating_distribution: dict[str, int] = {}
    institution_distribution: dict[str, int] = {}
    forecast_years: set[int] = set()
    for item in items:
        rating = str(item.get("rating") or "未评级")
        org = str(item.get("org") or "未知机构")
        rating_distribution[rating] = rating_distribution.get(rating, 0) + 1
        institution_distribution[org] = institution_distribution.get(org, 0) + 1
        forecast_years.update(
            int(forecast["year"]) for forecast in item.get("profit_forecasts") or [] if forecast.get("year") is not None
        )
    latest = next((item.get("publish_date") for item in items if item.get("publish_date")), None)
    if primary_available:
        source = "AKShare/东方财富个股研报"
        source_chain = [source]
    elif fallback_available:
        source = "RSSHub/东方财富个股研报"
        source_chain = [source]
    else:
        source = "none"
        source_chain = []
    return {
        "symbol": code,
        "name": name,
        "days": days,
        "limit": limit,
        "items": items,
        "item_count": len(items),
        "has_reports": bool(items),
        "analysis": {
            "latest_report_date": latest,
            "rating_distribution": dict(sorted(rating_distribution.items(), key=lambda pair: (-pair[1], pair[0]))),
            "institution_distribution": dict(
                sorted(institution_distribution.items(), key=lambda pair: (-pair[1], pair[0]))
            ),
            "institution_count": len(institution_distribution),
            "forecast_years": sorted(forecast_years),
        },
        "source": source,
        "source_chain": source_chain,
        "source_scope": "broker_individual_stock_research_reports",
        "success": acquisition_succeeded,
        "partial": acquisition_succeeded and bool(errors),
        "data_time": latest,
        "retrieved_at": now.isoformat(),
        "is_stale": False if latest else None,
        "freshness_unknown": latest is None,
        "fallback_attempted": fallback_attempted,
        "fallback_used": fallback_used,
        "fallback_recommended": not acquisition_succeeded,
        "errors": list(dict.fromkeys(errors))[:10],
        "warnings": list(dict.fromkeys(warnings))[:10],
        "_cached": cached,
    }


TOOL = ToolSpec(
    name="get_research_report",
    description=(
        "仅用于查询单只 A 股的券商个股研报；输入必须能定位到具体股票。返回报告日期、机构、评级、"
        "PDF 链接及预测 EPS（元/股）和 PE（倍）。不要用于公司新闻、公告、已实现财务数据、行业、"
        "宏观、期货或评级研究；其他研究材料应使用 websearch，或通过 RSS 原子工具发现并读取来源。"
    ),
    parameters=object_schema(
        {
            "symbol": {"type": "string", "description": "A 股代码或名称"},
            "days": {"type": "integer", "minimum": 1, "maximum": 1825, "default": 365},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
        },
        ["symbol"],
    ),
    executor=get_research_report,
    category="research",
)
