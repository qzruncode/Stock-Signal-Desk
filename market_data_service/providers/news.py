"""``search_news`` — precise, bounded news for one listed company."""

from __future__ import annotations
from contextvars import copy_context
import html
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from market_data_service.providers.common import (
    bare_local_symbol,
    bare_symbol,
    cached_call,
    frame_records,
)

_RSS_ROUTE = "/eastmoney/search/:keyword"
DESCRIPTION = "从 AKShare 的 stock_news_em 单一来源读取一只 A 股/北交所公司的相关新闻，按公司名称或代码校验主体、去重并限制返回量；不会调用 RSSHub 或其他新闻来源。这是 reference-only 来源索引，不包含新闻正文；若要用新闻内容支撑实质性结论，必须继续调用 read_web_source 读取对应 URL。"


def _clean_text(value: Any, limit: int = 700) -> str:
    text = html.unescape(re.sub("<[^>]+>", " ", str(value or "")))
    text = re.sub("\\s+", " ", text).strip()
    return text[:limit]


def _date_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed


def _stock_name(code: str) -> str | None:
    try:
        from market_data_service.providers.symbols import get_index_stock_name

        return str(get_index_stock_name(code) or "").strip() or None
    except Exception:
        return None


def _fetch_direct(code: str):
    import akshare as ak

    frame = ak.stock_news_em(symbol=code)
    if frame is None or frame.empty:
        raise RuntimeError("AKShare stock_news_em 没有返回单股新闻")
    return frame


def _fetch_rss(name: str, limit: int, *, force: bool = False) -> dict[str, Any]:
    from market_data_service.providers.rss_reader import read_feed

    return read_feed(
        route_path=_RSS_ROUTE,
        params={"keyword": name},
        limit=max(10, min(50, limit * 2)),
        force=force,
    )


