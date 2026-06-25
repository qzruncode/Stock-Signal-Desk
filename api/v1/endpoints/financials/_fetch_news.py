# -*- coding: utf-8 -*-
"""News search via RSSHub + direct sources."""
from __future__ import annotations

import logging
import re
import sys
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

from ._helpers import (
    _normalize_symbol, _safe_float, _safe_str, _parse_date, _pick_col,
    _rss_stock_keywords, _rss_stock_industry_keywords,
    _rss_stock_feed_specs, _classify_financial_text, _normalize_rss_text,
    _fetch_rsshub_entries, _rss_entry_text, _rss_entry_summary, _rss_entry_date,
    _dedupe_rss_entries, _build_structured_analysis,
    _latest_content_time, _content_is_stale,
)
from ._cache import _daily_cache_get, _daily_cache_put, NEWS_CACHE_KEY

# Late-bound reference for test monkey-patching compatibility
_pkg = sys.modules[__package__]

def _fetch_direct_sentiment_sources(symbol: str, days: int) -> tuple[list[dict], list[str]]:
    """Fallback to direct AkShare news/report feeds when RSSHub coverage is empty or too thin."""
    import akshare as ak

    code = _normalize_symbol(symbol)
    cutoff = datetime.now() - timedelta(days=days)
    items: list[dict] = []
    errors: list[str] = []

    try:
        df = ak.stock_news_em(symbol=code)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                pub_time = _parse_date(row.get("发布时间"))
                if pub_time is not None and pub_time < cutoff:
                    continue
                items.append({
                    "title": _safe_str(row.get("新闻标题")),
                    "content": _safe_str(row.get("新闻内容")),
                    "date_str": pub_time.date().isoformat() if pub_time else None,
                    "source": _safe_str(row.get("文章来源")) or "东方财富新闻",
                })
    except Exception as exc:
        errors.append(f"东方财富新闻直连: {exc}")

    try:
        df = ak.stock_research_report_em(symbol=code)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                pub_time = _parse_date(row.get("日期"))
                if pub_time is not None and pub_time < cutoff:
                    continue
                institution = _safe_str(row.get("机构"))
                rating = _safe_str(row.get("东财评级"))
                summary_parts = [part for part in (institution, rating) if part]
                items.append({
                    "title": _safe_str(row.get("报告名称")),
                    "content": "；".join(summary_parts),
                    "date_str": pub_time.date().isoformat() if pub_time else None,
                    "source": institution or "东方财富研报",
                })
    except Exception as exc:
        errors.append(f"东方财富研报直连: {exc}")

    deduped: list[dict] = []
    seen: set[str] = set()
    for item in items:
        key = re.sub(r"\s+", "", f"{item.get('title', '')}|{item.get('date_str', '')}").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped, errors


def _fetch_direct_news_sources(symbol: str, days: int) -> tuple[list[dict], list[str], list[str]]:
    """Direct AkShare fallback for stock news and research reports."""
    import akshare as ak

    code = _normalize_symbol(symbol)
    cutoff = datetime.now() - timedelta(days=days)
    items: list[dict] = []
    errors: list[str] = []
    used_sources: list[str] = []

    try:
        df = ak.stock_news_em(symbol=code)
        if df is not None and not df.empty:
            count = 0
            for _, row in df.iterrows():
                pub_time = _parse_date(row.get("发布时间"))
                if pub_time is not None and pub_time < cutoff:
                    continue
                title = _safe_str(row.get("新闻标题"))
                summary = _safe_str(row.get("新闻内容"))
                structured = _classify_financial_text(f"{title} {summary}")
                items.append({
                    "title": title,
                    "summary": summary,
                    "publish_time": pub_time.isoformat() if pub_time else None,
                    "source": _safe_str(row.get("文章来源")) or "东方财富新闻",
                    "url": _safe_str(row.get("新闻链接")),
                    "category": "新闻",
                    **structured,
                })
                count += 1
            if count > 0:
                used_sources.append(f"东方财富新闻直连({count}条)")
    except Exception as exc:
        errors.append(f"东方财富新闻直连: {exc}")

    try:
        df = ak.stock_research_report_em(symbol=code)
        if df is not None and not df.empty:
            count = 0
            for _, row in df.iterrows():
                pub_time = _parse_date(row.get("日期"))
                if pub_time is not None and pub_time < cutoff:
                    continue
                title = _safe_str(row.get("报告名称"))
                org = _safe_str(row.get("机构"))
                rating = _safe_str(row.get("东财评级"))
                summary_parts = [p for p in [f"机构: {org}", f"评级: {rating}"] if p.strip() not in ("机构: ", "评级: ")]
                structured = _classify_financial_text(f"{title} {'；'.join(summary_parts)}")
                items.append({
                    "title": title,
                    "summary": "；".join(summary_parts) if summary_parts else "",
                    "publish_time": pub_time.isoformat() if pub_time else None,
                    "source": org or "券商研报",
                    "url": _safe_str(row.get("报告PDF链接")),
                    "category": "研报",
                    **structured,
                })
                count += 1
            if count > 0:
                used_sources.append(f"券商研报直连({count}条)")
    except Exception as exc:
        errors.append(f"券商研报直连: {exc}")

    return items, used_sources, errors



