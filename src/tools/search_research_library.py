"""Cross-institution research search over the useful Infos RSS routes."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any

from src.tools.base import ToolSpec, object_schema


_NIFD_CATEGORIES = {
    "weekly": "7a6a826d-b525-42aa-b550-4236e524227f",
    "biweekly": "128d602c-7041-4546-beff-83e605f8a370",
    "monthly": "0712e220-fa3b-44d4-9226-bc3d57944e19",
    "quarterly": "b66aa691-87ee-4bfe-ac6b-2460386166ee",
    "annual": "c714853a-f09e-4510-8835-30a448fff7e3",
    "topic": "17d0b29b-7912-498a-b9c3-d30508220158",
    "academic": "e6a6d3a5-4bda-4739-9765-e4e41c900bcc",
    "working_paper": "3d23ba0e-4f46-44c2-9d21-6b38df4cdd70",
}

def _nanhua_spec(futures_type: str | None) -> dict[str, Any] | None:
    suffix = str(futures_type or "").strip()
    if not suffix:
        return None
    try:
        from api.v1.endpoints._nanhua_tree import get_nanhua_tree

        types = get_nanhua_tree(force=False).get("types") or []
        weekly = next((row for row in types if row.get("type") == "WEEK"), None)
        child = next((row for row in (weekly or {}).get("children") or [] if str(row.get("type") or "").endswith(suffix)), None)
        if child:
            return {"path": "/nanhua/report/:type1/:type2", "params": {"type1": "WEEK", "type2": str(child["type"])}, "source": "南华期货"}
    except Exception:
        return None
    return None


def _route_specs(category: str, futures_type: str | None = None) -> list[dict[str, Any]]:
    """Compile fixed source workflows from structured research parameters."""
    if category == "rating":
        return [{"path": "/moodysmismicrosite/report/:industry?", "params": {}, "source": "穆迪评级"}]
    if category == "futures":
        specs = [{"path": "/wkjyqh/research", "params": {}, "source": "五矿期货"}]
        nanhua = _nanhua_spec(futures_type)
        if nanhua:
            specs.append(nanhua)
        return specs
    if category == "macro":
        return [
            {"path": "/eastmoney/report/:category", "params": {"category": "macresearch"}, "source": "东方财富宏观研报"},
            {"path": "/mckinsey/cn/:category?", "params": {"category": "macroeconomy"}, "source": "麦肯锡"},
            {"path": "/moodysmismicrosite/report/:industry?", "params": {"industry": "宏观经济"}, "source": "穆迪评级"},
            {"path": "/nifd/research/:categoryGuid?", "params": {"categoryGuid": _NIFD_CATEGORIES["weekly"]}, "source": "国家金融与发展实验室"},
        ]
    return [
        {"path": "/eastmoney/report/:category", "params": {"category": "industry"}, "source": "东方财富行业研报"},
        {"path": "/qianzhan/analyst/column/:type?", "params": {"type": "all"}, "source": "前瞻研究"},
        {"path": "/cih-index/report/list/:report?", "params": {}, "source": "中指研究院"},
        {"path": "/mckinsey/cn/:category?", "params": {}, "source": "麦肯锡"},
    ]


def _semantic_terms(subjects: list[str]) -> list[str]:
    """Normalize Planner-supplied subjects without interpreting user prose."""
    return list(dict.fromkeys(
        str(subject).strip().lower()
        for subject in subjects
        if len(str(subject).strip()) >= 2
    ))


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
    from api.v1.endpoints._rss_reader import read_feed, read_item

    query = str(query or "").strip()
    category = str(category or "").strip().lower()
    days, limit = int(days), int(limit)
    if not query:
        raise ValueError("query 不能为空")
    if category not in {"industry", "macro", "futures", "rating"}:
        raise ValueError(f"不支持的 category: {category}")
    subject_terms = _semantic_terms(subjects or [])
    if not subject_terms:
        raise ValueError("subjects 必须由 Planner 提供至少一个语义主题")
    if not 1 <= days <= 3650 or not 1 <= limit <= 30:
        raise ValueError("days 必须为 1..3650，limit 必须为 1..30")
    resolved = category
    specs = _route_specs(resolved, futures_type)
    cutoff = datetime.now() - timedelta(days=days)
    terms = list(dict.fromkeys([query.lower(), *subject_terms]))
    errors: list[str] = []
    warnings: list[str] = []
    coverage: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []

    def fetch(spec: dict[str, Any]):
        return spec, read_feed(
            spec["path"], spec["params"], limit=50, fallback_to_xml=False,
        )

    with ThreadPoolExecutor(max_workers=min(4, len(specs) or 1)) as pool:
        futures = [pool.submit(fetch, spec) for spec in specs]
        for future in as_completed(futures):
            try:
                spec, result = future.result()
            except Exception as exc:
                errors.append(f"研究源读取失败: {exc}")
                continue
            route_errors = [str(error) for error in result.get("errors") or []]
            errors.extend(f"{spec['source']}: {error}" for error in route_errors)
            coverage.append({
                "source": spec["source"], "route_path": spec["path"], "params": spec["params"],
                "item_count": len(result.get("items") or []), "success": not route_errors,
                "cached": bool(result.get("_cached")), "errors": route_errors,
            })
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
                score = sum(5 if term in title.lower() else 2 if term in text else 0 for term in terms)
                subject_match = not subject_terms or any(term in text for term in subject_terms)
                candidates.append({
                    "title": title, "published": raw.get("published"), "summary": summary,
                    "link": link, "author": raw.get("author") or raw.get("source"),
                    "source": spec["source"], "source_type": "institutional_research_rss",
                    "research_category": resolved, "relevance_score": score,
                    "subject_match": subject_match,
                    "rss_route": spec["path"], "rss_params": spec["params"],
                })

    relevant = [
        item for item in candidates
        if item["relevance_score"] > 0 and item["subject_match"]
    ]
    relevant.sort(key=lambda row: (row["relevance_score"], str(row.get("published") or "")), reverse=True)
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in relevant:
        key = re.sub(r"\s+", "", item["title"]).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    items = deduped[:limit]

    if include_content:
        for item in items[:3]:
            try:
                detail = read_item(
                    route_path=item["rss_route"], params=item["rss_params"], title=item["title"],
                    link=item["link"], list_summary=item["summary"],
                )
                item["content_text"] = detail.get("content_text") or item["summary"]
                item["content_fallback"] = bool(detail.get("_fallback"))
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
                items.append({
                    "title": raw["title"], "published": raw.get("published_date"),
                    "summary": raw.get("snippet") or "", "link": raw["url"],
                    "author": raw.get("source"), "source": raw.get("source"),
                    "source_type": "websearch", "research_category": resolved,
                })
        items = items[:limit]
        fallback_used = bool(items)
        if fallback_used:
            warnings.append("专门研究源无匹配结果，已使用原始查询进行通用联网搜索")

    successful_sources = sum(bool(row["success"]) for row in coverage)
    acquisition_success = successful_sources > 0
    if acquisition_success and not items and not fallback_used:
        warnings.append("研究源读取成功，但时间窗内没有与查询主题匹配的报告")
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
        "source": (
            f"websearch/{(web_fallback or {}).get('provider', 'unknown')}" if fallback_used
            else "Infos/RSSHub研究资料库" if acquisition_success
            else "none"
        ),
        "success": acquisition_success or fallback_used,
        "partial": bool(items) and bool(errors),
        "data_time": latest.astimezone().isoformat() if latest and latest.tzinfo else latest.isoformat() if latest else None,
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


TOOL = ToolSpec(
    name="search_research_library",
    description=(
        "仅用于行业、宏观、期货、评级及跨机构专题研究资料检索。若用户询问一只具体 A 股的券商个股研报，"
        "必须使用 get_research_report；若询问公司新闻或公告，不要调用本工具。数据来自东方财富、中指研究院、"
        "麦肯锡、穆迪、南华期货、国家金融与发展实验室、前瞻和五矿期货等 Infos/RSSHub 路由。"
    ),
    parameters=object_schema({
        "query": {"type": "string", "description": "Planner 组织的检索表达式"},
        "category": {"type": "string", "enum": ["industry", "macro", "futures", "rating"], "description": "Planner 已解析的研究类别"},
        "subjects": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 12, "description": "Planner 提取的核心研究主题"},
        "futures_type": {"type": "string", "enum": ["black", "enchem", "nonfe", "agri", "bond", "exrate", "option", "ship", "stockindex_IM/IF/IH/IC", "macro"], "description": "仅期货研究可选的结构化品类"},
        "days": {"type": "integer", "minimum": 1, "maximum": 3650, "default": 365},
        "limit": {"type": "integer", "minimum": 1, "maximum": 30, "default": 12},
        "include_content": {"type": "boolean", "default": False},
        "fallback_to_web": {"type": "boolean", "default": True},
    }, ["query", "category", "subjects"]),
    executor=search_research_library,
    category="research",
)
