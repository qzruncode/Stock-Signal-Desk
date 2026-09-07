# -*- coding: utf-8 -*-
"""Legacy evidence endpoint for model-based sentiment analysis."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from ._helpers import (
    _content_is_stale,
    _dedupe_rss_entries,
    _fetch_rsshub_entries,
    _latest_content_time,
    _normalize_symbol,
    _parse_date,
    _rss_entry_date,
    _rss_entry_summary,
    _rss_entry_text,
    _rss_stock_feed_specs,
    _rss_stock_keywords,
    _safe_str,
)

logger = logging.getLogger(__name__)


def _fetch_direct_sentiment_sources(
    symbol: str,
    days: int,
) -> tuple[list[dict], list[str]]:
    """Fetch source material when RSS coverage is thin."""
    import akshare as ak

    code = _normalize_symbol(symbol)
    cutoff = datetime.now() - timedelta(days=days)
    items: list[dict] = []
    errors: list[str] = []
    sources = (
        (
            "东方财富新闻直连",
            lambda: ak.stock_news_em(symbol=code),
            "发布时间",
            "新闻标题",
            "新闻内容",
            "文章来源",
        ),
        (
            "东方财富研报直连",
            lambda: ak.stock_research_report_em(symbol=code),
            "日期",
            "报告名称",
            None,
            "机构",
        ),
    )
    for label, fetcher, date_col, title_col, content_col, source_col in sources:
        try:
            frame = fetcher()
            if frame is None or frame.empty:
                continue
            for _, row in frame.iterrows():
                published = _parse_date(row.get(date_col))
                if published is not None and published < cutoff:
                    continue
                items.append(
                    {
                        "title": _safe_str(row.get(title_col)),
                        "content": (
                            _safe_str(row.get(content_col)) if content_col else ""
                        ),
                        "date_str": (
                            published.date().isoformat() if published else None
                        ),
                        "source": _safe_str(row.get(source_col)) or label,
                        "semantic_status": "model_required",
                    }
                )
        except Exception as exc:
            errors.append(f"{label}: {exc}")

    deduped: list[dict] = []
    seen: set[str] = set()
    for item in items:
        key = re.sub(
            r"\s+",
            "",
            f"{item.get('title', '')}|{item.get('date_str', '')}",
        ).casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped, errors


def _fetch_sentiment(symbol: str, days: int) -> dict:
    """Collect dated financial content without dictionary sentiment voting."""
    import time as _time

    started = _time.time()
    code = _normalize_symbol(symbol)
    errors: list[str] = []
    subjects = _rss_stock_keywords(code)
    feed_specs = _rss_stock_feed_specs(subjects) + [
        ("eastmoney_report", {"category": "stock"}, "东方财富个股研报", True),
        ("wkjyqh_research", {}, "五矿期货研究", True),
    ]
    entries, _, fetch_errors = _fetch_rsshub_entries(
        code,
        days,
        feed_specs,
        keywords=subjects,
        limit=100,
        max_workers=10,
    )
    errors.extend(fetch_errors)

    unique_items: list[dict] = []
    for entry in _dedupe_rss_entries(entries):
        published = _rss_entry_date(entry)
        unique_items.append(
            {
                "title": _rss_entry_text(entry),
                "content": _rss_entry_summary(entry),
                "date_str": (published.date().isoformat() if published else None),
                "source": _safe_str(
                    entry.get("_rss_source_label") or entry.get("author") or "RSSHub"
                ),
                "semantic_status": "model_required",
            }
        )

    if len(unique_items) < 5:
        direct_items, direct_errors = _fetch_direct_sentiment_sources(
            code,
            days,
        )
        errors.extend(direct_errors)
        existing_keys = {
            re.sub(
                r"\s+",
                "",
                f"{item.get('title', '')}|{item.get('date_str', '')}",
            ).casefold()
            for item in unique_items
        }
        for item in direct_items:
            key = re.sub(
                r"\s+",
                "",
                f"{item.get('title', '')}|{item.get('date_str', '')}",
            ).casefold()
            if key and key not in existing_keys:
                existing_keys.add(key)
                unique_items.append(item)

    daily_counts: dict[str, int] = {}
    for item in unique_items:
        date_str = str(item.get("date_str") or "").strip()
        if date_str:
            daily_counts[date_str] = daily_counts.get(date_str, 0) + 1

    latest_time = _latest_content_time(unique_items, ["date_str"])
    logger.info(
        "[Sentiment evidence] total %.1fs for %s: %s items",
        _time.time() - started,
        code,
        len(unique_items),
    )
    return {
        "symbol": code,
        "days": days,
        "sentiment_score": None,
        "positive_count": None,
        "negative_count": None,
        "neutral_count": None,
        "daily_trend": [
            {"date": date, "total": total}
            for date, total in sorted(daily_counts.items())
        ],
        "top_keywords": [],
        "items": unique_items[:50],
        "analysis": {
            "semantic_status": "model_required",
            "classification_method": None,
            "item_count": len(unique_items),
            "source_count": len(
                {
                    str(item.get("source") or "")
                    for item in unique_items
                    if item.get("source")
                }
            ),
        },
        "errors": errors,
        "data_time": latest_time,
        "is_stale": _content_is_stale(
            unique_items,
            days,
            ["date_str"],
        ),
        "fallback_used": bool(errors),
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
    }
