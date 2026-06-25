# -*- coding: utf-8 -*-
"""Risk events: classify, extract, build risk event list."""
from __future__ import annotations

import logging
import re
import sys
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

from ._helpers import (
    _normalize_symbol, _safe_float, _safe_str, _parse_date,
    _rss_stock_keywords,
    _fetch_rsshub_entries, _rss_entry_text, _rss_entry_summary, _rss_entry_date,
    _dedupe_rss_entries, _build_structured_analysis,
    _latest_content_time, _content_is_stale,
)
from ._cache import _daily_cache_get, _daily_cache_put

# Late-bound reference for test monkey-patching compatibility
_pkg = sys.modules[__package__]

RISK_KEYWORDS: tuple[str, ...] = (
    "减持",
    "质押",
    "冻结",
    "诉讼",
    "仲裁",
    "问询函",
    "监管函",
    "立案",
    "处罚",
    "资产减值",
    "商誉减值",
    "业绩预告下修",
    "募投延期",
    "关联交易",
    "大额应收",
    "债务逾期",
    "担保",
    "退市风险",
)


def _classify_risk_event(text: str, *, source_kind: str) -> Optional[dict]:
    normalized = _safe_str(text)
    if not normalized:
        return None

    risk_rules = [
        (
            "regulatory",
            "监管处罚",
            {
                "high": ("立案", "处罚", "罚款", "通报批评", "调查", "违规"),
                "medium": ("监管函", "问询", "问询函", "警示函"),
            },
        ),
        (
            "litigation",
            "诉讼仲裁",
            {
                "high": ("冻结", "查封", "执行"),
                "medium": ("诉讼", "仲裁", "被告", "纠纷"),
            },
        ),
        (
            "delisting",
            "退市警示",
            {
                "high": ("退市", "*ST", "终止上市", "暂停上市"),
                "medium": ("风险警示", "ST"),
            },
        ),
        (
            "profit_warning",
            "业绩预警",
            {
                "high": ("预亏", "首亏", "亏损", "商誉减值", "大幅下滑"),
                "medium": ("下修", "减值", "资产减值"),
            },
        ),
        (
            "debt_cashflow",
            "债务现金流",
            {
                "high": ("违约", "无法偿还", "债务逾期", "现金流紧张"),
                "medium": ("债务", "流动性", "票据", "担保", "大额应收"),
            },
        ),
        (
            "pledge_reduction",
            "质押减持",
            {
                "high": ("平仓", "爆仓", "清仓", "被动减持"),
                "medium": ("质押", "减持"),
                "low": ("解除质押",),
            },
        ),
        (
            "governance",
            "治理异动",
            {
                "medium": ("失联", "无法保证", "非标", "保留意见", "否定意见"),
                "low": ("辞职", "更正", "内控", "内部控制"),
            },
        ),
        (
            "operation",
            "经营波动",
            {
                "high": ("事故", "失火", "安全生产"),
                "medium": ("停产", "停工", "召回", "环保"),
            },
        ),
    ]

    severity_rank = {"low": 1, "medium": 2, "high": 3}
    matched: Optional[dict[str, Any]] = None
    for category, label, severity_map in risk_rules:
        rule_hits: list[str] = []
        rule_severity: Optional[str] = None
        for severity in ("high", "medium", "low"):
            words = severity_map.get(severity, ())
            hits = [word for word in words if word in normalized]
            if hits:
                rule_hits.extend(hits)
                if rule_severity is None:
                    rule_severity = severity
        if rule_severity is None:
            continue
        candidate = {
            "risk_category": category,
            "risk_label": label,
            "severity": rule_severity,
            "tags": list(dict.fromkeys(rule_hits))[:5],
        }
        if matched is None or severity_rank[candidate["severity"]] > severity_rank[matched["severity"]]:
            matched = candidate

    if matched is None:
        return None

    return matched


def _match_risk_keywords(text: str) -> list[str]:
    normalized = _safe_str(text)
    return [kw for kw in RISK_KEYWORDS if kw in normalized]


def _extract_risk_summary(content: str, keywords: list[str]) -> str:
    normalized = _safe_str(content)
    if not normalized or not keywords:
        return ""
    sentences = re.split(r"[。！？；\n\r]+", normalized)
    matched = []
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if any(keyword in sentence for keyword in keywords):
            matched.append(sentence)
        if len(matched) >= 3:
            break
    return "；".join(matched)[:240]


def _fetch_risk_body_if_needed(url: str, title: str, matched_keywords: list[str]) -> tuple[str, str]:
    if not url or not matched_keywords:
        return "", ""
    try:
        from src.search_service import fetch_url_content

        content = fetch_url_content(url, timeout=8)
        summary = _extract_risk_summary(content, matched_keywords)
        return content[:1500], summary
    except Exception as exc:
        logger.warning("[RiskEvents] fetch body failed for %s: %s", title, exc)
        return "", ""


