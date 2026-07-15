# -*- coding: utf-8 -*-
"""One-call semantic access to the curated RSSHub finance catalog."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

from src.tools.base import ToolSpec, object_schema

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
        ("/szse/inquire/:category?/:select?/:keyword?", {"keyword": "{query}"}),
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

_TOPIC_HINTS: dict[str, tuple[str, ...]] = {
    "market": ("快讯", "实时", "要闻", "股市", "A股", "热门", "市场"),
    "company": ("公司", "个股", "股票", "搜索", "上市公司"),
    "announcement": ("公告", "披露", "问询", "监管", "交易所", "上市公司"),
    "research": ("研报", "研究", "报告", "评级", "券商"),
    "macro": ("宏观", "央行", "货币政策", "利率", "经济", "债券"),
    "industry": ("行业", "产业", "创投", "科技", "消费", "医药", "汽车"),
    "social": ("热帖", "热榜", "热门", "话题", "雪球", "社区"),
}

_INTENT_WORDS = re.compile(
    r"(?:最新|近期|今日|今天|现在|相关|查询|搜索|看看|分析|市场|公司|个股|股票|"
    r"消息|新闻|资讯|快讯|公告|披露|问询|监管|研报|研究报告|报告|评级|目标价|"
    r"宏观|政策|行业|产业链|板块|景气|雪球|热帖|讨论|社区|情绪|A股|a股)+",
    flags=re.I,
)


def _infer_topic(query: str) -> str:
    text = query.lower()
    rules = (
        ("announcement", ("公告", "问询", "监管", "分红", "回购", "减持", "增持")),
        ("research", ("研报", "评级", "目标价", "盈利预测", "机构观点")),
        ("macro", ("宏观", "央行", "货币政策", "利率", "lpr", "cpi", "pmi", "社融")),
        ("industry", ("行业", "产业链", "赛道", "板块", "景气")),
        ("social", ("雪球", "热帖", "讨论", "社区", "情绪")),
        ("company", ("公司", "个股", "股票", "业绩", "财报")),
    )
    for topic, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return topic
    return "market"


def _query_terms(query: str) -> list[str]:
    normalized = query.strip().lower()
    terms = [normalized]
    terms.extend(part.lower() for part in re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}", query))
    subject = _INTENT_WORDS.sub(" ", query)
    terms.extend(part.lower() for part in re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}", subject))
    return list(dict.fromkeys(term for term in terms if len(term) >= 2))


def _subject_terms(query: str) -> list[str]:
    subject = _INTENT_WORDS.sub(" ", query)
    return list(dict.fromkeys(
        part.lower()
        for part in re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}", subject)
        if len(part) >= 2
    ))


def _score(item: dict[str, Any], terms: list[str]) -> int:
    title = str(item.get("title") or "").lower()
    summary = str(item.get("summary") or "").lower()
    return sum(5 for term in terms if term in title) + sum(2 for term in terms if term in summary)


def _published_key(item: dict[str, Any]) -> str:
    value = str(item.get("published") or "")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return value


def _default_option(route: dict[str, Any], param: dict[str, Any], topic: str) -> str | None:
    options = param.get("options") or []
    if not isinstance(options, list) or not options:
        return None
    preferred_labels = {
        "market": ("A股", "财经", "要闻", "股票"),
        "company": ("A股", "公司", "个股", "股票"),
        "research": ("个股研报", "行业研报", "策略报告", "研究"),
        "macro": ("宏观", "经济", "债券"),
        "industry": ("行业", "科技", "消费"),
    }.get(topic, ())
    for preferred in preferred_labels:
        for option in options:
            if preferred.lower() in str(option.get("label") or "").lower():
                return str(option.get("value"))
    value = options[0].get("value")
    return str(value) if value is not None else None


def _route_params(
    route: dict[str, Any],
    query: str,
    topic: str,
    preferred_overrides: dict[str, str],
) -> dict[str, str] | None:
    """Build safe parameters from catalog metadata; None means unusable route."""
    path = str(route.get("route_path") or "")
    code_match = re.search(r"(?<!\d)(?:sh|sz|bj)?(\d{6})(?!\d)", query, flags=re.I)
    narrowed_query = " ".join(_subject_terms(query)) or query
    params: dict[str, str] = {}
    for param in route.get("params") or []:
        name = str(param.get("name") or "")
        value = preferred_overrides.get(name)
        if value:
            value = value.replace("{query}", narrowed_query)
            if path == "/szse/disclosure/listed/notice/:query?":
                value = f"stock={code_match.group(1)}" if code_match else None
        elif name in {"keyword", "query"}:
            if path == "/szse/disclosure/listed/notice/:query?" and code_match:
                value = f"stock={code_match.group(1)}"
            elif path == "/szse/disclosure/listed/notice/:query?":
                value = None
            else:
                value = narrowed_query
        elif name == "lang":
            value = "zh-Hans" if any(str(option.get("value")) == "zh-Hans" for option in param.get("options") or []) else "Mandarin"
        elif param.get("default") is not None:
            value = str(param.get("default"))
        elif param.get("required"):
            value = _default_option(route, param, topic)
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
    *,
    max_routes: int = 5,
) -> list[tuple[str, dict[str, str], str]]:
    """Rank the complete Infos catalog, while keeping battle-tested defaults."""
    preferred = {path: (index, overrides) for index, (path, overrides) in enumerate(_PREFERRED_ROUTES[topic])}
    query_terms = _query_terms(query)
    ranked: list[tuple[int, str, dict[str, str], str]] = []
    for route in routes:
        path = str(route.get("route_path") or "")
        if not path:
            continue
        preferred_info = preferred.get(path)
        overrides = preferred_info[1] if preferred_info else {}
        params = _route_params(route, query, topic, overrides)
        if params is None:
            continue
        haystack = " ".join(
            str(route.get(key) or "")
            for key in ("route_path", "name", "namespace", "namespace_name", "description")
        ).lower()
        score = 0
        if preferred_info:
            score += 100 - preferred_info[0] * 3
        score += sum(18 for hint in _TOPIC_HINTS[topic] if hint.lower() in haystack)
        score += sum(220 for term in query_terms if term in haystack)
        # Routes with query parameters can perform server-side narrowing.
        if any(key in params for key in ("keyword", "query")):
            score += 30
        if score > 0:
            ranked.append((score, path, params, str(route.get("name") or path)))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [(path, params, name) for _, path, params, name in ranked[:max_routes]]


def search_financial_news(
    query: str,
    topic: str = "auto",
    limit: int = 12,
    include_content: bool = False,
    fallback_to_web: bool = True,
) -> dict[str, Any]:
    # Lazy imports avoid importing the complete FastAPI router while the tool
    # registry itself is still being constructed.
    from api.v1.endpoints._rss_catalog import get_rss_catalog
    from api.v1.endpoints._rss_reader import read_feed, read_item

    query = query.strip()
    if not query:
        raise ValueError("query 不能为空")
    resolved_topic = _infer_topic(query) if topic == "auto" else topic
    if resolved_topic not in _PREFERRED_ROUTES:
        raise ValueError(f"不支持的 topic: {resolved_topic}")
    catalog = get_rss_catalog(force=False)
    catalog_routes = [route for route in catalog.get("routes") or [] if isinstance(route, dict)]
    specs = _select_specs(catalog_routes, query, resolved_topic)
    if not specs and resolved_topic != "market":
        specs = _select_specs(catalog_routes, query, "market")

    route_results: list[dict[str, Any]] = []
    errors: list[str] = []

    def fetch(spec: tuple[str, dict[str, str], str]) -> dict[str, Any]:
        route_path, params, route_name = spec
        result = read_feed(route_path=route_path, params=params, limit=max(8, min(int(limit), 20)))
        return {"route_path": route_path, "route_name": route_name, "params": params, "result": result}

    with ThreadPoolExecutor(max_workers=min(4, max(1, len(specs)))) as pool:
        futures = [pool.submit(fetch, spec) for spec in specs]
        for future in as_completed(futures):
            try:
                route_results.append(future.result())
            except Exception as exc:
                errors.append(str(exc))

    deduped: dict[str, dict[str, Any]] = {}
    for route_result in route_results:
        result = route_result["result"]
        errors.extend(str(error) for error in result.get("errors") or [])
        for raw in result.get("items") or []:
            item = dict(raw)
            item["rss_route"] = route_result["route_path"]
            item["rss_params"] = route_result["params"]
            key = str(item.get("link") or item.get("id") or item.get("title") or "").strip()
            if not key:
                continue
            existing = deduped.get(key)
            if existing is None or len(str(item.get("summary") or "")) > len(str(existing.get("summary") or "")):
                deduped[key] = item

    terms = _query_terms(query)
    items = list(deduped.values())
    items.sort(key=lambda item: (_score(item, terms), _published_key(item)), reverse=True)
    subject_terms = _subject_terms(query)
    if subject_terms and resolved_topic != "market":
        relevant = [item for item in items if any(term in f"{item.get('title', '')} {item.get('summary', '')}".lower() for term in subject_terms)]
        selected = relevant[: max(1, min(int(limit), 30))]
    else:
        selected = items[: max(1, min(int(limit), 30))]

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
    fallback_used = False
    if not selected and fallback_to_web:
        from src.tools.websearch import websearch

        web_fallback = websearch(query=query, num_results=max(5, min(int(limit), 10)), search_type="auto")
        fallback_used = True
        for raw in web_fallback.get("results") or []:
            selected.append({
                "title": raw.get("title") or "",
                "summary": raw.get("snippet") or "",
                "link": raw.get("url") or "",
                "published": raw.get("published_date"),
                "source": raw.get("source") or web_fallback.get("provider"),
                "source_type": "websearch",
            })
        selected = selected[: max(1, min(int(limit), 30))]

    used_routes = [
        {
            "route_path": result["route_path"],
            "route_name": result["route_name"],
            "params": result["params"],
            "item_count": len(result["result"].get("items") or []),
        }
        for result in route_results
    ]
    return {
        "query": query,
        "topic": resolved_topic,
        "items": selected,
        "item_count": len(selected),
        "rss_routes": used_routes,
        "rss_catalog_count": catalog.get("count"),
        "web_fallback": web_fallback,
        "source": f"websearch/{web_fallback.get('provider', 'unknown')}" if fallback_used and web_fallback else "RSSHub",
        "success": bool(selected),
        "data_time": datetime.now().astimezone().isoformat(),
        "fallback_used": fallback_used,
        "is_stale": False,
        "errors": list(dict.fromkeys(errors))[:10],
    }


TOOL = ToolSpec(
    name="search_financial_news",
    description=DESCRIPTION,
    parameters=object_schema(
        {
            "query": {"type": "string", "description": "要查的公司、行业、事件或宏观主题；例如 贵州茅台、半导体景气、央行降准"},
            "topic": {
                "type": "string",
                "enum": ["auto", "market", "company", "announcement", "research", "macro", "industry", "social"],
                "default": "auto",
                "description": "资讯类型，auto 会根据 query 自动判断",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 30, "default": 12, "description": "去重后最多返回条数"},
            "include_content": {"type": "boolean", "default": False, "description": "是否在同一次调用中补取前 3 条正文；仅深入阅读时开启"},
            "fallback_to_web": {"type": "boolean", "default": True, "description": "RSS 无结果时是否自动调用联网搜索兜底"},
        },
        ["query"],
    ),
    executor=search_financial_news,
    category="sentiment",
)
