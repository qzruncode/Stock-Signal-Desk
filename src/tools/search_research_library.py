"""Cross-institution research search over the complete RSSHub instance."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any

from src.tools.rss_source_resolver import resolve_rss_source_specs


def _semantic_terms(subjects: list[str]) -> list[str]:
    """Normalize caller-supplied subjects without interpreting user prose."""
    return list(dict.fromkeys(str(subject).strip().lower() for subject in subjects if len(str(subject).strip()) >= 2))


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        return None


def search_research_library(
    query: str,
    category: str = "",
    subjects: list[str] | None = None,
    futures_type: str | None = None,
    days: int = 365,
    limit: int = 12,
    include_content: bool = False,
    fallback_to_web: bool = True,
) -> dict[str, Any]:
    from api.v1.endpoints._rss_catalog import get_rss_catalog
    from api.v1.endpoints._rss_reader import read_feed
    from src.tools._rss_agent import rss_item_ref
    from src.tools.read_rss_item import read_rss_item

    query = str(query or "").strip()
    category = str(category or "").strip().lower()
    days, limit = int(days), int(limit)
    if not query:
        raise ValueError("query 不能为空")
    if category not in {"industry", "macro", "futures", "rating"}:
        raise ValueError(f"不支持的 category: {category}")
    subject_terms = _semantic_terms(subjects or [])
    if not subject_terms:
        raise ValueError("subjects 必须提供至少一个语义主题")
    if not 1 <= days <= 3650 or not 1 <= limit <= 30:
        raise ValueError("days 必须为 1..3650，limit 必须为 1..30")
    resolved = category
    catalog = get_rss_catalog(force=False, scope="finance")
    catalog_routes = [
        route
        for route in catalog.get("routes") or []
        if isinstance(route, dict)
    ]
    resolver_subjects = [
        *(subjects or []),
        *([str(futures_type)] if futures_type else []),
    ]
    specs = resolve_rss_source_specs(
        catalog_routes,
        information_need=resolved,
        query=query,
        subjects=resolver_subjects,
        max_sources=max(2, min(6, limit // 4 + 1)),
    )
    cutoff = datetime.now() - timedelta(days=days)
    errors: list[str] = []
    warnings: list[str] = []
    coverage: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []

    def fetch(spec: tuple[str, dict[str, str], str]):
        route_path, params, source_name = spec
        return spec, read_feed(
            route_path,
            params,
            limit=50,
        )

    with ThreadPoolExecutor(max_workers=min(4, len(specs) or 1)) as pool:
        futures = [pool.submit(fetch, spec) for spec in specs]
        for future in as_completed(futures):
            try:
                spec, result = future.result()
            except Exception as exc:
                errors.append(f"研究源读取失败: {exc}")
                continue
            route_path, params, source_name = spec
            route_errors = [
                str(error) for error in result.get("errors") or []
            ]
            errors.extend(
                f"{source_name}: {error}" for error in route_errors
            )
            coverage.append(
                {
                    "source": source_name,
                    "route_path": route_path,
                    "params": params,
                    "item_count": len(result.get("items") or []),
                    "success": not route_errors,
                    "cached": bool(result.get("_cached")),
                    "errors": route_errors,
                }
            )
            for raw in result.get("items") or []:
                title = re.sub(r"\s+", " ", str(raw.get("title") or "")).strip()
                link = str(raw.get("link") or "").strip()
                if not title or not link:
                    continue
                published = _parse_time(raw.get("published"))
                if published and published < cutoff:
                    continue
                summary = re.sub(r"\s+", " ", str(raw.get("summary") or "")).strip()
                text = f"{title} {summary}".lower()
                candidates.append(
                    {
                        "title": title,
                        "published": raw.get("published"),
                        "summary": summary,
                        "link": link,
                        "author": raw.get("author") or raw.get("source"),
                        "source": source_name,
                        "source_type": "institutional_research_rss",
                        "research_category": resolved,
                        "exact_subject_mentions": [term for term in subject_terms if term in text],
                        "semantic_status": "model_required",
                        "rss_route": route_path,
                        "rss_params": params,
                        "item_ref": (
                            raw.get("item_ref")
                            or rss_item_ref(
                                route_path=route_path,
                                params=params,
                                options={},
                                namespace="",
                                item=dict(raw),
                            )
                        ),
                        "attachments": raw.get("attachments") or [],
                        "content_html": raw.get("content_html") or "",
                    }
                )

    candidates.sort(
        key=lambda row: (
            len(row.get("exact_subject_mentions") or []),
            str(row.get("published") or ""),
        ),
        reverse=True,
    )
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        key = re.sub(r"\s+", "", item["title"]).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    items = deduped[:limit]

    if include_content:
        for item in items[:3]:
            try:
                detail = read_rss_item(
                    item_ref=item.get("item_ref") or {},
                    item=item,
                    include_documents=True,
                )
                item["content_text"] = detail.get("content_text") or item["summary"]
                item["content_chunks"] = detail.get("content_chunks") or []
                item["resources"] = detail.get("resources") or []
                item["content_fallback"] = False
            except Exception as exc:
                warnings.append(f"{item['title'][:40]} 正文读取失败: {exc}")

    fallback_attempted = False
    fallback_used = False
    web_fallback = None
    if not items and fallback_to_web:
        from src.tools.websearch import websearch

        fallback_attempted = True
        web_fallback = websearch(query, num_results=min(limit, 10))
        for raw in web_fallback.get("results") or []:
            if raw.get("title") and raw.get("url"):
                items.append(
                    {
                        "title": raw["title"],
                        "published": raw.get("published_date"),
                        "summary": raw.get("snippet") or "",
                        "link": raw["url"],
                        "author": raw.get("source"),
                        "source": raw.get("source"),
                        "source_type": "websearch",
                        "research_category": resolved,
                    }
                )
        items = items[:limit]
        fallback_used = bool(items)
        if fallback_used:
            warnings.append("专门研究源没有返回时间窗内报告，已使用原始查询进行通用联网搜索")

    successful_sources = sum(bool(row["success"]) for row in coverage)
    acquisition_success = successful_sources > 0
    if acquisition_success and not items and not fallback_used:
        warnings.append("研究源读取成功，但时间窗内没有可用报告")
    known_times = [value for value in (_parse_time(item.get("published")) for item in items) if value]
    latest = max(known_times, default=None)
    retrieved_at = datetime.now().astimezone().isoformat()
    return {
        "query": query,
        "requested_category": category,
        "research_category": resolved,
        "days": days,
        "items": items,
        "item_count": len(items),
        "source_coverage": coverage,
        "rss_catalog_count": catalog.get("count"),
        "coverage": {
            "planned_sources": len(specs),
            "attempted_sources": len(specs),
            "successful_sources": successful_sources,
            "item_count": len(items),
            "text_documents_found": sum(
                len(item.get("attachments") or [])
                for item in items
            ),
            "text_documents_extracted": sum(
                len(item.get("resources") or [])
                for item in items
            ),
            "discarded_non_text": 0,
            "failures": list(dict.fromkeys(errors))[:10],
        },
        "source": (
            f"websearch/{(web_fallback or {}).get('provider', 'unknown')}"
            if fallback_used
            else "Infos/RSSHub研究资料库" if acquisition_success else "none"
        ),
        "success": acquisition_success or fallback_used,
        "partial": bool(items) and bool(errors),
        "data_time": (
            latest.astimezone().isoformat() if latest and latest.tzinfo else latest.isoformat() if latest else None
        ),
        "retrieved_at": retrieved_at,
        "is_stale": latest < datetime.now() - timedelta(days=max(120, days)) if latest else None,
        "freshness_unknown": latest is None,
        "fallback_attempted": fallback_attempted,
        "fallback_used": fallback_used,
        "fallback_recommended": not (acquisition_success or fallback_used),
        "web_fallback": web_fallback,
        "errors": list(dict.fromkeys(errors))[:10],
        "warnings": list(dict.fromkeys(warnings))[:10],
    }


__all__ = ["search_research_library"]