def _fetch_rss_stock_news(code: str, days: int, limit: int = 80) -> tuple[list[dict], list[str], list[str]]:
    """Fetch and aggregate stock news through the project-local RSSHub instance."""
    errors: list[str] = []
    used_sources: list[str] = []
    items: list[dict] = []
    keywords = _rss_stock_keywords(code)

    entries, keywords, fetch_errors = _fetch_rsshub_entries(
        code,
        days,
        _rss_stock_feed_specs(keywords),
        keywords=keywords,
        limit=limit,
    )
    errors.extend(fetch_errors)
    for entry in entries:
        label = _safe_str(entry.get("_rss_source_label", "RSSHub"))
        title = re.sub(r"<[^>]+>", "", _safe_str(entry.get("title")))
        text = f"{title} {_safe_str(entry.get('summary'))}"
        structured = _classify_financial_text(text)
        items.append({
            "title": title,
            "summary": _safe_str(entry.get("summary")),
            "publish_time": entry.get("published"),
            "source": _safe_str(entry.get("author")) or label,
            "url": _safe_str(entry.get("link")),
            "category": "新闻",
            **structured,
            "_rss_source_label": label,
        })

    unique_rss_items = []
    seen_rss = set()
    source_counts: dict[str, int] = {}
    for item in items:
        normalized_title = _normalize_rss_text(item.get("title")).strip()
        key = normalized_title or _safe_str(item.get("url"))
        if key and key in seen_rss:
            continue
        seen_rss.add(key)
        label = _safe_str(item.pop("_rss_source_label", "RSSHub"))
        source_counts[label] = source_counts.get(label, 0) + 1
        unique_rss_items.append(item)
    items = unique_rss_items

    used_sources.extend(
        f"RSSHub{label}({count}条)"
        for label, count in source_counts.items()
        if count > 0
    )

    if items:
        logger.info(
            "[News] RSSHub aggregate OK for %s: %s items, keywords=%s",
            code,
            len(items),
            keywords,
        )

    return items, used_sources, errors


