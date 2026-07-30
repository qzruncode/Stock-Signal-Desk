# -*- coding: utf-8 -*-
"""Collect a complete, stock-scoped evidence dossier for one theme analysis."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Callable

from src.tools._akshare import bare_symbol
from src.tools.base import ToolSpec, object_schema


DESCRIPTION = (
    "为一只A股独立收集主题业务分析证据。逐股读取公司资料、主营构成、正式公告、"
    "公司新闻和个股研报，不接受股票集合，也不从行业新闻发现其他公司。只有该股票的"
    "项目数据源全部没有形成可分析资料时，才按该公司和目标主题单独使用公开网络兜底。"
)

_MAX_DOCUMENTS = 80
_MAX_DOCUMENT_CHARACTERS = 42_000


def _clean(value: Any, limit: int = 2_000) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _json_line(value: dict[str, Any], fields: tuple[str, ...]) -> str:
    return "；".join(
        f"{field}={_clean(value.get(field), 500)}"
        for field in fields
        if value.get(field) not in (None, "", [], {})
    )


def _append_document(
    documents: list[dict[str, Any]],
    *,
    source_type: str,
    title: str,
    text: str,
    source_name: str,
    source_url: str = "",
    source_date: str = "",
    official: bool = False,
) -> None:
    if len(documents) >= _MAX_DOCUMENTS:
        return
    used = sum(len(str(item.get("text") or "")) for item in documents)
    remaining = _MAX_DOCUMENT_CHARACTERS - used
    normalized_text = _clean(text, min(6_000, max(0, remaining)))
    if not normalized_text:
        return
    documents.append({
        "source_id": f"s{len(documents) + 1}",
        "source_type": source_type,
        "title": _clean(title, 300),
        "text": normalized_text,
        "source_name": _clean(source_name, 120),
        "source_url": str(source_url or "").strip(),
        "source_date": str(source_date or "").strip()[:10],
        "official": bool(official),
    })


def _profile_documents(
    result: dict[str, Any],
    name: str,
) -> list[dict[str, Any]]:
    if not any(
        result.get(field)
        for field in (
            "company_name",
            "short_name",
            "industry",
            "industry_eastmoney",
            "main_business",
            "business_scope",
            "company_profile",
        )
    ):
        return []
    lines = [
        f"公司名称={result.get('company_name') or name}",
        f"证券简称={result.get('short_name') or name}",
        f"所属行业={result.get('industry') or result.get('industry_eastmoney') or ''}",
        f"主营业务={result.get('main_business') or ''}",
        f"经营范围={result.get('business_scope') or ''}",
        f"公司简介={result.get('company_profile') or ''}",
    ]
    text = "\n".join(line for line in lines if not line.endswith("="))
    documents: list[dict[str, Any]] = []
    _append_document(
        documents,
        source_type="company_profile",
        title=f"{name}公司资料",
        text=text,
        source_name=str(result.get("source") or "巨潮资讯公司概况"),
        source_url="",
        source_date=str(result.get("data_time") or ""),
        official=True,
    )
    return documents


def _segment_documents(
    result: dict[str, Any],
    name: str,
) -> list[dict[str, Any]]:
    rows = [
        _json_line(
            item,
            (
                "report_date",
                "category",
                "segment_name",
                "revenue",
                "revenue_share_pct",
                "gross_profit",
                "gross_profit_share_pct",
                "gross_margin_pct",
            ),
        )
        for item in result.get("items") or []
        if isinstance(item, dict)
    ]
    if not rows:
        return []
    documents: list[dict[str, Any]] = []
    _append_document(
        documents,
        source_type="business_segments",
        title=f"{name}主营构成",
        text=f"公司={name}\n" + "\n".join(rows),
        source_name=str(result.get("source") or "公司主营构成"),
        source_url=str(result.get("source_url") or ""),
        source_date=str(result.get("data_time") or ""),
        official=True,
    )
    return documents


def _item_documents(
    result: dict[str, Any],
    *,
    source_type: str,
    name: str,
    official: bool,
    max_items: int,
) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for item in (result.get("items") or [])[:max_items]:
        if not isinstance(item, dict):
            continue
        title = _clean(item.get("title"), 300)
        body = _clean(
            item.get("summary")
            or item.get("content_text")
            or item.get("content"),
            2_500,
        )
        forecasts = item.get("profit_forecasts")
        forecast_text = (
            json.dumps(forecasts, ensure_ascii=False, default=str)
            if forecasts
            else ""
        )
        text = "\n".join(
            value
            for value in (
                f"公司={name}",
                f"标题={title}" if title else "",
                f"正文或摘要={body}" if body else "",
                f"机构={_clean(item.get('org'), 120)}" if item.get("org") else "",
                f"评级={_clean(item.get('rating'), 80)}" if item.get("rating") else "",
                f"盈利预测={forecast_text}" if forecast_text else "",
            )
            if value
        )
        if not title and not body:
            continue
        _append_document(
            documents,
            source_type=source_type,
            title=title or f"{name}{source_type}",
            text=text,
            source_name=str(item.get("source") or result.get("source") or source_type),
            source_url=str(item.get("url") or item.get("link") or ""),
            source_date=str(
                item.get("publish_date")
                or item.get("published")
                or result.get("data_time")
                or ""
            ),
            official=official,
        )
    return documents


def _fallback_documents(
    result: dict[str, Any],
    name: str,
) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for item in result.get("results") or []:
        if not isinstance(item, dict):
            continue
        title = _clean(item.get("title"), 300)
        body = _clean(item.get("snippet") or item.get("content"), 2_500)
        _append_document(
            documents,
            source_type="public_web_fallback",
            title=title or f"{name}公开资料",
            text="\n".join(
                value
                for value in (
                    f"公司={name}",
                    f"标题={title}" if title else "",
                    f"正文或摘要={body}" if body else "",
                )
                if value
            ),
            source_name=str(item.get("source") or result.get("provider") or "公开网络"),
            source_url=str(item.get("url") or ""),
            source_date=str(item.get("published_date") or ""),
        )
    return documents


def get_company_theme_evidence(
    symbol: str,
    company_name: str = "",
    target_topics: list[str] | None = None,
    domains: list[str] | None = None,
    objective: str = "",
    days: int = 365,
) -> dict[str, Any]:
    code = bare_symbol(symbol)
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("symbol 必须能解析为单个 6 位 A 股代码")
    clean_topics = list(dict.fromkeys(
        _clean(value, 120)
        for value in target_topics or []
        if _clean(value, 120)
    ))
    clean_domains = list(dict.fromkeys(
        _clean(value, 120)
        for value in domains or []
        if _clean(value, 120)
    ))
    days = max(30, min(int(days), 730))

    from src.data.stock_index_loader import get_index_stock_name
    from src.tools.get_announcements import get_announcements
    from src.tools.get_business_segments import get_business_segments
    from src.tools.get_research_report import get_research_report
    from src.tools.get_stock_info import get_stock_info
    from src.tools.search_news import search_news

    resolved_name = _clean(company_name, 120) or _clean(
        get_index_stock_name(code),
        120,
    ) or code
    source_calls: dict[str, tuple[Callable[..., dict[str, Any]], dict[str, Any]]] = {
        "company_profile": (get_stock_info, {"symbol": code}),
        "business_segments": (
            get_business_segments,
            {"symbol": code, "category": "all", "periods": 3},
        ),
        "announcements": (
            get_announcements,
            {"symbol": code, "days": days, "type": "all", "limit": 60},
        ),
        "company_news": (
            search_news,
            {"symbol": code, "days": min(days, 365), "limit": 40},
        ),
        "stock_research": (
            get_research_report,
            {"symbol": code, "days": days, "limit": 40},
        ),
    }
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=len(source_calls)) as pool:
        futures = {
            pool.submit(function, **arguments): source_type
            for source_type, (function, arguments) in source_calls.items()
        }
        for future in as_completed(futures):
            source_type = futures[future]
            try:
                value = future.result()
                results[source_type] = value if isinstance(value, dict) else {
                    "success": False,
                    "errors": ["项目工具返回值不是对象"],
                }
            except Exception as exc:
                results[source_type] = {
                    "success": False,
                    "errors": [f"{type(exc).__name__}: {exc}"],
                }

    documents: list[dict[str, Any]] = []
    documents.extend(_profile_documents(
        results.get("company_profile") or {},
        resolved_name,
    ))
    documents.extend(_segment_documents(
        results.get("business_segments") or {},
        resolved_name,
    ))
    documents.extend(_item_documents(
        results.get("announcements") or {},
        source_type="announcement",
        name=resolved_name,
        official=True,
        max_items=30,
    ))
    documents.extend(_item_documents(
        results.get("company_news") or {},
        source_type="company_news",
        name=resolved_name,
        official=False,
        max_items=30,
    ))
    documents.extend(_item_documents(
        results.get("stock_research") or {},
        source_type="stock_research",
        name=resolved_name,
        official=False,
        max_items=18,
    ))

    fallback_attempted = not bool(documents)
    fallback_used = False
    fallback_errors: list[str] = []
    if fallback_attempted:
        from src.tools.websearch import websearch

        query_parts = [
            resolved_name,
            code,
            *clean_topics,
            *clean_domains,
            _clean(objective, 400),
        ]
        try:
            fallback = websearch(
                query=" ".join(query_parts),
                num_results=8,
                livecrawl="fallback",
                search_type="auto",
                context_max_characters=12_000,
            )
            fallback_documents = _fallback_documents(fallback, resolved_name)
            documents.extend(fallback_documents)
            fallback_used = bool(fallback_documents)
            fallback_errors.extend(
                str(error)
                for error in fallback.get("errors") or []
                if error
            )
        except Exception as exc:
            fallback_errors.append(f"{type(exc).__name__}: {exc}")

    documents = documents[:_MAX_DOCUMENTS]
    source_status = {
        source_type: {
            "success": value.get("success") is True,
            "partial": bool(value.get("partial")),
            "item_count": int(value.get("item_count") or 0),
            "errors": [
                str(error)
                for error in value.get("errors") or []
                if error
            ][:5],
        }
        for source_type, value in results.items()
    }
    failed_sources = [
        source_type
        for source_type, status in source_status.items()
        if not status["success"]
    ]
    now = datetime.now().astimezone().isoformat()
    errors = [
        f"{source_type}: {error}"
        for source_type, status in source_status.items()
        for error in status["errors"]
    ]
    errors.extend(f"public_web_fallback: {error}" for error in fallback_errors)
    return {
        "success": True,
        "partial": bool(documents) and bool(failed_sources or fallback_used),
        "symbol": code,
        "name": resolved_name,
        "target_topics": clean_topics,
        "domains": clean_domains,
        "analysis_unit": "single_security",
        "project_source_count": len(source_calls),
        "project_source_success_count": len(source_calls) - len(failed_sources),
        "project_source_coverage_complete": not failed_sources,
        "evidence_document_count": len(documents),
        "evidence_documents": documents,
        "fallback_attempted": fallback_attempted,
        "fallback_used": fallback_used,
        "source_status": source_status,
        "errors": list(dict.fromkeys(errors))[:20],
        "warnings": (
            ["项目内公司资料全部未形成可分析文本，已按该公司单独使用公开网络兜底"]
            if fallback_used
            else []
        ),
        "data_time": now if documents else None,
        "is_stale": False if documents else None,
        "freshness_unknown": not bool(documents),
    }


TOOL = ToolSpec(
    name="get_company_theme_evidence",
    description=DESCRIPTION,
    parameters=object_schema(
        {
            "symbol": {
                "type": "string",
                "pattern": r"^\d{6}$",
                "description": "只允许一个 6 位 A 股代码",
            },
            "company_name": {
                "type": "string",
                "description": "本地证券库解析出的公司简称",
                "default": "",
            },
            "target_topics": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 8,
            },
            "domains": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 12,
            },
            "objective": {
                "type": "string",
                "maxLength": 400,
                "default": "",
            },
            "days": {
                "type": "integer",
                "minimum": 30,
                "maximum": 730,
                "default": 365,
            },
        },
        ["symbol", "target_topics", "domains"],
    ),
    executor=get_company_theme_evidence,
    category="research",
)


__all__ = ["TOOL", "get_company_theme_evidence"]
