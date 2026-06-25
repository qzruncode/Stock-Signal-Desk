# -*- coding: utf-8 -*-
"""Company announcements via RSSHub."""
from __future__ import annotations

import logging
import re
import sys
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

from ._helpers import (
    _normalize_symbol, _safe_str, _parse_date, _to_em_symbol, _pick_col,
    _rss_stock_keywords, _classify_financial_text,
    _fetch_rsshub_entries, _rss_entry_text, _rss_entry_summary, _rss_entry_date,
    _dedupe_rss_entries, _build_structured_analysis,
    _latest_content_time, _content_is_stale,
)
from ._cache import _daily_cache_get, _daily_cache_put

# Late-bound reference for test monkey-patching compatibility
_pkg = sys.modules[__package__]

def _fetch_rss_announcements(symbol: str, days: int, ann_type: str) -> tuple[list[dict], list[str], list[str]]:
    code = _normalize_symbol(symbol)
    em_symbol = _to_em_symbol(code)
    feed_specs: list[tuple[str, dict, str, bool]] = []
    if em_symbol.startswith("SZ"):
        feed_specs.append(("szse_disclosure", {"stock_code": code}, "深交所公告", False))

    entries, _, errors = _fetch_rsshub_entries(
        code,
        days,
        feed_specs,
        keywords=[],
        limit=100,
        max_workers=2,
        timeout=10.0,
    )

    type_keywords = {
        "业绩": ["业绩", "年报", "半年报", "季报", "报告", "预告", "快报", "修正"],
        "分红": ["分红", "派息", "送转", "权益分派", "利润分配"],
        "增持": ["增持", "回购"],
        "减持": ["减持"],
        "高管变动": ["高管", "董事", "监事", "独立董事", "任职", "辞职", "变更", "聘任"],
    }

    items: list[dict] = []
    source_counts: dict[str, int] = {}
    for entry in _dedupe_rss_entries(entries):
        title = _rss_entry_text(entry)
        if ann_type != "all":
            keywords = type_keywords.get(ann_type, [])
            if keywords and not any(keyword in title for keyword in keywords):
                continue
        pub_time = _rss_entry_date(entry)
        label = _safe_str(entry.get("_rss_source_label") or entry.get("author") or "RSSHub公告")
        items.append({
            "title": title,
            "notice_type": ann_type if ann_type != "all" else "公告",
            "publish_date": pub_time.date().isoformat() if pub_time else None,
            "url": _safe_str(entry.get("link")),
            "source": label,
        })
        source_counts[label] = source_counts.get(label, 0) + 1

    used_sources = [f"RSSHub{label}({count}条)" for label, count in source_counts.items() if count > 0]
    return items, used_sources, errors



def _fetch_announcements(symbol: str, days: int, ann_type: str) -> dict:
    """获取上市公司公告。

    数据源: akshare.stock_individual_notice_report()
    """
    import time as _time
    import akshare as ak

    t0 = _time.time()
    code = _normalize_symbol(symbol)
    errors: list[str] = []
    items: list[dict] = []

    try:
        end_date = datetime.now().strftime("%Y-%m-%d")
        begin_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        df = ak.stock_individual_notice_report(
            security=code,
            symbol="全部",
            begin_date=begin_date,
            end_date=end_date,
        )
        if df is not None and not df.empty:
            title_col = _pick_col(df.columns, ["公告标题", "title"])
            type_col = _pick_col(df.columns, ["公告类型", "type"])
            date_col = _pick_col(df.columns, ["公告日期", "date"])
            url_col = _pick_col(df.columns, ["网址", "url"])

            # 类型关键词映射
            TYPE_KEYWORDS = {
                "业绩": ["业绩", "年报", "半年报", "季报", "报告", "预告", "快报", "修正"],
                "分红": ["分红", "派息", "送转", "权益分派", "利润分配"],
                "增持": ["增持", "回购"],
                "减持": ["减持"],
                "高管变动": ["高管", "董事", "监事", "独立董事", "任职", "辞职", "变更", "聘任"],
            }

            for _, row in df.iterrows():
                pub_time = _parse_date(row.get(date_col)) if date_col is not None else None
                title = _safe_str(row.get(title_col)) if title_col is not None else ""
                notice_type = _safe_str(row.get(type_col)) if type_col is not None else ""

                # 类型过滤
                if ann_type != "all":
                    keywords = TYPE_KEYWORDS.get(ann_type, [])
                    if not any(kw in title or kw in notice_type for kw in keywords):
                        continue

                items.append({
                    "title": title,
                    "notice_type": notice_type,
                    "publish_date": pub_time.date().isoformat() if pub_time else None,
                    "url": _safe_str(row.get(url_col)) if url_col is not None else "",
                    **_classify_financial_text(f"{notice_type} {title}"),
                })

            logger.info(f"[Announcements] OK for {code}: {len(items)} items (type={ann_type})")
    except Exception as exc:
        errors.append(f"公司公告: {exc}")
        logger.warning(f"[Announcements] failed for {code}: {exc}")

    if len(items) < 3:
        rss_items, rss_sources, rss_errors = _pkg._fetch_rss_announcements(code, days, ann_type)
        items.extend(rss_items)
        errors.extend(rss_errors)
        if rss_sources:
            source_chain = rss_sources
        else:
            source_chain = []
    else:
        source_chain = []

    deduped: list[dict] = []
    seen: set[str] = set()
    for item in items:
        key = re.sub(r"\s+", "", f"{item.get('title', '')}|{item.get('publish_date', '')}").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    deduped.sort(key=lambda item: item.get("publish_date") or "", reverse=True)

    return {
        "symbol": code,
        "days": days,
        "type": ann_type,
        "items": deduped,
        "analysis": _build_structured_analysis(deduped, days=days, dimension="公司公告"),
        "source_chain": source_chain,
        "errors": errors,
        "data_time": _latest_content_time(deduped, ["publish_date"]),
        "is_stale": _content_is_stale(deduped, days, ["publish_date"]),
        "fallback_used": bool(errors or source_chain),
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
    }