def _canonical_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
        return urlunsplit(
            (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", "")
        )
    except ValueError:
        return text


def _entity_mentions(
    title: str, summary: str, code: str, name: str | None
) -> dict[str, bool]:
    normalized_title = re.sub("\\s+", "", title).upper()
    normalized_summary = re.sub("\\s+", "", summary).upper()
    normalized_name = re.sub("\\s+", "", name or "").upper()
    code_patterns = (
        code,
        f"SH{code}",
        f"SZ{code}",
        f"BJ{code}",
        f"{code}.SH",
        f"{code}.SZ",
        f"{code}.BJ",
    )
    name_in_title = bool(normalized_name and normalized_name in normalized_title)
    name_in_summary = bool(normalized_name and normalized_name in normalized_summary)
    code_in_title = any((pattern in normalized_title for pattern in code_patterns))
    code_in_summary = any((pattern in normalized_summary for pattern in code_patterns))
    return {
        "name_in_title": name_in_title,
        "name_in_summary": name_in_summary,
        "code_in_title": code_in_title,
        "code_in_summary": code_in_summary,
    }


def _normalize_direct(
    frame: Any, code: str, name: str | None
) -> tuple[list[dict[str, Any]], int]:
    items: list[dict[str, Any]] = []
    weak_mentions = 0
    for row in frame_records(frame):
        title = _clean_text(row.get("新闻标题"), 300)
        summary = _clean_text(row.get("新闻内容"))
        mentions = _entity_mentions(title, summary, code, name)
        if not any(mentions.values()):
            weak_mentions += 1
        published = _date_time(row.get("发布时间"))
        items.append(
            {
                "title": title,
                "summary": summary,
                "published": published.isoformat() if published else None,
                "source": _clean_text(row.get("文章来源"), 100) or "东方财富新闻",
                "url": str(row.get("新闻链接") or "").strip(),
                "source_type": "akshare_stock_news_em",
                "entity_mentions": mentions,
                "relevance": None,
                "relevance_score": None,
                "semantic_status": "model_required",
            }
        )
    return (items, weak_mentions)


def _normalize_rss(
    payload: dict[str, Any], code: str, name: str | None
) -> tuple[list[dict[str, Any]], int]:
    items: list[dict[str, Any]] = []
    weak_mentions = 0
    for row in payload.get("items") or []:
        title = _clean_text(row.get("title"), 300)
        summary = _clean_text(row.get("summary"))
        mentions = _entity_mentions(title, summary, code, name)
        if not any(mentions.values()):
            weak_mentions += 1
        published = _date_time(row.get("published"))
        items.append(
            {
                "title": title,
                "summary": summary,
                "published": published.isoformat() if published else None,
                "source": _clean_text(row.get("source") or row.get("author"), 100)
                or "RSSHub/东方财富搜索",
                "url": str(row.get("link") or "").strip(),
                "source_type": "rsshub_eastmoney_search",
                "rss_route": _RSS_ROUTE,
                "entity_mentions": mentions,
                "relevance": None,
                "relevance_score": None,
                "semantic_status": "model_required",
            }
        )
    return (items, weak_mentions)


def _dedupe(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for item in items:
        key = (
            _canonical_url(item.get("url"))
            or re.sub("\\W+", "", str(item.get("title") or "")).lower()
        )
        if not key:
            continue
        existing = unique.get(key)
        if existing is None or len(str(item.get("summary") or "")) > len(
            str(existing.get("summary") or "")
        ):
            unique[key] = item
    result = list(unique.values())
    result.sort(key=lambda item: item.get("published") or "", reverse=True)
    return result[:limit]


def search_news(
    symbol: str, days: int = 30, limit: int = 20, use_cache: bool = True
) -> dict[str, Any]:
    code = bare_symbol(symbol)
    if not re.fullmatch("\\d{6}", code):
        raise ValueError(
            "symbol 必须能解析为 6 位股票代码；行业或主题资讯请直接选择对应的 read_rss_* 数据源工具，必要时再使用 websearch"
        )
    if not 1 <= int(days) <= 365:
        raise ValueError("days 必须在 1 到 365 之间")
    if not 1 <= int(limit) <= 50:
        raise ValueError("limit 必须在 1 到 50 之间")
    name = _stock_name(code)
    errors: list[str] = []
    warnings: list[str] = []
    direct_frame = None
    rss_payload: dict[str, Any] = {}
    cache_detail = {"akshare_news": False, "rsshub": False}

    def direct_task():
        if not use_cache:
            return (_fetch_direct(code), False)
        return cached_call(
            f"stock-news:{code}", lambda: _fetch_direct(code), ttl_seconds=1800
        )

    def rss_task():
        if not use_cache:
            return _fetch_rss(name or code, int(limit), force=True)
        return _fetch_rss(name or code, int(limit))

    with ThreadPoolExecutor(max_workers=2) as pool:
        direct_future = pool.submit(copy_context().run, direct_task)
        rss_future = pool.submit(copy_context().run, rss_task)
        try:
            direct_frame, cache_detail["akshare_news"] = direct_future.result()
        except Exception as exc:
            errors.append(f"akshare_news: {type(exc).__name__}: {exc}")
        try:
            rss_payload = rss_future.result()
            cache_detail["rsshub"] = bool(rss_payload.get("_cached"))
            errors.extend(
                (f"rsshub: {error}" for error in rss_payload.get("errors") or [])
            )
        except Exception as exc:
            errors.append(f"rsshub: {type(exc).__name__}: {exc}")
    direct_items, direct_weak = _normalize_direct(direct_frame, code, name)
    rss_items, rss_weak = _normalize_rss(rss_payload, code, name)
    cutoff = datetime.now() - timedelta(days=int(days))
    all_items = []
    undated_count = 0
    for item in [*direct_items, *rss_items]:
        published = _date_time(item.get("published"))
        if published is None:
            undated_count += 1
        elif published < cutoff:
            continue
        all_items.append(item)
    items = _dedupe(all_items, int(limit))
    if direct_weak + rss_weak:
        warnings.append(
            f"{direct_weak + rss_weak} 条结果未在标题或摘要中逐字出现证券代码/简称；已保留供模型结合来源语义复核"
        )
    if undated_count:
        warnings.append(f"包含 {undated_count} 条无可验证发布时间的结果")
    if not name:
        warnings.append("本地股票索引未找到公司简称，仅能按代码校验主体")
    dates = [_date_time(item.get("published")) for item in items]
    latest = max((value for value in dates if value), default=None)
    sources = sorted(
        {str(item.get("source_type")) for item in items if item.get("source_type")}
    )
    return {
        "symbol": code,
        "name": name,
        "days": int(days),
        "limit": int(limit),
        "items": items,
        "item_count": len(items),
        "unverified_entity_mention_count": direct_weak + rss_weak,
        "source": "AKShare stock_news_em + RSSHub 东方财富搜索",
        "sources": sources,
        "rss_routes": [{"route_path": _RSS_ROUTE, "params": {"keyword": name or code}}],
        "success": bool(items),
        "partial": bool(errors) and bool(items),
        "errors": list(dict.fromkeys(errors))[:10],
        "warnings": warnings,
        "data_time": latest.isoformat() if latest else None,
        "freshness_unknown": latest is None,
        "is_stale": latest < cutoff if latest else None,
        "fallback_used": False,
        "fallback_recommended": not items,
        "fallback_query": f"{name or code} {code} 最新新闻" if not items else None,
        "cache_detail": cache_detail,
        "_cached": all(cache_detail.values()),
        "_fetched_at": datetime.now().astimezone().isoformat(),
    }


def read_company_news_akshare(
    symbol: str, days: int = 30, limit: int = 20, use_cache: bool = True
) -> dict[str, Any]:
    """Read one provider's company-news feed without fallback source mixing."""
    code = bare_local_symbol(symbol)
    if not re.fullmatch("\\d{6}", code):
        raise ValueError("symbol 必须能解析为 6 位股票代码")
    if not 1 <= int(days) <= 365:
        raise ValueError("days 必须在 1 到 365 之间")
    if not 1 <= int(limit) <= 50:
        raise ValueError("limit 必须在 1 到 50 之间")
    name = _stock_name(code)
    try:
        if use_cache:
            frame, cached = cached_call(
                f"stock-news:{code}", lambda: _fetch_direct(code), ttl_seconds=1800
            )
        else:
            frame, cached = (_fetch_direct(code), False)
    except Exception as exc:
        return {
            "success": False,
            "partial": False,
            "symbol": code,
            "name": name,
            "items": [],
            "item_count": 0,
            "source": "AKShare stock_news_em",
            "errors": [f"akshare_news: {type(exc).__name__}: {exc}"],
            "warnings": [],
            "content_access": {
                "mode": "reference_only",
                "content_read": False,
                "content_extracted": False,
                "content_read_required": False,
            },
            "reference_links": [],
            "data_time": None,
            "is_stale": None,
            "freshness_unknown": True,
            "_cached": False,
        }
    cutoff = datetime.now() - timedelta(days=int(days))
    direct_items, weak_mentions = _normalize_direct(frame, code, name)
    undated_count = 0
    filtered: list[dict[str, Any]] = []
    for item in direct_items:
        published = _date_time(item.get("published"))
        if published is None:
            undated_count += 1
        elif published < cutoff:
            continue
        filtered.append(item)
    items = _dedupe(filtered, int(limit))
    dates = [_date_time(item.get("published")) for item in items]
    latest = max((value for value in dates if value), default=None)
    warnings: list[str] = []
    if weak_mentions:
        warnings.append(
            f"{weak_mentions} 条结果未在标题或摘要中逐字出现证券代码或简称；需结合来源语义复核"
        )
    if undated_count:
        warnings.append(f"包含 {undated_count} 条无可验证发布时间的结果")
    if not name:
        warnings.append("本地股票索引未找到公司简称，仅能按代码校验主体")
    reference_links = [
        str(item.get("url") or "").strip()
        for item in items
        if str(item.get("url") or "").strip()
    ]
    return {
        "success": True,
        "partial": False,
        "symbol": code,
        "name": name,
        "days": int(days),
        "limit": int(limit),
        "items": items,
        "item_count": len(items),
        "unverified_entity_mention_count": weak_mentions,
        "source": "AKShare stock_news_em",
        "sources": ["akshare_stock_news_em"],
        "content_access": {
            "mode": "reference_only",
            "content_read": False,
            "content_extracted": False,
            "content_read_required": bool(reference_links),
        },
        "reference_links": reference_links,
        "data_time": latest.isoformat() if latest else None,
        "freshness_unknown": latest is None,
        "is_stale": latest < cutoff if latest else None,
        "errors": [],
        "warnings": warnings,
        "_cached": cached,
        "_fetched_at": datetime.now().astimezone().isoformat(),
    }
