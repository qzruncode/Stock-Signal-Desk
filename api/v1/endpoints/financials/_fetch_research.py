# -*- coding: utf-8 -*-
"""Research reports via RSSHub."""
from __future__ import annotations

import logging
import re
import sys
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

from ._helpers import (
    _normalize_symbol, _safe_float, _safe_str, _safe_int_like, _parse_date,
    _rss_stock_keywords, _classify_financial_text,
    _fetch_rsshub_entries, _rss_entry_text, _rss_entry_summary, _rss_entry_date,
    _dedupe_rss_entries, _build_structured_analysis,
    _latest_content_time, _content_is_stale,
)
from ._cache import _daily_cache_get, _daily_cache_put

# Late-bound reference for test monkey-patching compatibility
_pkg = sys.modules[__package__]

def _fetch_rss_research_reports(symbol: str, days: int) -> tuple[list[dict], list[str], list[str]]:
    code = _normalize_symbol(symbol)
    keywords = _rss_stock_keywords(code)
    feed_specs = [
        ("eastmoney_report", {"category": "stock"}, "东方财富个股研报", True),
        ("ulapia", {"category": "stock_research"}, "ulapia个股研报", True),
        ("ulapia", {"category": "brokerage_news"}, "ulapia券商晨报", True),
    ]
    entries, _, errors = _fetch_rsshub_entries(
        code,
        days,
        feed_specs,
        keywords=keywords,
        limit=80,
        max_workers=6,
    )

    items: list[dict] = []
    source_counts: dict[str, int] = {}
    for entry in _dedupe_rss_entries(entries):
        title = _rss_entry_text(entry)
        summary = _rss_entry_summary(entry)
        pub_time = _rss_entry_date(entry)
        label = _safe_str(entry.get("_rss_source_label") or entry.get("author") or "RSSHub研报")
        structured = _classify_financial_text(f"{title} {summary}")
        items.append({
            "title": title,
            "org": _safe_str(entry.get("author")) or label,
            "rating": None,
            "industry": None,
            "publish_date": pub_time.date().isoformat() if pub_time else None,
            "url": _safe_str(entry.get("link")),
            "profit_forecasts": [],
            "monthly_report_count": None,
            **structured,
        })
        source_counts[label] = source_counts.get(label, 0) + 1

    used_sources = [f"RSSHub{label}({count}条)" for label, count in source_counts.items() if count > 0]
    return items, used_sources, errors



def _fetch_research_reports(symbol: str, days: int) -> dict:
    """获取券商对公司的最新研究报告摘要。

    数据源: akshare.stock_research_report_em()
    """
    import time as _time
    import akshare as ak

    t0 = _time.time()
    code = _normalize_symbol(symbol)
    errors: list[str] = []
    cutoff = datetime.now() - timedelta(days=days)
    items: list[dict] = []

    try:
        df = ak.stock_research_report_em(symbol=code)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                pub_time = _parse_date(row.get("日期"))
                if pub_time and pub_time < cutoff:
                    continue

                # Extract analyst info from title (some reports include analyst names)
                title = _safe_str(row.get("报告名称"))
                org = _safe_str(row.get("机构"))
                rating = _safe_str(row.get("东财评级"))
                url = _safe_str(row.get("报告PDF链接"))
                industry = _safe_str(row.get("行业"))

                # Build profit forecast summary
                forecasts = []
                for year_key, label in [
                    ("2026-盈利预测-收益", "2026"),
                    ("2027-盈利预测-收益", "2027"),
                    ("2028-盈利预测-收益", "2028"),
                ]:
                    eps = _safe_float(row.get(year_key))
                    pe = _safe_float(row.get(year_key.replace("-收益", "-市盈率")))
                    if eps is not None:
                        forecasts.append({
                            "year": label,
                            "eps": eps,
                            "pe": pe,
                        })

                items.append({
                    "title": title,
                    "org": org,
                    "rating": rating,
                    "industry": industry,
                    "publish_date": pub_time.date().isoformat() if pub_time else None,
                    "url": url,
                    "profit_forecasts": forecasts,
                    "monthly_report_count": _safe_int_like(row.get("近一月个股研报数")),
                })

            logger.info(f"[Research] OK for {code}: {len(items)} items (days={days})")
    except Exception as exc:
        errors.append(f"券商研报: {exc}")
        logger.warning(f"[Research] failed for {code}: {exc}")

    used_sources: list[str] = [f"东方财富研报直连({len(items)}条)"] if items else []
    if len(items) < 3:
        rss_items, rss_sources, rss_errors = _pkg._fetch_rss_research_reports(code, days)
        errors.extend(rss_errors)
        existing_keys = {
            re.sub(r"\s+", "", f"{item.get('title', '')}|{item.get('publish_date', '')}").lower()
            for item in items
        }
        for item in rss_items:
            key = re.sub(r"\s+", "", f"{item.get('title', '')}|{item.get('publish_date', '')}").lower()
            if key and key not in existing_keys:
                existing_keys.add(key)
                items.append(item)
        used_sources.extend(rss_sources)

    return {
        "symbol": code,
        "days": days,
        "items": items,
        "analysis": _build_structured_analysis(items, days=days, dimension="券商研报", source_key="org"),
        "source_chain": used_sources,
        "errors": errors,
        "data_time": _latest_content_time(items, ["publish_date"]),
        "is_stale": _content_is_stale(items, days, ["publish_date"]),
        "fallback_used": bool(errors or len(used_sources) > 1 or (used_sources and not used_sources[0].startswith("东方财富"))),
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
    }
