# -*- coding: utf-8 -*-
"""One-call semantic access to the curated RSSHub finance catalog."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any

from src.tools.base import ToolSpec, object_schema
from src.tools.rss_sources import TOPIC_ROUTE_PATHS

DESCRIPTION = (
    "从项目 Infos 页同一套 RSSHub 财经源中语义检索最新资讯。工具会自动选源、填路由参数、"
    "并行聚合、去重和排序；无需先列目录再读 feed。可覆盖市场快讯、公司新闻、公告、研报、"
    "宏观政策、行业和雪球热帖，必要时可在一次调用内补取前几条正文。RSS 无结果时可明确降级到联网搜索。"
)

_PREFERRED_ROUTES: dict[str, list[tuple[str, dict[str, str]]]] = {
    "market": [
        ("/cls/telegraph/:category?", {}),
        ("/wallstreetcn/live/:category?/:score?", {}),
        ("/10jqka/realtimenews/:tag?", {}),
        ("/stcn/article/list/kx", {}),
        ("/jin10/:important?", {}),
    ],
    "company": [
        ("/eastmoney/search/:keyword", {"keyword": "{query}"}),
        ("/gelonghui/keyword/:keyword", {"keyword": "{query}"}),
        ("/wallstreetcn/news/:category?", {}),
    ],
    "announcement": [
        ("/sse/disclosure/:query?", {"query": "{query}"}),
        ("/szse/disclosure/listed/notice/:query?", {"query": "{query}"}),
        ("/sse/inquire", {}),
        ("/szse/inquire/:category?/:select?/:keyword?", {}),
    ],
    "research": [
        ("/eastmoney/report/:category", {"category": "stock"}),
        ("/wkjyqh/research", {}),
        ("/nifd/research/:categoryGuid?", {}),
        ("/cih-index/report/list/:report?", {}),
    ],
    "macro": [
        ("/gov/pbc/tradeAnnouncement", {}),
        ("/nifd/research/:categoryGuid?", {}),
        ("/wallstreetcn/news/:category?", {"category": "global"}),
        ("/jin10/:important?", {"important": "1"}),
    ],
    "industry": [
        ("/eastmoney/search/:keyword", {"keyword": "{query}"}),
        ("/qianzhan/analyst/column/:type?", {}),
        ("/hexun/pe/news", {}),
        ("/mckinsey/cn/:category?", {}),
    ],
    "social": [
        ("/xueqiu/hots", {}),
        ("/eastmoney/search/:keyword", {"keyword": "{query}"}),
        ("/cls/hot", {}),
    ],
}

_DEFAULT_ROUTE_LIMITS = {
    "market": 3,
    "company": 1,
    "announcement": 3,
    "research": 3,
    "macro": 3,
    "industry": 1,
    "social": 2,
}

def _subject_terms(subjects: list[str]) -> list[str]:
    """Normalize semantic subjects supplied by the Planner/Workflow.

    This layer deliberately does not infer subjects from user wording.  Its
    only job is retrieval against an already structured request.
    """
    return list(dict.fromkeys(
        str(subject).strip().lower()
        for subject in subjects
        if len(str(subject).strip()) >= 2
    ))


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


def _default_option(route: dict[str, Any], param: dict[str, Any]) -> str | None:
    options = param.get("options") or []
    if not isinstance(options, list) or not options:
        return None
    value = options[0].get("value")
    return str(value) if value is not None else None


def _route_params(
    route: dict[str, Any],
    query: str,
    topic: str,
    subject_terms: list[str],
    preferred_overrides: dict[str, str],
) -> dict[str, str] | None:
    """Build safe parameters from catalog metadata; None means unusable route."""
    path = str(route.get("route_path") or "")
    code = next(
        (term for term in subject_terms if re.fullmatch(r"\d{6}", term)),
        "",
    )
    narrowed_query = " ".join(subject_terms) or query
    params: dict[str, str] = {}
    for param in route.get("params") or []:
        name = str(param.get("name") or "")
        if path == "/szse/inquire/:category?/:select?/:keyword?":
            if name == "category":
                value = "1" if code.startswith("3") else "0"
            elif name == "select":
                value = "全部函件类别"
            elif name == "keyword":
                value = code or narrowed_query
            else:
                value = None
        else:
            value = preferred_overrides.get(name)
        if value:
            value = value.replace("{query}", narrowed_query)
            if path == "/szse/disclosure/listed/notice/:query?":
                value = f"stock={code}" if code else None
        elif name in {"keyword", "query"}:
            if path == "/szse/disclosure/listed/notice/:query?" and code:
                value = f"stock={code}"
            elif path == "/szse/disclosure/listed/notice/:query?":
                value = None
            else:
                value = narrowed_query
        elif name == "lang":
            value = "zh-Hans" if any(str(option.get("value")) == "zh-Hans" for option in param.get("options") or []) else "Mandarin"
        elif param.get("default") is not None:
            value = str(param.get("default"))
        elif param.get("required"):
            value = _default_option(route, param)
            if value is None:
                return None
        else:
            value = None
        if value is not None and str(value).strip():
            params[name] = str(value).strip()
    return params


def _select_specs(
    routes: list[dict[str, Any]],
    query: str,
    topic: str,
    subjects: list[str] | None = None,
    *,
    max_routes: int = 5,
    allowed_paths_override: frozenset[str] | None = None,
) -> list[tuple[str, dict[str, str], str]]:
    """Rank the complete Infos catalog, while keeping battle-tested defaults."""
    preferred = {path: (index, overrides) for index, (path, overrides) in enumerate(_PREFERRED_ROUTES[topic])}
    subject_terms = _subject_terms(subjects or [])
    ranked: list[tuple[int, str, dict[str, str], str]] = []
    allowed_paths = allowed_paths_override or TOPIC_ROUTE_PATHS[topic]
    for route in routes:
        path = str(route.get("route_path") or "")
        if not path or path not in allowed_paths:
            continue
        preferred_info = preferred.get(path)
        overrides = dict(preferred_info[1]) if preferred_info else {}
        params = _route_params(route, query, topic, subject_terms, overrides)
        if params is None:
            continue
        score = 0
        if preferred_info:
            score += 1000 - preferred_info[0] * 20
        # Routes with query parameters can perform server-side narrowing.
        if any(key in params for key in ("keyword", "query")):
            score += 300
        if score > 0:
            ranked.append((score, path, params, str(route.get("name") or path)))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [(path, params, name) for _, path, params, name in ranked[:max_routes]]


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
    from api.v1.endpoints._rss_reader import read_feed, read_item

    query = query.strip()
    if not query:
        raise ValueError("query 不能为空")
    if not 1 <= int(days) <= 365:
        raise ValueError("days 必须在 1 到 365 之间")
    if not 1 <= int(limit) <= 30:
        raise ValueError("limit 必须在 1 到 30 之间")
    resolved_topic = str(topic or "").strip().lower()
    if resolved_topic not in _PREFERRED_ROUTES:
        raise ValueError(f"不支持的 topic: {resolved_topic}")
    catalog = get_rss_catalog(force=False)
    catalog_routes = [route for route in catalog.get("routes") or [] if isinstance(route, dict)]
    max_routes = _DEFAULT_ROUTE_LIMITS[resolved_topic] if _max_routes is None else max(1, int(_max_routes))
    specs = _select_specs(
        catalog_routes,
        query,
        resolved_topic,
        subjects,
        max_routes=max_routes,
        allowed_paths_override=_route_paths,
    )
    if not specs and resolved_topic != "market" and _route_paths is None:
        specs = _select_specs(catalog_routes, query, "market", subjects)

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
            result = read_feed(route_path=route_path, params=params, limit=feed_window)
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
        errors.extend(
            f"{route_result['route_path']}: {error}"
            for error in result.get("errors") or []
        )
        for raw in result.get("items") or []:
            item = dict(raw)
            item["rss_route"] = route_result["route_path"]
            item["rss_params"] = route_result["params"]
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
    expired_count = sum(
        1 for item in candidate_items
        if _has_known_time(item) and not _item_is_recent(item, cutoff)
    )
    unknown_time_count = sum(1 for item in candidate_items if not _has_known_time(item))
    items = [item for item in candidate_items if _item_is_recent(item, cutoff)]
    subject_terms = _subject_terms(subjects or [])
    for item in items:
        searchable = (
            f"{item.get('title', '')} {item.get('summary', '')}"
        ).lower()
        item["exact_subject_mentions"] = [
            subject for subject in subject_terms
            if subject in searchable
        ]
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
                detail = read_item(
                    route_path=item["rss_route"],
                    params=item.get("rss_params") or {},
                    title=str(item.get("title") or ""),
                    item_id=str(item.get("id") or ""),
                    link=str(item.get("link") or ""),
                    list_summary=str(item.get("summary") or ""),
                )
                item["content_text"] = detail.get("content_text") or item.get("summary")
                item["content_fallback"] = bool(detail.get("_fallback"))
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
            selected.append({
                "title": raw.get("title") or "",
                "summary": raw.get("snippet") or "",
                "link": link,
                "published": raw.get("published_date"),
                "source": raw.get("source") or web_fallback.get("provider"),
                "source_type": "websearch",
                "semantic_status": "model_required",
            })
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
                1 for item in result["result"].get("items") or []
                if _item_is_recent(item, cutoff)
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
    acquisition_success = successful_route_count > 0 or bool(
        web_fallback and web_fallback.get("success")
    )
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
        "web_fallback": web_fallback,
        "source": source,
        # `success` describes whether the tool executed and reached a data
        # source. `item_count == 0` describes a valid empty search result.
        "success": acquisition_success,
        "partial": partial,
        "data_time": latest_time.astimezone().isoformat() if latest_time and latest_time.tzinfo else latest_time.isoformat() if latest_time else None,
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
            "query": {"type": "string", "description": "要查的公司、行业、事件或宏观主题；例如 贵州茅台、半导体景气、央行降准"},
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
            "days": {"type": "integer", "minimum": 1, "maximum": 365, "default": 30, "description": "只返回最近多少天的记录；缺少发布时间的记录会保留并明确告警"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 30, "default": 12, "description": "去重后最多返回条数"},
            "include_content": {"type": "boolean", "default": False, "description": "是否在同一次调用中补取前 3 条正文；仅深入阅读时开启"},
            "fallback_to_web": {"type": "boolean", "default": True, "description": "RSS 无结果时是否自动调用联网搜索兜底"},
        },
        ["query", "topic"],
    ),
    executor=search_financial_news,
    category="sentiment",
)
