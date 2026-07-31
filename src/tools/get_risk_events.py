"""Company disclosure evidence for model-based risk review.

This tool retrieves evidence and preserves provenance. It deliberately does
not classify risk categories, severity or lifecycle state from phrase lists.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from src.tools._akshare import bare_symbol
from src.tools.base import ToolSpec, object_schema


def _date_value(item: dict[str, Any]) -> str | None:
    value = str(item.get("published") or item.get("publish_date") or item.get("date") or "").strip()
    return value[:10] or None


def _evidence_item(
    item: dict[str, Any],
    *,
    source_type: str,
) -> dict[str, Any] | None:
    title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
    summary = re.sub(
        r"\s+",
        " ",
        str(item.get("summary") or item.get("source_notice_type") or ""),
    ).strip()
    if not title and not summary:
        return None
    return {
        "title": title,
        "date": _date_value(item),
        "source": item.get("source") or ("公司公告" if source_type == "announcement" else "新闻"),
        "source_type": source_type,
        "url": item.get("url") or item.get("link") or "",
        "summary": summary[:700] or None,
        "evidence_basis": "title_and_available_summary",
        "semantic_status": "model_required",
        "requires_fulltext_verification": True,
    }


def get_risk_events(
    symbol: str,
    days: int = 90,
    limit: int = 30,
) -> dict[str, Any]:
    code = bare_symbol(symbol)
    if len(code) != 6 or not code.isdecimal():
        raise ValueError(f"无法识别 A 股代码: {symbol}")
    days = int(days)
    limit = int(limit)
    if not 1 <= days <= 730:
        raise ValueError("days 必须在 1 到 730 之间")
    if not 1 <= limit <= 100:
        raise ValueError("limit 必须在 1 到 100 之间")

    from src.tools.get_announcements import get_announcements
    from src.tools.search_news import search_news

    news = search_news(
        code,
        days=min(days, 365),
        limit=min(max(limit * 2, 20), 50),
    )
    announcements = get_announcements(
        code,
        days=days,
        type="all",
        limit=min(max(limit * 2, 30), 100),
    )

    evidence: list[dict[str, Any]] = []
    for item in news.get("items") or []:
        normalized = _evidence_item(item, source_type="news")
        if normalized:
            evidence.append(normalized)
    for item in announcements.get("items") or []:
        normalized = _evidence_item(item, source_type="announcement")
        if normalized:
            evidence.append(normalized)

    # Prefer a primary announcement when the same disclosure is syndicated.
    # This is source-quality ordering, not semantic risk classification.
    evidence.sort(
        key=lambda item: (
            item.get("date") or "",
            1 if item.get("source_type") == "announcement" else 0,
        ),
        reverse=True,
    )
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in evidence:
        key = re.sub(
            r"\s+",
            "",
            f"{item.get('title', '')}|{item.get('date', '')}",
        ).casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    items = deduped[:limit]

    news_ok = bool(news.get("success"))
    announcements_ok = bool(announcements.get("success"))
    acquisition_succeeded = news_ok or announcements_ok
    partial = acquisition_succeeded and not (news_ok and announcements_ok)
    errors = list(
        dict.fromkeys(
            [
                *[str(error) for error in news.get("errors") or []],
                *[str(error) for error in announcements.get("errors") or []],
            ]
        )
    )
    warnings = list(
        dict.fromkeys(
            [
                *[str(warning) for warning in news.get("warnings") or []],
                *[str(warning) for warning in announcements.get("warnings") or []],
            ]
        )
    )
    if not items and acquisition_succeeded:
        warnings.append(f"最近 {days} 天的数据源未返回可用的新闻或公告证据")

    latest = next((item.get("date") for item in items if item.get("date")), None)
    name = announcements.get("name") or news.get("name")
    return {
        "symbol": code,
        "name": name,
        "days": days,
        "limit": limit,
        "items": items,
        "item_count": len(items),
        "has_risk_events": None,
        "analysis": {
            "semantic_status": "model_required",
            "classification_method": None,
            "risk_categories": None,
            "severity_distribution": None,
            "lifecycle_status": None,
            "coverage": {
                "requested_days": days,
                "news_coverage_days": min(days, 365),
                "announcement_coverage_days": days,
                "news_sample_count": len(news.get("items") or []),
                "announcement_sample_count": len(announcements.get("items") or []),
            },
        },
        "source": "search_news + get_announcements",
        "source_chain": list(
            dict.fromkeys(
                [
                    *([str(news.get("source"))] if news.get("source") else []),
                    *[str(source) for source in announcements.get("source_chain") or []],
                ]
            )
        ),
        "source_scope": ("retrieved_news_and_formal_announcements_for_model_risk_review"),
        "success": acquisition_succeeded,
        "partial": partial,
        "data_time": latest,
        "retrieved_at": datetime.now().astimezone().isoformat(),
        "is_stale": False if latest else None,
        "freshness_unknown": latest is None,
        "fallback_used": bool(news.get("fallback_used") or announcements.get("fallback_used")),
        "fallback_recommended": not acquisition_succeeded,
        "errors": errors[:10],
        "warnings": warnings[:10],
    }


TOOL = ToolSpec(
    name="get_risk_events",
    description=(
        "获取公司的新闻和正式公告，保留时间、来源、链接与摘要，供模型研判风险类别、"
        "严重度和事项生命周期。工具本身不使用关键词词典给风险下结论。"
    ),
    parameters=object_schema(
        {
            "symbol": {"type": "string", "description": "A 股代码或名称"},
            "days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 730,
                "default": 90,
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 30,
            },
        },
        ["symbol"],
    ),
    executor=get_risk_events,
    category="risk",
)
