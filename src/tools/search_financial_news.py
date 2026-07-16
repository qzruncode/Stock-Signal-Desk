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

_DEFAULT_ROUTE_LIMITS = {
    "market": 3,
    "company": 1,
    "announcement": 3,
    "research": 3,
    "macro": 3,
    "industry": 1,
    "social": 2,
}

_MACRO_MATCH_TERMS = (
    "买断式逆回购",
    "逆回购",
    "公开市场操作",
    "中期借贷便利",
    "mlf",
    "lpr",
    "cpi",
    "pmi",
    "gdp",
    "社融",
    "降准",
    "降息",
    "利率",
    "央行",
    "货币政策",
)

_MACRO_SPECIFIC_TERMS = frozenset({
    "买断式逆回购",
    "逆回购",
    "公开市场操作",
    "中期借贷便利",
    "mlf",
    "lpr",
    "cpi",
    "pmi",
    "gdp",
    "社融",
    "降准",
    "降息",
    "利率",
})


def _infer_topic(query: str) -> str:
    text = query.lower()
    rules = (
        ("macro", ("宏观", "央行", "货币政策", "公开市场操作", "逆回购", "mlf", "lpr", "cpi", "pmi", "社融")),
        ("announcement", ("公告", "问询", "监管", "分红", "回购", "减持", "增持")),
        ("research", ("研报", "评级", "目标价", "盈利预测", "机构观点")),
        ("industry", ("行业", "产业链", "赛道", "板块", "景气")),
        ("social", ("雪球", "热帖", "讨论", "社区", "情绪")),
        ("company", ("公司", "个股", "股票", "业绩", "财报")),
    )
    for topic, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return topic
    if re.search(r"(?<!\d)(?:sh|sz|bj)?\d{6}(?:\.(?:sh|sz|bj))?(?!\d)", text, flags=re.I):
        return "company"
    try:
        from src.data.stock_index_loader import get_stock_name_index_map

        names = {name for name in get_stock_name_index_map().values() if len(name) >= 3}
        if any(name in query for name in names):
            return "company"
    except Exception:
        pass
    return "market"


def _research_category(query: str) -> str:
    """Choose an Eastmoney report channel from the user's research intent."""
    text = query.lower()
    if any(word in text for word in ("宏观", "经济", "货币", "利率", "cpi", "pmi", "gdp")):
        return "macresearch"
    if any(word in text for word in ("策略", "大势", "配置", "晨报")):
        return "strategyreport" if "晨报" not in text else "brokerreport"
    if re.search(r"(?<!\d)(?:sh|sz|bj)?\d{6}(?!\d)", text, flags=re.I):
        return "stock"
    if any(word in text for word in ("个股", "公司", "股票", "评级", "目标价", "盈利预测")):
        return "stock"
    return "industry"


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


def _matching_terms(query: str, topic: str) -> list[str]:
    terms = _subject_terms(query)
    if topic == "macro":
        lowered = query.lower()
        terms.extend(term for term in _MACRO_MATCH_TERMS if term.lower() in lowered)
    return list(dict.fromkeys(term.lower() for term in terms if len(term) >= 2))


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


def _matches_subject(item: dict[str, Any], subject_terms: list[str], topic: str) -> bool:
    """Keep broad feeds from leaking unrelated rows into a focused query.

    Company and exchange queries must name the subject in the title.  A broad
    market table often contains hundreds of company names in its body; treating
    that as company-specific news is a false positive.  For industry, research,
    macro and social topics, a body match is still useful and is retained.
    """
    if not subject_terms:
        return True
    title = str(item.get("title") or "").lower()
    summary = str(item.get("summary") or "").lower()
    if topic == "macro":
        specific = [term for term in subject_terms if term in _MACRO_SPECIFIC_TERMS]
        if specific:
            return any(term in f"{title} {summary}" for term in specific)
    if any(term in title for term in subject_terms):
        return True
    if topic in {"company", "announcement"}:
        return False
    return any(term in summary for term in subject_terms)


