"""Company announcement evidence with explicit source provenance."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from src.tools._akshare import bare_symbol, cached_call, frame_records
from src.tools.base import ToolSpec, object_schema


def _empty_notice_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["代码", "名称", "公告标题", "公告类型", "公告日期", "网址"])


def _fetch_akshare(code: str, begin_date: str, end_date: str) -> pd.DataFrame:
    import akshare as ak

    try:
        return ak.stock_individual_notice_report(
            security=code,
            symbol="全部",
            begin_date=begin_date,
            end_date=end_date,
        )
    except KeyError as exc:
        # AKShare currently indexes the ``代码`` column even when Eastmoney
        # returns an empty page.  In a bounded date window this means "no
        # announcements", not a transport failure.
        if str(exc).strip("'") == "代码":
            return _empty_notice_frame()
        raise


def _rss_route_for(code: str) -> tuple[str, str] | None:
    if code.startswith(("6", "9")):
        return "/sse/disclosure/:query?", "productId"
    if code.startswith(("0", "2", "3")):
        return "/szse/disclosure/listed/notice/:query?", "stock"
    return None


def _fetch_exchange_rss(
    code: str,
    begin_date: str,
    end_date: str,
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], str | None, list[str]]:
    route = _rss_route_for(code)
    if route is None:
        return [], None, ["当前 Infos RSSHub 目录没有北交所公司公告路由"]

    from api.v1.endpoints._rss_reader import read_feed

    route_path, code_key = route
    query = f"{code_key}={code}&beginDate={begin_date}&endDate={end_date}"
    result = read_feed(
        route_path=route_path,
        params={"query": query},
        limit=min(max(limit, 20), 50),
    )
    records: list[dict[str, Any]] = []
    for item in result.get("items") or []:
        title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
        if not title:
            continue
        published = str(item.get("published") or "").strip()
        records.append(
            {
                "代码": code,
                "名称": None,
                "公告标题": title,
                "公告类型": "交易所公告",
                "公告日期": published[:10] or None,
                "网址": str(item.get("link") or "").strip(),
                "_rss_route": route_path,
            }
        )
    return records, route_path, [str(error) for error in result.get("errors") or []]


def _normalize_item(row: dict[str, Any], code: str) -> dict[str, Any] | None:
    title = re.sub(r"\s+", " ", str(row.get("公告标题") or "")).strip()
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
        "source": "RSSHub/交易所官方披露" if row.get("_rss_route") else "AKShare/东方财富公司公告",
        "source_type": "announcement",
        "semantic_status": "model_required",
    }


def get_announcements(
    symbol: str,
    days: int = 30,
    type: str = "all",
    limit: int = 30,
    use_cache: bool = True,
) -> dict[str, Any]:
    code = bare_symbol(symbol)
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError(f"无法识别 A 股代码: {symbol}")
    days = int(days)
    limit = int(limit)
    if not 1 <= days <= 730:
        raise ValueError("days 必须在 1 到 730 之间")
    if type != "all":
        raise ValueError("公告语义类型不再由程序词典筛选，请使用 type='all' 并由模型分析")
    if not 1 <= limit <= 100:
        raise ValueError("limit 必须在 1 到 100 之间")

    now = datetime.now().astimezone()
    begin_date = (now - timedelta(days=days)).date().isoformat()
    end_date = now.date().isoformat()
    errors: list[str] = []
    warnings: list[str] = []
    primary_available = False
    cached = False
    rows: list[dict[str, Any]] = []

    try:
        frame, cached = cached_call(
            f"announcements:{code}:{begin_date}:{end_date}",
            lambda: _fetch_akshare(code, begin_date, end_date),
            ttl_seconds=86400 if use_cache else 0,
            attempts=1,
        )
        primary_available = True
        rows = frame_records(frame)
    except Exception as exc:
        errors.append(f"AKShare/东方财富公司公告: {exc}")

    fallback_attempted = not primary_available
    fallback_used = False
    fallback_available = False
    rss_route: str | None = None
    if fallback_attempted:
        rss_rows, rss_route, rss_errors = _fetch_exchange_rss(
            code,
            begin_date,
            end_date,
            limit=limit,
        )
        if rss_rows:
            rows = rss_rows
            fallback_used = True
        fallback_available = bool(rss_rows) or not rss_errors
        if rss_errors:
            warnings.extend(f"RSSHub {error}" for error in rss_errors)

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        item = _normalize_item(row, code)
        if item is None:
            continue
        key = re.sub(r"\s+", "", f"{item['title']}|{item.get('publish_date') or ''}").lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
    items.sort(key=lambda item: (item.get("publish_date") or "", item.get("title") or ""), reverse=True)
    items = items[:limit]

    name = next((str(item.get("name")) for item in items if item.get("name")), None)
    if name is None:
        try:
            from src.data.stock_index_loader import get_index_stock_name

            name = get_index_stock_name(code)
        except Exception:
            name = None

    acquisition_succeeded = primary_available or fallback_available
    if primary_available:
        source = "AKShare/东方财富公司公告"
        source_chain = [source]
    elif fallback_available:
        source = "RSSHub/交易所官方披露"
        source_chain = [f"{source}:{rss_route}"]
    else:
        source = "none"
        source_chain = []

    if not items and acquisition_succeeded:
        warnings.append(f"在 {begin_date} 至 {end_date} 范围内未找到符合类型条件的公告")
    latest = next((item.get("publish_date") for item in items if item.get("publish_date")), None)
    type_distribution: dict[str, int] = {}
    for item in items:
        category = str(item.get("source_notice_type") or "未提供")
        type_distribution[category] = type_distribution.get(category, 0) + 1
    return {
        "symbol": code,
        "name": name,
        "days": days,
        "type": type,
        "limit": limit,
        "items": items,
        "item_count": len(items),
        "has_announcements": bool(items),
        "analysis": {
            "semantic_status": "model_required",
            "classification_method": None,
            "source_notice_type_distribution": dict(
                sorted(type_distribution.items(), key=lambda pair: (-pair[1], pair[0]))
            ),
            "latest_announcement_date": latest,
        },
        "coverage_start": begin_date,
        "coverage_end": end_date,
        "source": source,
        "source_chain": source_chain,
        "source_scope": "formal_company_announcements",
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
    name="get_announcements",
    description=(
        "获取单只 A 股在指定时间窗内的正式公司公告，返回公告日期、数据源原始类型和可引用链接。"
        "工具不按标题词典判断事件分类或重要性；没有公告也是有效查询结果。"
    ),
    parameters=object_schema(
        {
            "symbol": {"type": "string", "description": "A 股代码或名称"},
            "days": {"type": "integer", "minimum": 1, "maximum": 730, "default": 30, "description": "向前查询自然日数"},
            "type": {
                "type": "string",
                "enum": ["all"],
                "default": "all",
                "description": "固定为 all；公告语义由模型根据证据判断",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30},
        },
        ["symbol"],
    ),
    executor=get_announcements,
    category="events",
)
