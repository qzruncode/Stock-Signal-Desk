# -*- coding: utf-8 -*-
"""Financial text classification, sentiment, and content freshness analysis."""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Optional

from api.v1.endpoints.financials._symbol import _safe_str, _parse_date

logger = logging.getLogger(__name__)


def _lazy_classify_sentiment(text: str) -> float:
    from ._fetch_sentiment import _classify_sentiment
    return _classify_sentiment(text)


def _classify_financial_text(text: str) -> dict:
    normalized = _safe_str(text)
    event_rules = [
        ("earnings", "业绩", ("业绩", "净利润", "营收", "年报", "半年报", "季报", "预告", "快报", "盈利", "亏损")),
        ("capital_action", "资本动作", ("增发", "定增", "发行", "并购", "收购", "重组", "资产", "投资", "募资")),
        ("shareholder", "股东变化", ("股东", "增持", "减持", "回购", "质押", "解押")),
        ("governance", "治理变动", ("董事", "监事", "高管", "总经理", "财务总监", "辞职", "聘任", "变更")),
        ("risk", "风险监管", ("问询", "监管", "处罚", "诉讼", "仲裁", "违规", "退市", "立案", "风险")),
        ("market", "市场交易", ("涨停", "跌停", "大宗交易", "龙虎榜", "主力资金", "净流入", "净流出")),
        ("research", "研究评级", ("研报", "评级", "买入", "增持", "中性", "减持", "卖出", "盈利预测", "目标价")),
        ("industry", "行业主题", ("行业", "板块", "景气", "周期", "需求", "供给", "价格")),
    ]
    event_type = "general"
    event_label = "一般资讯"
    tags: list[str] = []
    for key, label, words in event_rules:
        matched = [word for word in words if word in normalized]
        if matched:
            if event_type == "general":
                event_type = key
                event_label = label
            tags.extend(matched[:3])

    score = _lazy_classify_sentiment(normalized) if normalized else 0.0
    polarity = "positive" if score > 0.01 else ("negative" if score < -0.01 else "neutral")
    high_words = ("重大", "终止", "停牌", "复牌", "退市", "处罚", "立案", "亏损", "预增", "预减", "收购", "重组", "分红", "回购")
    medium_words = ("公告", "业绩", "评级", "增持", "减持", "资金", "问询", "诉讼", "投资")
    importance = "high" if any(word in normalized for word in high_words) else (
        "medium" if any(word in normalized for word in medium_words) else "low"
    )
    return {
        "event_type": event_type,
        "event_label": event_label,
        "polarity": polarity,
        "sentiment_score": round(score, 3),
        "importance": importance,
        "tags": list(dict.fromkeys(tags))[:8],
    }


def _build_structured_analysis(items: list[dict], *, days: int, dimension: str, source_key: str = "source") -> dict:
    source_counter = Counter(_safe_str(item.get(source_key)) or "未知" for item in items)
    notice_type_counter = Counter(_safe_str(item.get("notice_type")) or "未分类" for item in items if _safe_str(item.get("notice_type")))
    event_counter = Counter(_safe_str(item.get("event_type")) or _safe_str(item.get("notice_type")) or _safe_str(item.get("category")) or "general" for item in items)
    polarity_counter = Counter(_safe_str(item.get("polarity") or item.get("label")) or "neutral" for item in items)
    importance_counter = Counter(_safe_str(item.get("importance")) or "low" for item in items)
    daily_counter: dict[str, int] = defaultdict(int)
    for item in items:
        date_value = (
            _safe_str(item.get("publish_date"))
            or _safe_str(item.get("publish_time"))[:10]
            or _safe_str(item.get("date_str"))
        )
        if date_value:
            daily_counter[date_value[:10]] += 1

    key_events = [
        {
            "title": item.get("title", ""),
            "date": item.get("publish_date") or _safe_str(item.get("publish_time"))[:10] or item.get("date_str"),
            "source": item.get(source_key) or item.get("org") or "未知",
            "event_type": item.get("event_type") or item.get("notice_type") or item.get("category") or "general",
            "polarity": item.get("polarity") or item.get("label") or "neutral",
            "importance": item.get("importance") or "low",
            "tags": item.get("tags", []),
        }
        for item in items
        if item.get("importance") in ("high", "medium")
    ][:12]

    coverage_level = "none"
    if len(items) >= 20:
        coverage_level = "good"
    elif len(items) >= 8:
        coverage_level = "fair"
    elif items:
        coverage_level = "thin"

    return {
        "dimension": dimension,
        "data_quality": {
            "item_count": len(items),
            "source_count": len(source_counter),
            "days": days,
            "coverage_level": coverage_level,
            "proxy_item_count": sum(1 for item in items if item.get("is_proxy")),
        },
        "source_distribution": dict(source_counter.most_common()),
        "notice_type_distribution": dict(notice_type_counter.most_common()),
        "event_distribution": dict(event_counter.most_common()),
        "polarity_distribution": dict(polarity_counter.most_common()),
        "importance_distribution": dict(importance_counter.most_common()),
        "daily_distribution": dict(sorted(daily_counter.items())),
        "key_events": key_events,
        "ai_summary_hints": [
            f"{dimension}覆盖度: {coverage_level}, 共{len(items)}条, 来源{len(source_counter)}个",
            f"主要事件类型: {', '.join([k for k, _ in event_counter.most_common(3)]) or '无'}",
            f"情绪分布: {dict(polarity_counter.most_common())}",
        ],
    }


def _latest_content_time(items: list[dict], keys: list[str]) -> str | None:
    latest: datetime | None = None
    for item in items:
        for key in keys:
            raw = item.get(key)
            if not raw:
                continue
            parsed = _parse_date(raw)
            if parsed is not None and (latest is None or parsed > latest):
                latest = parsed
                break
    return latest.isoformat() if latest is not None else None


def _content_is_stale(items: list[dict], max_age_days: int, keys: list[str]) -> bool:
    latest = _latest_content_time(items, keys)
    if not latest:
        return True
    parsed = _parse_date(latest)
    if parsed is None:
        return True
    return parsed < (datetime.now() - timedelta(days=max_age_days))


def _resolve_post_publish_time(update_time: str, now: datetime | None = None) -> datetime | None:
    if now is None:
        now = datetime.now()
    date_match = re.match(r'(\d{2})-(\d{2})\s+(\d{2}):(\d{2})', update_time)
    if date_match:
        month, day, hour, minute = date_match.groups()
        try:
            pub_dt = datetime(now.year, int(month), int(day), int(hour), int(minute))
        except ValueError:
            return None
        if pub_dt - now > timedelta(hours=12):
            pub_dt = pub_dt.replace(year=now.year - 1)
        return pub_dt
    return _parse_date(update_time)