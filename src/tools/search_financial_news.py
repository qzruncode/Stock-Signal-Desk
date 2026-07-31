# -*- coding: utf-8 -*-
"""One-call semantic access to the filtered RSSHub finance catalog."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any

from src.tools.base import ToolSpec, object_schema
from src.tools.rss_source_resolver import resolve_rss_source_specs

DESCRIPTION = (
    "从助手既有 RSSHub 财经来源目录中语义检索最新资讯。工具会自动选源、填路由参数、"
    "并行聚合、去重和排序；无需先列目录再读 feed。可覆盖市场快讯、公司新闻、公告、研报、"
    "宏观政策、行业和社区热点，必要时可在一次调用内补取正文和文本附件。RSS 无结果时可明确降级到联网搜索。"
)

_TOPICS = frozenset(
    {
        "market",
        "company",
        "announcement",
        "research",
        "macro",
        "industry",
        "social",
    }
)


def _subject_terms(subjects: list[str]) -> list[str]:
    """Normalize semantic subjects supplied by the Planner/Workflow.

    This layer deliberately does not infer subjects from user wording.  Its
    only job is retrieval against an already structured request.
    """
    return list(dict.fromkeys(str(subject).strip().lower() for subject in subjects if len(str(subject).strip()) >= 2))


def _published_key(item: dict[str, Any]) -> str:
    value = str(item.get("published") or "")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return value


def _latest_item_time(items: list[dict[str, Any]]) -> datetime | None:
    values: list[datetime] = []
    for item in items:
        raw = str(item.get("published") or "").strip()
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone().replace(tzinfo=None)
            values.append(parsed)
        except ValueError:
            continue
    return max(values) if values else None


def _item_is_recent(item: dict[str, Any], cutoff: datetime) -> bool:
    raw = str(item.get("published") or "").strip()
    if not raw:
        return True
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed >= cutoff
    except ValueError:
        return True


def _has_known_time(item: dict[str, Any]) -> bool:
    raw = str(item.get("published") or "").strip()
    if not raw:
        return False
    try:
        datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _is_usable_item(item: dict[str, Any]) -> bool:
    title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
    link = str(item.get("link") or "").strip()
    if not title or not link:
        return False
    # Some keyword feeds leak chart-series labels such as
    # ``11136,贵州茅台`` as article titles.  They are not readable documents.
    if re.fullmatch(r"\d{2,}\s*[,，]\s*[^,，]+", title):
        return False
    meaningful = re.findall(r"[A-Za-z0-9\u4e00-\u9fff]", title)
    return len(meaningful) >= 4


def _select_specs(
    routes: list[dict[str, Any]],
    query: str,
    topic: str,
    subjects: list[str] | None = None,
    *,
    max_routes: int = 5,
    allowed_paths_override: frozenset[str] | None = None,
) -> list[tuple[str, dict[str, str], str]]:
    return resolve_rss_source_specs(
        routes,
        information_need=topic,
        query=query,
        subjects=subjects or (),
        max_sources=max_routes,
        allowed_paths=allowed_paths_override,
    )


def search_financial_news(
    query: str,
    topic: str = "",
    subjects: list[str] | None = None,
    days: int = 30,
    limit: int = 12,
    include_content: bool = False,
    fallback_to_web: bool = True,
    _route_paths: frozenset[str] | None = None,
    _max_routes: int | None = None,
) -> dict[str, Any]:
    # Lazy imports avoid importing the complete FastAPI router while the tool
    # registry itself is still being constructed.
    from api.v1.endpoints._rss_catalog import get_rss_catalog
    from api.v1.endpoints._rss_reader import read_feed
    from src.tools._rss_agent import rss_item_ref
    from src.tools.read_rss_item import read_rss_item

    query = query.strip()
    if not query:
        raise ValueError("query 不能为空")
    if not 1 <= int(days) <= 365:
        raise ValueError("days 必须在 1 到 365 之间")
    if not 1 <= int(limit) <= 30:
        raise ValueError("limit 必须在 1 到 30 之间")
    resolved_topic = str(topic or "").strip().lower()
    if resolved_topic not in _TOPICS:
        raise ValueError(f"不支持的 topic: {resolved_topic}")
    catalog = get_rss_catalog(force=False, scope="finance")
    catalog_routes = [route for route in catalog.get("routes") or [] if isinstance(route, dict)]
    max_routes = (
        max(2, min(6, int(limit) // 4 + 1))
        if _max_routes is None
        else max(1, int(_max_routes))
    )
    specs = _select_specs(
        catalog_routes,
        query,
        resolved_topic,
        subjects,
        max_routes=max_routes,
        allowed_paths_override=_route_paths,
    )
    route_results: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    def fetch(spec: tuple[str, dict[str, str], str]) -> dict[str, Any]:
        route_path, params, route_name = spec
        feed_window = (
            max(20, min(50, int(limit) * 4))
            if resolved_topic in {"company", "announcement", "research", "macro", "industry"}
            else max(12, min(30, int(limit) * 2))
        )
        try:
            result = read_feed(
                route_path=route_path,
                params=params,
                limit=feed_window,
            )
        except Exception as exc:
            result = {
                "items": [],
                "item_count": 0,
                "errors": [f"读取失败: {exc}"],
                "_cached": False,
            }
        return {"route_path": route_path, "route_name": route_name, "params": params, "result": result}

    with ThreadPoolExecutor(max_workers=min(4, max(1, len(specs)))) as pool:
        futures = [pool.submit(fetch, spec) for spec in specs]
        for future in as_completed(futures):
            try:
                route_results.append(future.result())
            except Exception as exc:
                errors.append(f"RSSHub 并行读取失败: {exc}")

    spec_order = {path: index for index, (path, _, _) in enumerate(specs)}
    route_results.sort(key=lambda row: spec_order.get(row["route_path"], len(specs)))

    deduped: dict[str, dict[str, Any]] = {}
    for route_result in route_results:
        result = route_result["result"]
        errors.extend(f"{route_result['route_path']}: {error}" for error in result.get("errors") or [])
        for raw in result.get("items") or []:
            item = dict(raw)
            item["rss_route"] = route_result["route_path"]
            item["rss_params"] = route_result["params"]
            item.setdefault(
                "item_ref",
                rss_item_ref(
                    route_path=route_result["route_path"],
                    params=route_result["params"],
                    options={},
                    namespace="",
                    item=item,
                ),
            )
            if not _is_usable_item(item):
                continue
            normalized_title = re.sub(r"\s+", "", str(item.get("title") or "")).lower()
            key = normalized_title or str(item.get("link") or item.get("id") or "").strip()
            if not key:
                continue
            existing = deduped.get(key)
            if existing is None or len(str(item.get("summary") or "")) > len(str(existing.get("summary") or "")):
                deduped[key] = item

    cutoff = datetime.now() - timedelta(days=int(days))
    candidate_items = list(deduped.values())
    expired_count = sum(1 for item in candidate_items if _has_known_time(item) and not _item_is_recent(item, cutoff))
    unknown_time_count = sum(1 for item in candidate_items if not _has_known_time(item))
    items = [item for item in candidate_items if _item_is_recent(item, cutoff)]
    subject_terms = _subject_terms(subjects or [])
    for item in items:
        searchable = (f"{item.get('title', '')} {item.get('summary', '')}").lower()
        item["exact_subject_mentions"] = [subject for subject in subject_terms if subject in searchable]
        item["semantic_status"] = "model_required"
    items.sort(
        key=lambda item: (
            len(item.get("exact_subject_mentions") or []),
            _published_key(item),
        ),
        reverse=True,
    )
    selected = items[: max(1, min(int(limit), 30))]

    if expired_count:
        warnings.append(f"已按最近 {days} 天过滤 {expired_count} 条过期 RSS 记录")
    if unknown_time_count:
        warnings.append(f"有 {unknown_time_count} 条 RSS 记录缺少可解析发布时间，无法验证时间范围")

    if include_content:
        for item in selected[:3]:
            try:
                detail = read_rss_item(
                    item_ref=item.get("item_ref") or {},
                    item=item,
                    include_documents=True,
                )
                item["content_text"] = detail.get("content_text") or item.get("summary")
                item["content_chunks"] = detail.get("content_chunks") or []
                item["resources"] = detail.get("resources") or []
                item["content_fallback"] = False
            except Exception as exc:
                item["content_text"] = item.get("summary")
                item["content_fallback"] = True
                errors.append(f"{item.get('rss_route')}: 正文读取失败: {exc}")

    web_fallback: dict[str, Any] | None = None
    fallback_attempted = False
    fallback_used = False
    if not selected and fallback_to_web:
        from src.tools.websearch import websearch

        fallback_attempted = True
        try:
            web_fallback = websearch(
                query=query,
                num_results=max(5, min(int(limit), 10)),
                search_type="auto",
            )
        except Exception as exc:
            web_fallback = {
                "success": False,
                "provider": "none",
                "results": [],
                "errors": [str(exc)],
            }
        for raw in web_fallback.get("results") or []:
            link = str(raw.get("url") or "").strip()
            title = str(raw.get("title") or "").strip()
            if not link and not title:
                continue
            selected.append(
                {
                    "title": raw.get("title") or "",
                    "summary": raw.get("snippet") or "",
                    "link": link,
                    "published": raw.get("published_date"),
                    "source": raw.get("source") or web_fallback.get("provider"),
                    "source_type": "websearch",
                    "semantic_status": "model_required",
                }
            )
        selected = selected[: max(1, min(int(limit), 30))]
        fallback_used = bool(selected)
        if fallback_used:
            warnings.append("RSSHub 未返回时间窗内记录，已使用通用联网搜索兜底")
        else:
            errors.extend(f"websearch: {error}" for error in web_fallback.get("errors") or [])
            warnings.append("RSSHub 和通用联网搜索均未返回可用结果")

    used_routes = [
        {
            "route_path": result["route_path"],
            "route_name": result["route_name"],
            "params": result["params"],
            "item_count": len(result["result"].get("items") or []),
            "recent_item_count": sum(
                1 for item in result["result"].get("items") or [] if _item_is_recent(item, cutoff)
            ),
            # A feed that was read successfully can legitimately contain no
            # items inside the requested time window.
            # Treat transport/parser errors as failures, not an empty result.
            "success": not bool(result["result"].get("errors")),
            "cached": bool(result["result"].get("_cached")),
            "errors": [str(error) for error in result["result"].get("errors") or []],
        }
        for result in route_results
    ]
    attempted_route_count = len(specs)
    successful_route_count = sum(1 for route in used_routes if route["success"])
    failed_route_count = attempted_route_count - successful_route_count
    if not specs:
        warnings.append(f"RSSHub 目录中没有可用于 {resolved_topic} 主题的路由")
    if failed_route_count and selected and not fallback_used:
        warnings.append(
            f"{failed_route_count}/{attempted_route_count} 条 RSSHub 路由未返回数据，当前结果为可用路由的部分聚合"
        )
    latest_time = _latest_item_time(selected)
    max_age_days = {
        "market": 2,
        "social": 2,
        "company": 30,
        "industry": 30,
        "macro": 45,
        "announcement": 90,
        "research": 120,
    }[resolved_topic]
    acquisition_success = successful_route_count > 0 or bool(web_fallback and web_fallback.get("success"))
    if selected:
        source = f"websearch/{web_fallback.get('provider', 'unknown')}" if fallback_used and web_fallback else "RSSHub"
    elif successful_route_count > 0:
        source = "RSSHub"
    else:
        source = "none"
    partial = bool(selected) and (fallback_used or (failed_route_count > 0 and successful_route_count > 0))
    return {
        "query": query,
        "topic": resolved_topic,
        "days": int(days),
        "items": selected,
        "item_count": len(selected),
        "rss_routes": used_routes,
        "rss_catalog_count": catalog.get("count"),
        "attempted_route_count": attempted_route_count,
        "successful_route_count": successful_route_count,
        "coverage": {
            "planned_sources": attempted_route_count,
            "attempted_sources": attempted_route_count,
            "successful_sources": successful_route_count,
            "item_count": len(selected),
            "text_documents_found": sum(
                len(item.get("attachments") or [])
                for item in selected
            ),
            "text_documents_extracted": sum(
                len(item.get("resources") or [])
                for item in selected
            ),
            "discarded_non_text": sum(
                int(
                    (route.get("result") or {})
                    .get("coverage", {})
                    .get("discarded_non_text")
                    or 0
                )
                for route in route_results
            ),
            "failures": list(dict.fromkeys(errors))[:10],
        },
        "web_fallback": web_fallback,
        "source": source,
        # `success` describes whether the tool executed and reached a data
        # source. `item_count == 0` describes a valid empty search result.
        "success": acquisition_success,
        "partial": partial,
        "data_time": (
            latest_time.astimezone().isoformat()
            if latest_time and latest_time.tzinfo
            else latest_time.isoformat() if latest_time else None
        ),
        "retrieved_at": datetime.now().astimezone().isoformat(),
        "fallback_attempted": fallback_attempted,
        "fallback_used": fallback_used,
        "fallback_recommended": not bool(selected),
        "is_stale": (latest_time < datetime.now() - timedelta(days=max_age_days)) if latest_time else None,
        "freshness_unknown": latest_time is None,
        "errors": list(dict.fromkeys(errors))[:10],
        "warnings": list(dict.fromkeys(warnings))[:10],
    }


TOOL = ToolSpec(
    name="search_financial_news",
    description=DESCRIPTION,
    parameters=object_schema(
        {
            "query": {
                "type": "string",
                "description": "要查的公司、行业、事件或宏观主题；例如 贵州茅台、半导体景气、央行降准",
            },
            "topic": {
                "type": "string",
                "enum": ["market", "company", "announcement", "research", "macro", "industry", "social"],
                "description": "Planner 已解析的资讯类型；工具不根据 query 猜测类别",
            },
            "subjects": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 12,
                "description": "Planner 提取的核心公司、行业或事件主体；工具只记录逐字提及，语义相关性由分析模型判断",
            },
            "days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 365,
                "default": 30,
                "description": "只返回最近多少天的记录；缺少发布时间的记录会保留并明确告警",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 30,
                "default": 12,
                "description": "去重后最多返回条数",
            },
            "include_content": {
                "type": "boolean",
                "default": False,
                "description": "是否在同一次调用中补取前 3 条正文；仅深入阅读时开启",
            },
            "fallback_to_web": {
                "type": "boolean",
                "default": True,
                "description": "RSS 无结果时是否自动调用联网搜索兜底",
            },
        },
        ["query", "topic"],
    ),
    executor=search_financial_news,
    category="sentiment",
)