def _build_risk_events(symbol: str, days: int) -> dict:
    code = _normalize_symbol(symbol)
    news = _pkg._fetch_news(code, days, "all")
    announcements = _pkg._fetch_announcements(code, days, "all")

    items: list[dict] = []
    risk_counter: dict[str, int] = {}
    severity_counter = {"high": 0, "medium": 0, "low": 0}

    for item in news.get("items", []):
        title = _safe_str(item.get("title"))
        title_keywords = _match_risk_keywords(title)
        text = " ".join(
            filter(
                None,
                [
                    title,
                    _safe_str(item.get("summary")),
                    _safe_str(item.get("event_label")),
                    " ".join(item.get("tags", []) or []),
                ],
            )
        )
        risk = _classify_risk_event(text, source_kind="news")
        if not title_keywords and not risk:
            continue
        body_content, risk_summary = _fetch_risk_body_if_needed(item.get("url") or "", title, title_keywords)
        body_keywords = _match_risk_keywords(body_content)
        merged_keywords = list(dict.fromkeys(title_keywords + body_keywords + (risk["tags"] if risk else [])))
        risk = risk or _classify_risk_event(body_content, source_kind="news")
        if not risk:
            continue
        severity_counter[risk["severity"]] = severity_counter.get(risk["severity"], 0) + 1
        risk_counter[risk["risk_label"]] = risk_counter.get(risk["risk_label"], 0) + 1
        items.append({
            "title": title,
            "summary": item.get("summary"),
            "risk_summary": risk_summary or _extract_risk_summary(_safe_str(item.get("summary")), merged_keywords),
            "date": _safe_str(item.get("publish_time"))[:10] or None,
            "source": item.get("source") or "新闻",
            "source_type": "news",
            "url": item.get("url") or "",
            "severity": risk["severity"],
            "risk_category": risk["risk_category"],
            "risk_label": risk["risk_label"],
            "event_type": item.get("event_type") or "general",
            "tags": merged_keywords[:8],
        })

    for item in announcements.get("items", []):
        title = _safe_str(item.get("title"))
        title_keywords = _match_risk_keywords(title)
        text = " ".join(
            filter(
                None,
                [
                    _safe_str(item.get("notice_type")),
                    title,
                    _safe_str(item.get("event_label")),
                    " ".join(item.get("tags", []) or []),
                ],
            )
        )
        risk = _classify_risk_event(text, source_kind="announcement")
        if not title_keywords and not risk:
            continue
        body_content, risk_summary = _fetch_risk_body_if_needed(item.get("url") or "", title, title_keywords)
        body_keywords = _match_risk_keywords(body_content)
        merged_keywords = list(dict.fromkeys(title_keywords + body_keywords + (risk["tags"] if risk else [])))
        risk = risk or _classify_risk_event(body_content, source_kind="announcement")
        if not risk:
            continue
        severity_counter[risk["severity"]] = severity_counter.get(risk["severity"], 0) + 1
        risk_counter[risk["risk_label"]] = risk_counter.get(risk["risk_label"], 0) + 1
        items.append({
            "title": title,
            "summary": item.get("notice_type") or "",
            "risk_summary": risk_summary or _extract_risk_summary(_safe_str(item.get("notice_type")), merged_keywords),
            "date": item.get("publish_date"),
            "source": item.get("source") or "公司公告",
            "source_type": "announcement",
            "url": item.get("url") or "",
            "severity": risk["severity"],
            "risk_category": risk["risk_category"],
            "risk_label": risk["risk_label"],
            "event_type": item.get("event_type") or "announcement",
            "tags": merged_keywords[:8],
        })

    deduped: list[dict] = []
    seen: set[str] = set()
    for item in sorted(items, key=lambda row: row.get("date") or "", reverse=True):
        key = re.sub(r"\s+", "", f"{item.get('title', '')}|{item.get('date', '')}|{item.get('risk_category', '')}").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    top_risk_labels = [label for label, _ in sorted(risk_counter.items(), key=lambda pair: pair[1], reverse=True)[:5]]
    source_type_counter = {"news": 0, "announcement": 0}
    for item in deduped:
        source_type = _safe_str(item.get("source_type")).lower()
        if source_type in source_type_counter:
            source_type_counter[source_type] += 1

    analysis = {
        "total_events": len(deduped),
        "severity_distribution": severity_counter,
        "source_distribution": source_type_counter,
        "top_risk_labels": top_risk_labels,
        "high_severity_titles": [item["title"] for item in deduped if item.get("severity") == "high"][:8],
        "ai_summary_hints": [
            f"近{days}天共发现 {len(deduped)} 条风险线索",
            f"高风险事件 {severity_counter['high']} 条, 中风险事件 {severity_counter['medium']} 条",
            f"主要风险主题: {', '.join(top_risk_labels) or '暂无明显风险主题'}",
        ],
    }

    return {
        "symbol": code,
        "days": days,
        "items": deduped[:50],
        "analysis": analysis,
        "source_chain": list(dict.fromkeys((news.get("source_chain") or []) + (announcements.get("source_chain") or []))),
        "errors": list(dict.fromkeys((news.get("errors") or []) + (announcements.get("errors") or []))),
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
    }