def _fetch_news(symbol: str, days: int, source: str) -> dict:
    """搜索指定股票的相关新闻。

    数据源:
      1. RSSHub 东方财富搜索新闻 (/eastmoney/search/:keyword)
      2. 东方财富个股研报 (stock_research_report_em)
    """
    import time as _time
    import akshare as ak

    t0 = _time.time()
    code = _normalize_symbol(symbol)
    errors: list[str] = []
    news_items: list[dict] = []
    used_sources: list[str] = []

    cutoff = datetime.now() - timedelta(days=days)

    def _is_recent(d) -> bool:
        if d is None:
            return True
        return d >= cutoff

    # -------------------------------------------------------------------
    # Source 1: RSSHub 东方财富搜索新闻
    # -------------------------------------------------------------------
    if source in ("all", "eastmoney", "news"):
        rss_items, rss_sources, rss_errors = _pkg._fetch_rss_stock_news(code, days)
        news_items.extend(rss_items)
        used_sources.extend(rss_sources)
        errors.extend(rss_errors)
        if len(rss_items) < 5:
            direct_items, direct_sources, direct_errors = _pkg._fetch_direct_news_sources(code, days)
            news_items.extend([item for item in direct_items if item.get("category") == "新闻"])
            used_sources.extend([item for item in direct_sources if "新闻" in item])
            errors.extend(direct_errors)

    # -------------------------------------------------------------------
    # Source 2: 东方财富个股研报
    # -------------------------------------------------------------------
    if source in ("all", "eastmoney", "research"):
        try:
            df = ak.stock_research_report_em(symbol=code)
            if df is not None and not df.empty:
                title_col = _pick_col(df.columns, ["报告名称", "title"])
                org_col = _pick_col(df.columns, ["机构", "org"])
                date_col = _pick_col(df.columns, ["日期", "date"])
                url_col = _pick_col(df.columns, ["报告PDF链接", "url"])
                rating_col = _pick_col(df.columns, ["东财评级", "评级", "rating"])

                research_count = 0
                for _, row in df.iterrows():
                    pub_time = _parse_date(row.get(date_col)) if date_col is not None else None
                    if not _is_recent(pub_time):
                        continue
                    title = _safe_str(row.get(title_col)) if title_col is not None else ""
                    org = _safe_str(row.get(org_col)) if org_col is not None else ""
                    rating = _safe_str(row.get(rating_col)) if rating_col is not None else ""
                    summary_parts = [p for p in [f"机构: {org}", f"评级: {rating}"] if p.strip() not in ("机构: ", "评级: ")]
                    structured = _classify_financial_text(f"{title} {'；'.join(summary_parts)}")
                    news_items.append({
                        "title": title,
                        "summary": "；".join(summary_parts) if summary_parts else "",
                        "publish_time": pub_time.isoformat() if pub_time else None,
                        "source": org or "券商研报",
                        "url": _safe_str(row.get(url_col)) if url_col is not None else "",
                        "category": "研报",
                        **structured,
                    })
                    research_count += 1

                if research_count > 0:
                    used_sources.append(f"券商研报({research_count}条)")
                    logger.info(f"[News] research reports OK for {code}: {research_count} items")
                if research_count < 3:
                    rss_research_items, rss_research_sources, rss_research_errors = _pkg._fetch_rss_research_reports(code, days)
                    news_items.extend([
                        {
                            "title": item.get("title", ""),
                            "summary": _safe_str(item.get("event_label") or ""),
                            "publish_time": item.get("publish_date"),
                            "source": item.get("org") or "RSSHub研报",
                            "url": item.get("url") or "",
                            "category": "研报",
                            "event_type": item.get("event_type"),
                            "event_label": item.get("event_label"),
                            "polarity": item.get("polarity"),
                            "importance": item.get("importance"),
                            "tags": item.get("tags", []),
                        }
                        for item in rss_research_items
                    ])
                    used_sources.extend(rss_research_sources)
                    errors.extend(rss_research_errors)
        except Exception as exc:
            errors.append(f"券商研报: {exc}")
            logger.warning(f"[News] research reports failed for {code}: {exc}")
            rss_research_items, rss_research_sources, rss_research_errors = _pkg._fetch_rss_research_reports(code, days)
            news_items.extend([
                {
                    "title": item.get("title", ""),
                    "summary": _safe_str(item.get("event_label") or ""),
                    "publish_time": item.get("publish_date"),
                    "source": item.get("org") or "RSSHub研报",
                    "url": item.get("url") or "",
                    "category": "研报",
                    "event_type": item.get("event_type"),
                    "event_label": item.get("event_label"),
                    "polarity": item.get("polarity"),
                    "importance": item.get("importance"),
                    "tags": item.get("tags", []),
                }
                for item in rss_research_items
            ])
            used_sources.extend(rss_research_sources)
            errors.extend(rss_research_errors)

    # 去重 + 按时间倒序
    seen = set()
    unique_items = []
    for item in news_items:
        normalized_title = re.sub(r"<[^>]+>", "", item.get("title") or "")
        normalized_title = normalized_title.replace("Ａ", "A").replace("Ｂ", "B").strip().upper()
        key = normalized_title or (item.get("url") or "")
        if key and key not in seen:
            seen.add(key)
            unique_items.append(item)
    unique_items.sort(
        key=lambda x: x.get("publish_time") or "",
        reverse=True,
    )

    logger.info(f"[News] total {_time.time() - t0:.1f}s for {code}: "
                f"{len(unique_items)} unique items from {used_sources}")
    latest_time = _latest_content_time(unique_items, ["publish_time", "publish_date", "date_str"])

    return {
        "symbol": code,
        "days": days,
        "source": source,
        "items": unique_items,
        "analysis": _build_structured_analysis(unique_items, days=days, dimension="相关新闻"),
        "source_chain": used_sources,
        "errors": errors,
        "data_time": latest_time,
        "is_stale": _content_is_stale(unique_items, days, ["publish_time", "publish_date", "date_str"]),
        "fallback_used": bool(errors),
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
    }

