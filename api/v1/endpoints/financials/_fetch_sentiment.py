# -*- coding: utf-8 -*-
"""Sentiment analysis via RSSHub + direct sources."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

from ._helpers import (
    _normalize_symbol, _safe_float, _safe_str, _parse_date,
    _rss_stock_keywords, _rss_stock_feed_specs, _classify_financial_text,
    _fetch_rsshub_entries, _rss_entry_text, _rss_entry_summary, _rss_entry_date,
    _dedupe_rss_entries, _build_structured_analysis,
    _latest_content_time, _content_is_stale,
)
from ._cache import _daily_cache_get, _daily_cache_put

_SENTIMENT_POSITIVE = {
    '增长', '上升', '突破', '新高', '利好', '看好', '受益', '回暖',
    '复苏', '改善', '提升', '盈利', '超额', '领先', '强劲', '大涨', '涨停',
    '增持', '回购', '分红', '超预期', '创新高', '景气', '放量', '优化',
    '成功', '签约', '合作', '扩张', '布局', '亮眼', '丰厚', '稳健',
    '持续增长', '净流入', '上涨', '反弹', '拉升', '领涨', '跑赢',
    '提振', '兑现', '创纪录', '高景气', '高增长', '强势',
}


def _fetch_direct_sentiment_sources(symbol: str, days: int) -> tuple[list[dict], list[str]]:
    """Use structured AKShare feeds when the legacy sentiment endpoint lacks RSS samples."""
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
                content = _safe_str(row.get(content_col)) if content_col else ""
                if label.endswith("研报直连"):
                    rating = _safe_str(row.get("东财评级"))
                    content = f"机构评级: {rating}" if rating else ""
                items.append({
                    "title": _safe_str(row.get(title_col)),
                    "content": content,
                    "date_str": published.date().isoformat() if published else None,
                    "source": _safe_str(row.get(source_col)) or label,
                })
        except Exception as exc:
            errors.append(f"{label}: {exc}")

    deduped: list[dict] = []
    seen: set[str] = set()
    for item in items:
        key = re.sub(r"\s+", "", f"{item.get('title', '')}|{item.get('date_str', '')}").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped, errors

_SENTIMENT_NEGATIVE = {
    '下跌', '暴跌', '亏损', '下滑', '减持', '套牢', '风险',
    '警告', '违规', '处罚', '退市', '爆雷', '缩水', '承压',
    '低迷', '恶化', '诉讼', '违约', '暴雷', '跌停', '腰斩', '清仓',
    '解禁', '停牌', '摘牌', '造假', '欺诈', '质疑', '压力', '下行',
    '净流出', '流出', '撤离', '利空', '拖累', '受挫', '遇阻', '受阻',
    '负增长', '大幅下滑', '大幅下跌', '不及预期',
}

_SENTIMENT_AMPLIFIER = {
    '大幅', '暴涨', '暴跌', '严重', '超预期', '远超', '显著', '急剧',
    '远低于', '远高于',
}

def _classify_sentiment(text: str) -> float:
    """Classify sentiment of a single text. Returns score in [-1, 1]."""
    try:
        import jieba
    except ImportError:
        jieba = None

    if jieba is not None:
        words = list(jieba.cut(text))
    else:
        words = [text[i:i+2] for i in range(len(text)-1)]

    pos = sum(1 for w in words if w in _SENTIMENT_POSITIVE)
    neg = sum(1 for w in words if w in _SENTIMENT_NEGATIVE)
    amp = sum(1 for w in words if w in _SENTIMENT_AMPLIFIER)

    total = pos + neg
    if total == 0:
        return 0.0  # neutral

    raw = (pos - neg) / total
    if amp > 0:
        raw = max(-1.0, min(1.0, raw * 1.3))
    return raw


def _fetch_sentiment(symbol: str, days: int) -> dict:
    """分析市场对某股票的情绪倾向。

    数据源: RSSHub 聚合财经资讯 + 个股研报
    方法: 中文分词 + 金融情绪词典匹配
    """
    import time as _time

    t0 = _time.time()
    code = _normalize_symbol(symbol)
    errors: list[str] = []
    keywords = _rss_stock_keywords(code)
    feed_specs = _rss_stock_feed_specs(keywords) + [
        ("eastmoney_report", {"category": "stock"}, "东方财富个股研报", True),
        ("wkjyqh_research", {}, "五矿期货研究", True),
    ]
    entries, _, fetch_errors = _fetch_rsshub_entries(
        code,
        days,
        feed_specs,
        keywords=keywords,
        limit=100,
        max_workers=10,
    )
    errors.extend(fetch_errors)

    unique_items = []
    for entry in _dedupe_rss_entries(entries):
        pub_time = _rss_entry_date(entry)
        unique_items.append({
            "title": _rss_entry_text(entry),
            "content": _rss_entry_summary(entry),
            "date_str": pub_time.date().isoformat() if pub_time else None,
            "source": _safe_str(entry.get("_rss_source_label") or entry.get("author") or "RSSHub"),
        })

    if len(unique_items) < 5:
        direct_items, direct_errors = _fetch_direct_sentiment_sources(code, days)
        errors.extend(direct_errors)
        existing_keys = {
            re.sub(r"\s+", "", f"{item.get('title', '')}|{item.get('date_str', '')}").lower()
            for item in unique_items
        }
        for item in direct_items:
            key = re.sub(r"\s+", "", f"{item.get('title', '')}|{item.get('date_str', '')}").lower()
            if key and key not in existing_keys:
                existing_keys.add(key)
                unique_items.append(item)

    # 情感分析
    positive_count = 0
    negative_count = 0
    neutral_count = 0
    daily_counts: dict[str, dict] = {}
    sentiment_items: list[dict] = []

    for item in unique_items:
        combined = (item.get("title") or "") + " " + (item.get("content") or "")
        score = _classify_sentiment(combined)

        if score > 0.01:
            label = "positive"
            positive_count += 1
        elif score < -0.01:
            label = "negative"
            negative_count += 1
        else:
            label = "neutral"
            neutral_count += 1

        # Date for trend
        date_str = item.get("date_str")  # use pre-extracted date
        if not date_str:
            for part in [item.get("title") or "", item.get("content") or ""]:
                m = re.search(r'(\d{4}-\d{2}-\d{2})', part)
                if m:
                    date_str = m.group(1)
                    break

        if date_str:
            daily = daily_counts.setdefault(date_str, {"total": 0, "positive": 0, "negative": 0, "neutral": 0})
            daily["total"] += 1
            daily[label] += 1

        sentiment_items.append({
            "title": item.get("title", ""),
            "sentiment_score": round(score, 3),
            "label": label,
            "source": item.get("source", ""),
            **_classify_financial_text(combined),
        })

    # Overall sentiment score (-100 to +100)
    total_classified = positive_count + negative_count + neutral_count
    if total_classified > 0:
        overall = ((positive_count - negative_count) / total_classified) * 100
    else:
        overall = 0.0

    daily_trend = [
        {"date": k, "total": v["total"], "positive": v["positive"],
         "negative": v["negative"], "neutral": v["neutral"]}
        for k, v in sorted(daily_counts.items())
    ]

    # Top keywords
    try:
        import jieba
        from collections import Counter
        all_text = " ".join(i.get("title") or "" for i in unique_items)
        stop_words = {
            '的', '了', '是', '在', '和', '与', '或', '但', '而', '对', '于',
            '中', '上', '下', '到', '将', '以', '被', '由', '从', '向', '个',
            '年', '月', '日', '一', '这', '那', '他', '她', '它', '们',
            '等', '并', '各', '已', '仍', '再', '又', '也', '还',
        }
        words = [w for w in jieba.cut(all_text) if len(w) >= 2 and w not in stop_words]
        top_keywords = [w for w, _ in Counter(words).most_common(20)]
    except Exception:
        top_keywords = []

    logger.info(f"[Sentiment] total {_time.time() - t0:.1f}s for {code}: "
                f"{len(unique_items)} items, score={overall:.1f}")
    latest_time = _latest_content_time(unique_items, ["date_str"])

    return {
        "symbol": code,
        "days": days,
        "sentiment_score": round(overall, 1),
        "positive_count": positive_count,
        "negative_count": negative_count,
        "neutral_count": neutral_count,
        "daily_trend": daily_trend,
        "top_keywords": top_keywords,
        "items": sentiment_items[:50],
        "analysis": _build_structured_analysis(sentiment_items, days=days, dimension="舆情情绪"),
        "errors": errors,
        "data_time": latest_time,
        "is_stale": _content_is_stale(unique_items, days, ["date_str"]),
        "fallback_used": bool(errors),
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
    }