def _trusted_route_bonus(item: dict[str, Any], subject_terms: list[str], topic: str) -> int:
    """Prefer a first-party specialist feed when it directly owns the fact."""
    route = str(item.get("rss_route") or "")
    if (
        topic == "macro"
        and route == "/gov/pbc/tradeAnnouncement"
        and any(term in _MACRO_SPECIFIC_TERMS for term in subject_terms)
    ):
        return 100
    return 0


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
        if path == "/szse/inquire/:category?/:select?/:keyword?":
            code = code_match.group(1) if code_match else ""
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
    allowed_paths_override: frozenset[str] | None = None,
) -> list[tuple[str, dict[str, str], str]]:
    """Rank the complete Infos catalog, while keeping battle-tested defaults."""
    preferred = {path: (index, overrides) for index, (path, overrides) in enumerate(_PREFERRED_ROUTES[topic])}
    subject_terms = _subject_terms(query)
    ranked: list[tuple[int, str, dict[str, str], str]] = []
    allowed_paths = allowed_paths_override or TOPIC_ROUTE_PATHS[topic]
    for route in routes:
        path = str(route.get("route_path") or "")
        if not path or path not in allowed_paths:
            continue
        preferred_info = preferred.get(path)
        overrides = dict(preferred_info[1]) if preferred_info else {}
        if path == "/eastmoney/report/:category" and topic == "research":
            overrides["category"] = _research_category(query)
        params = _route_params(route, query, topic, overrides)
        if params is None:
            continue
        haystack = " ".join(
            str(route.get(key) or "")
            for key in ("route_path", "name", "namespace", "namespace_name", "description")
        ).lower()
        source_identity = " ".join(
            str(route.get(key) or "")
            for key in ("name", "namespace", "namespace_name")
        ).lower()
        score = 0
        if preferred_info:
            score += 1000 - preferred_info[0] * 20
        score += sum(10 for hint in _TOPIC_HINTS[topic] if hint.lower() in haystack)
        # A user can explicitly name a source (for example "穆迪评级").
        # Match only source identity fields here; matching arbitrary query words
        # against a route description made broad feeds outrank exact search
        # routes for ordinary topics such as "半导体景气".
        score += sum(2000 for term in subject_terms if term in source_identity)
        # Routes with query parameters can perform server-side narrowing.
        if any(key in params for key in ("keyword", "query")):
            score += 300
        if score > 0:
            ranked.append((score, path, params, str(route.get("name") or path)))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [(path, params, name) for _, path, params, name in ranked[:max_routes]]


def search_financial_news(
    query: str,
    topic: str = "auto",
    days: int = 30,
    limit: int = 12,
    include_content: bool = False,
    fallback_to_web: bool = True,
    _route_paths: frozenset[str] | None = None,
    _max_routes: int | None = None,
    _filter_by_subject: bool = True,
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
    resolved_topic = _infer_topic(query) if topic == "auto" else topic
    if resolved_topic not in _PREFERRED_ROUTES:
        raise ValueError(f"不支持的 topic: {resolved_topic}")
    catalog = get_rss_catalog(force=False)
    catalog_routes = [route for route in catalog.get("routes") or [] if isinstance(route, dict)]
    max_routes = _DEFAULT_ROUTE_LIMITS[resolved_topic] if _max_routes is None else max(1, int(_max_routes))
    specs = _select_specs(
        catalog_routes,
        query,
        resolved_topic,
        max_routes=max_routes,
        allowed_paths_override=_route_paths,
    )
    if not specs and resolved_topic != "market" and _route_paths is None:
        specs = _select_specs(catalog_routes, query, "market")

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

    terms = _query_terms(query)
    cutoff = datetime.now() - timedelta(days=int(days))
    candidate_items = list(deduped.values())
    expired_count = sum(
        1 for item in candidate_items
        if _has_known_time(item) and not _item_is_recent(item, cutoff)
    )
    unknown_time_count = sum(1 for item in candidate_items if not _has_known_time(item))
    subject_terms = _matching_terms(query, resolved_topic)
    items = [item for item in candidate_items if _item_is_recent(item, cutoff)]
    ranking_terms = list(dict.fromkeys([*terms, *subject_terms]))
    items.sort(
        key=lambda item: (
            _trusted_route_bonus(item, subject_terms, resolved_topic) + _score(item, ranking_terms),
            _published_key(item),
        ),
        reverse=True,
    )
    if _filter_by_subject and subject_terms and resolved_topic != "market":
        relevant = [item for item in items if _matches_subject(item, subject_terms, resolved_topic)]
        selected = relevant[: max(1, min(int(limit), 30))]
    else:
        relevant = items
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
            })
        selected = selected[: max(1, min(int(limit), 30))]
        fallback_used = bool(selected)
        if fallback_used:
            warnings.append("RSSHub 未返回匹配记录，已使用通用联网搜索兜底")
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
            "relevant_item_count": sum(
                1 for item in result["result"].get("items") or []
                if _item_is_recent(item, cutoff)
                and (
                    not _filter_by_subject
                    or resolved_topic == "market"
                    or _matches_subject(item, subject_terms, resolved_topic)
                )
            ),
            "success": bool(result["result"].get("items")),
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
    if selected:
        source = f"websearch/{web_fallback.get('provider', 'unknown')}" if fallback_used and web_fallback else "RSSHub"
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
        "success": bool(selected),
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
                "enum": ["auto", "market", "company", "announcement", "research", "macro", "industry", "social"],
                "default": "auto",
                "description": "资讯类型，auto 会根据 query 自动判断",
            },
            "days": {"type": "integer", "minimum": 1, "maximum": 365, "default": 30, "description": "只返回最近多少天的记录；缺少发布时间的记录会保留并明确告警"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 30, "default": 12, "description": "去重后最多返回条数"},
            "include_content": {"type": "boolean", "default": False, "description": "是否在同一次调用中补取前 3 条正文；仅深入阅读时开启"},
            "fallback_to_web": {"type": "boolean", "default": True, "description": "RSS 无结果时是否自动调用联网搜索兜底"},
        },
        ["query"],
    ),
    executor=search_financial_news,
    category="sentiment",
)
