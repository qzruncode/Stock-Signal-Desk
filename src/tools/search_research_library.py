"""Cross-institution research search over the useful Infos RSS routes."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any

from src.tools._akshare import bare_symbol
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

_FUTURES_ALIASES = {
    "black": ("黑色", "螺纹", "铁矿", "焦煤", "焦炭", "钢材"),
    "enchem": ("能化", "原油", "化工", "塑料", "沥青", "燃料油", "甲醇"),
    "nonfe": ("有色", "铜", "铝", "锌", "铅", "镍", "锡"),
    "agri": ("农产品", "豆粕", "玉米", "棉花", "白糖", "生猪", "油脂"),
    "bond": ("国债", "债券"),
    "exrate": ("外汇", "人民币", "美元", "汇率"),
    "option": ("期权",),
    "ship": ("航运", "集运"),
    "stockindex_IM/IF/IH/IC": ("股指", "沪深300", "中证500", "中证1000", "上证50"),
    "macro": ("宏观",),
}


def _infer_category(query: str, requested: str) -> str:
    if requested != "all":
        return requested
    text = query.lower()
    if any(word in text for word in ("穆迪", "评级", "信用", "违约", "城投")):
        return "rating"
    if any(word in text for words in _FUTURES_ALIASES.values() for word in words) or any(word in text for word in ("期货", "商品")):
        return "futures"
    if any(word in text for word in ("宏观", "经济", "货币", "利率", "通胀", "cpi", "pmi", "gdp", "社融")):
        return "macro"
    try:
        code = bare_symbol(query)
    except Exception:
        code = ""
    if re.fullmatch(r"\d{6}", code):
        return "stock"
    try:
        from src.data.stock_index_loader import get_stock_name_index_map

        if any(name and name in query for name in get_stock_name_index_map().values()):
            return "stock"
    except Exception:
        pass
    return "industry"


def _catalog_options(path: str) -> list[dict[str, str]]:
    try:
        from api.v1.endpoints._rss_catalog import get_rss_catalog

        route = next((row for row in get_rss_catalog().get("routes") or [] if row.get("route_path") == path), None)
        return [option for param in (route or {}).get("params") or [] for option in param.get("options") or []]
    except Exception:
        return []


def _matching_option(path: str, query: str, default: str | None = None) -> str | None:
    options = _catalog_options(path)
    if path.startswith("/moodysmismicrosite/"):
        aliases = {
            "地方政府及城投公司": ("城投", "地方政府"),
            "金融机构": ("银行", "保险", "金融机构"),
            "主权": ("主权", "国家评级"),
            "宏观经济": ("宏观", "经济", "通胀", "利率"),
            "基础设施及项目融资": ("基础设施", "项目融资"),
            "结构融资": ("结构融资",),
            "企业": ("企业", "公司信用"),
            "ESG": ("esg", "绿色", "气候"),
        }
        matched_label = next(
            (label for label, words in aliases.items() if any(word.lower() in query.lower() for word in words)),
            None,
        )
        if matched_label:
            return matched_label
    for option in options:
        label = str(option.get("label") or "")
        if label and (label in query or any(token in label for token in re.findall(r"[\u4e00-\u9fff]{2,}", query))):
            # The Moody's RSSHub route documents numeric option values but its
            # handler actually resolves Chinese labels. Other routes consume
            # the declared option value.
            return label if path.startswith("/moodysmismicrosite/") else str(option.get("value"))
    if default is not None and path.startswith("/moodysmismicrosite/"):
        matched = next((option for option in options if str(option.get("value")) == default), None)
        return str(matched.get("label")) if matched else default
    return default


def _nanhua_spec(query: str) -> dict[str, Any] | None:
    suffix = next((key for key, aliases in _FUTURES_ALIASES.items() if any(alias.lower() in query.lower() for alias in aliases)), None)
    if suffix is None:
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


def _cih_spec(query: str) -> dict[str, Any] | None:
    if not any(word in query for word in ("地产", "房地产", "住宅", "土地", "物业", "房企", "商业市场", "政策解读")):
        return None
    try:
        from api.v1.endpoints._cih_index_categories import get_cih_index_categories

        categories = get_cih_index_categories(force=False).get("categories") or []
        selected = next((row for row in categories if str(row.get("className") or "") in query), None)
        if selected is None:
            selected = next((row for row in categories if row.get("className") == "住宅市场"), None)
        report = f"f{selected['classId']}-p1-oaddtime-ddesc" if selected else "p1-oaddtime-ddesc"
        return {"path": "/cih-index/report/list/:report?", "params": {"report": report}, "source": "中指研究院"}
    except Exception:
        return {"path": "/cih-index/report/list/:report?", "params": {}, "source": "中指研究院"}


def _route_specs(query: str, category: str) -> list[dict[str, Any]]:
    if category == "stock":
        return [{"path": "/eastmoney/report/:category", "params": {"category": "stock"}, "source": "东方财富个股研报"}]
    if category == "rating":
        industry = _matching_option("/moodysmismicrosite/report/:industry?", query)
        params = {"industry": industry} if industry else {}
        return [{"path": "/moodysmismicrosite/report/:industry?", "params": params, "source": "穆迪评级"}]
    if category == "futures":
        specs = [{"path": "/wkjyqh/research", "params": {}, "source": "五矿期货"}]
        nanhua = _nanhua_spec(query)
        if nanhua:
            specs.append(nanhua)
        return specs
    if category == "macro":
        mck = _matching_option("/mckinsey/cn/:category?", query, "macroeconomy")
        moodys = _matching_option("/moodysmismicrosite/report/:industry?", query, "4")
        return [
            {"path": "/eastmoney/report/:category", "params": {"category": "macresearch"}, "source": "东方财富宏观研报"},
            {"path": "/mckinsey/cn/:category?", "params": {"category": mck}, "source": "麦肯锡"},
            {"path": "/moodysmismicrosite/report/:industry?", "params": {"industry": moodys}, "source": "穆迪评级"},
            {"path": "/nifd/research/:categoryGuid?", "params": {"categoryGuid": _NIFD_CATEGORIES["weekly"]}, "source": "国家金融与发展实验室"},
        ]
    specs = [
        {"path": "/eastmoney/report/:category", "params": {"category": "industry"}, "source": "东方财富行业研报"},
        {"path": "/qianzhan/analyst/column/:type?", "params": {"type": "all"}, "source": "前瞻研究"},
    ]
    cih = _cih_spec(query)
    if cih:
        specs.append(cih)
    mck = _matching_option("/mckinsey/cn/:category?", query)
    if mck:
        specs.append({"path": "/mckinsey/cn/:category?", "params": {"category": mck}, "source": "麦肯锡"})
    return specs


def _terms(query: str) -> list[str]:
    text = re.sub(
        r"(?:最新|近期|研报|研究报告|研究|报告|分析|行业|公司|股票|个股|景气|供需|风险|走势|现状|前景|展望)",
        " ", query, flags=re.I,
    )
    values = re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}", text.lower())
    values.extend(
        term for term in (
            "半导体", "人工智能", "新能源", "房地产", "消费", "医药", "银行", "保险",
            "城投", "信用", "违约", "主权", "通胀", "利率", "货币政策", "铁矿石", "原油",
            "铜", "铝", "黄金", "国债", "外汇", "股指", "期权",
        )
        if term.lower() in query.lower()
    )
    return list(dict.fromkeys([query.lower(), *values]))


_GENERIC_RESEARCH_TERMS = frozenset({
    "产业链", "价值链", "价值量", "市场空间", "竞争格局", "核心零部件",
    "受益环节", "国产替代", "订单", "量产", "产能", "交付", "降本",
    "业务", "主营", "收入", "客户", "验证", "公告", "实际", "检索", "查询",
    "搜索", "a股", "标的", "上市公司",
})

_HIGH_PRECISION_RESEARCH_SUBJECTS = (
    "人形机器人", "具身智能", "低空经济", "商业航天", "固态电池", "光模块",
)


def _subject_terms(query: str) -> list[str]:
    """Return topic-identifying terms, excluding generic research dimensions."""
    query_lower = query.lower().strip()
    precise = [term for term in _HIGH_PRECISION_RESEARCH_SUBJECTS if term in query_lower]
    terms = [
        term for term in _terms(query)
        if term != query_lower and term not in _GENERIC_RESEARCH_TERMS
    ]
    # Common compound topics are sometimes shortened in report titles.  Keep
    # the original high-precision term first, with a conservative alias after
    # it, instead of allowing generic words such as ``产业链`` to pass alone.
    if precise:
        return list(dict.fromkeys(precise))
    return list(dict.fromkeys(terms))


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        return None


def search_research_library(
    query: str,
    category: str = "all",
    days: int = 365,
    limit: int = 12,
    include_content: bool = False,
    fallback_to_web: bool = True,
) -> dict[str, Any]:
    from api.v1.endpoints._rss_reader import read_feed, read_item

    query = str(query or "").strip()
    category = str(category or "all").strip().lower()
    days, limit = int(days), int(limit)
    if not query:
        raise ValueError("query 不能为空")
    if category not in {"all", "stock", "industry", "macro", "futures", "rating"}:
        raise ValueError(f"不支持的 category: {category}")
    if not 1 <= days <= 3650 or not 1 <= limit <= 30:
        raise ValueError("days 必须为 1..3650，limit 必须为 1..30")
    resolved = _infer_category(query, category)
    if resolved == "stock":
        try:
            code = bare_symbol(query)
        except Exception:
            code = ""
        if not re.fullmatch(r"\d{6}", code):
            try:
                from src.data.stock_index_loader import get_stock_name_index_map

                code = next((stock_code for stock_code, stock_name in get_stock_name_index_map().items() if stock_name and stock_name in query), "")
            except Exception:
                code = ""
        if re.fullmatch(r"\d{6}", code):
            from src.tools.get_research_report import get_research_report

            stock_result = get_research_report(code, days=days, limit=limit)
            items = [
                {
                    "title": item.get("title"), "published": item.get("publish_date"),
                    "summary": "；".join(filter(None, [item.get("org"), item.get("rating")])),
                    "link": item.get("url"), "author": item.get("org"),
                    "source": item.get("source") or "东方财富券商研报",
                    "source_type": item.get("source_type"), "research_category": "stock",
                    "rating": item.get("rating"), "industry": item.get("industry"),
                    "profit_forecasts": item.get("profit_forecasts"),
                }
                for item in stock_result.get("items") or []
            ]
            return {
                "query": query, "requested_category": category, "research_category": "stock",
                "days": days, "items": items, "item_count": len(items),
                "source_coverage": [{
                    "source": stock_result.get("source"), "route_path": None,
                    "item_count": len(items), "success": bool(stock_result.get("success")),
                    "cached": bool(stock_result.get("_cached")), "errors": stock_result.get("errors") or [],
                }],
                "source": stock_result.get("source"), "success": bool(stock_result.get("success")),
                "partial": bool(stock_result.get("partial")), "data_time": stock_result.get("data_time"),
                "retrieved_at": stock_result.get("retrieved_at"), "is_stale": stock_result.get("is_stale"),
                "freshness_unknown": stock_result.get("freshness_unknown"),
                "fallback_attempted": bool(stock_result.get("fallback_attempted")),
                "fallback_used": bool(stock_result.get("fallback_used")),
                "fallback_recommended": bool(stock_result.get("fallback_recommended")),
                "web_fallback": None, "errors": stock_result.get("errors") or [],
                "warnings": stock_result.get("warnings") or [],
            }
    specs = _route_specs(query, resolved)
    cutoff = datetime.now() - timedelta(days=days)
    terms = _terms(query)
    subject_terms = _subject_terms(query)
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
        "query": {"type": "string", "description": "研究主题或股票代码/简称"},
        "category": {"type": "string", "enum": ["all", "stock", "industry", "macro", "futures", "rating"], "default": "all"},
        "days": {"type": "integer", "minimum": 1, "maximum": 3650, "default": 365},
        "limit": {"type": "integer", "minimum": 1, "maximum": 30, "default": 12},
        "include_content": {"type": "boolean", "default": False},
        "fallback_to_web": {"type": "boolean", "default": True},
    }, ["query"]),
    executor=search_research_library,
    category="research",
)
