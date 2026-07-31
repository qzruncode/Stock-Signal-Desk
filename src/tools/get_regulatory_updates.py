"""Official exchange disclosures, inquiry letters and listing-project updates."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urljoin, urlparse

from src.tools._akshare import bare_symbol
from src.tools.base import ToolSpec, object_schema


_PROJECT_TYPE = {"all": "0", "ipo": "1", "refinancing": "2", "restructuring": "3"}
_PROJECT_STAGE = {
    "all": "0",
    "accepted": "10",
    "inquiry": "20",
    "meeting": "30",
    "registration": "35",
    "result": "40",
    "suspended": "50",
    "terminated": "60",
}
_PROJECT_STATUS = {
    "all": "0",
    "newly_accepted": "20",
    "inquired": "30",
    "approved": "45",
    "rejected": "44",
    "registration_effective": "70",
    "withdrawn": "95",
}
_SZSE_LISTING_NOTICE_URL = "https://www.szse.cn/disclosure/notice/company/index.html"


def _resolve_subject(keyword: str) -> tuple[str | None, str | None]:
    if not keyword:
        return None, None
    try:
        code = bare_symbol(keyword)
    except Exception:
        code = ""
    if not re.fullmatch(r"\d{6}", code):
        match = re.search(r"(?<!\d)(\d{6})(?!\d)", keyword)
        code = match.group(1) if match else ""
    name = None
    if code:
        try:
            from src.data.stock_index_loader import get_index_stock_name

            name = get_index_stock_name(code)
        except Exception:
            pass
    return code or None, name


def _market(code: str | None, requested: str) -> str:
    if requested != "auto":
        return requested
    if not code:
        return "all"
    if code.startswith(("6", "5")):
        return "sse"
    if code.startswith(("0", "2", "3")):
        return "szse"
    return "bse"


def _specs(
    event_type: str,
    market: str,
    *,
    code: str | None,
    keyword: str,
    days: int,
    project_type: str,
    project_stage: str,
    project_status: str,
) -> list[dict[str, Any]]:
    end = datetime.now().date()
    begin = end - timedelta(days=days)
    query_parts = [f"beginDate={begin.isoformat()}", f"endDate={end.isoformat()}"]
    specs: list[dict[str, Any]] = []
    if event_type in {"all", "disclosure"}:
        if market in {"all", "sse"}:
            query = [*query_parts, *([f"productId={code}"] if code else [])]
            specs.append(
                {
                    "path": "/sse/disclosure/:query?",
                    "params": {"query": "&".join(query)},
                    "exchange": "SSE",
                    "kind": "disclosure",
                }
            )
        if market in {"all", "szse"}:
            query = [*([f"stock={code}"] if code else []), *query_parts]
            specs.append(
                {
                    "path": "/szse/disclosure/listed/notice/:query?",
                    "params": {"query": "&".join(query)},
                    "exchange": "SZSE",
                    "kind": "disclosure",
                }
            )
    if event_type in {"all", "inquiry"}:
        if market in {"all", "sse"}:
            specs.append({"path": "/sse/inquire", "params": {}, "exchange": "SSE", "kind": "inquiry"})
        if market in {"all", "szse"}:
            categories = ["1"] if code and code.startswith("3") else ["0"] if code else ["0", "1"]
            for category in categories:
                params = {"category": category, "select": "全部函件类别"}
                if code or keyword:
                    params["keyword"] = code or keyword
                specs.append(
                    {
                        "path": "/szse/inquire/:category?/:select?/:keyword?",
                        "params": params,
                        "exchange": "SZSE",
                        "kind": "inquiry",
                    }
                )
    if event_type in {"all", "project"}:
        if market in {"all", "sse"}:
            specs.append({"path": "/sse/renewal", "params": {}, "exchange": "SSE", "kind": "project"})
        if market in {"all", "szse"}:
            # The route defaults to IPO, so omitting ``type`` does not mean
            # all project types. Fan out explicitly for complete coverage.
            project_types = ["1", "2", "3"] if project_type == "all" else [_PROJECT_TYPE[project_type]]
            for type_value in project_types:
                specs.append(
                    {
                        "path": "/szse/projectdynamic/:type?/:stage?/:status?",
                        "params": {
                            "type": type_value,
                            "stage": _PROJECT_STAGE[project_stage],
                            "status": _PROJECT_STATUS[project_status],
                        },
                        "exchange": "SZSE",
                        "kind": "project",
                    }
                )
    if event_type in {"all", "listing_notice"} and market in {"all", "szse"}:
        # Despite the stale RSSHub catalog label "可转换债券", the route
        # implementation fetches SZSE /disclosure/notice/company/index.html:
        # exchange notices for company listing/termination, not convertible
        # bond issuance notices.
        specs.append({"path": "/szse/notice", "params": {}, "exchange": "SZSE", "kind": "listing_notice"})
    return specs


def _published(raw: Any) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(raw or "").replace("Z", "+00:00"))
        return value.astimezone().replace(tzinfo=None) if value.tzinfo else value
    except ValueError:
        return None


def _matches(item: dict[str, Any], keyword: str, code: str | None, name: str | None) -> bool:
    if not keyword:
        return True
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    terms = [value.lower() for value in (code, name, keyword) if value and len(value) >= 2]
    return any(term in text for term in terms)


def _official_web_host(host: str, market: str) -> bool:
    roots = ("sse.com.cn",) if market == "sse" else ("szse.cn",) if market == "szse" else ("sse.com.cn", "szse.cn")
    return any(host == root or host.endswith(f".{root}") for root in roots)


def _web_result_date(raw: dict[str, Any]) -> datetime | None:
    published = _published(raw.get("published_date"))
    if published:
        return published
    url = str(raw.get("url") or "")
    match = re.search(r"/(20\d{2})-(\d{2})-(\d{2})/", url)
    if match:
        try:
            return datetime(*map(int, match.groups()))
        except ValueError:
            pass
    match = re.search(r"[t_/](20\d{6})(?:[_./-]|$)", url)
    if match:
        try:
            return datetime.strptime(match.group(1), "%Y%m%d")
        except ValueError:
            pass
    return None


def _parse_szse_listing_page(html: str, days: int, limit: int) -> list[dict[str, Any]]:
    from bs4 import BeautifulSoup

    cutoff = datetime.now() - timedelta(days=days)
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    for node in soup.select(".article-list .newslist li"):
        script = node.find("script")
        script_text = script.get_text(" ", strip=True) if script else ""
        href_match = re.search(r"curHref\s*=\s*['\"]([^'\"]+)['\"]", script_text)
        title_match = re.search(r"curTitle\s*=\s*['\"]([^'\"]+)['\"]", script_text)
        date_node = node.select_one("span.time")
        published = _published(date_node.get_text(" ", strip=True) if date_node else "")
        if not href_match or not title_match or published is None or published < cutoff:
            continue
        items.append(
            {
                "title": re.sub(r"\s+", " ", title_match.group(1)).strip(),
                "published": published.isoformat(),
                "summary": "",
                "link": urljoin(_SZSE_LISTING_NOTICE_URL, href_match.group(1)),
                "source": "SZSE official listing notice page",
                "exchange": "SZSE",
                "event_type": "listing_notice",
                "project_status": None,
                "company_code": None,
                "official": True,
                "source_type": "official_exchange_html",
            }
        )
        if len(items) >= limit:
            break
    return items


def _read_szse_listing_page(days: int, limit: int) -> dict[str, Any]:
    import httpx

    try:
        response = httpx.get(
            _SZSE_LISTING_NOTICE_URL,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; DailyStockAnalysis/1.0)",
                "Referer": "https://www.szse.cn/",
            },
            timeout=15,
            follow_redirects=True,
        )
        response.raise_for_status()
        items = _parse_szse_listing_page(response.text, days, limit)
        # The page may legitimately have no rows inside a short requested
        # window, but a missing list container means the scraper contract has
        # changed and must not be reported as a valid zero.
        if "article-list" not in response.text or "newslist" not in response.text:
            raise RuntimeError("深交所上市公告页面结构已变化")
        return {"success": True, "items": items, "error": None, "source_url": str(response.url)}
    except Exception as exc:
        return {"success": False, "items": [], "error": str(exc), "source_url": _SZSE_LISTING_NOTICE_URL}


def _normalized_item(raw: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    title = re.sub(r"\s+", " ", str(raw.get("title") or "")).strip()
    status_match = re.match(r"[【\[]([^】\]]+)[】\]]", title)
    code_match = re.search(r"[\[(]?(\d{6})[\])]?[\s:：-]+", title)
    return {
        "title": title,
        "published": raw.get("published"),
        "summary": str(raw.get("summary") or "").strip(),
        "link": str(raw.get("link") or "").strip(),
        "source": f"{spec['exchange']} official disclosure via RSSHub",
        "exchange": spec["exchange"],
        "event_type": spec["kind"],
        "project_status": status_match.group(1) if status_match else None,
        "company_code": code_match.group(1) if code_match else None,
        "official": True,
        "rss_route": spec["path"],
        "rss_params": spec["params"],
    }


def get_regulatory_updates(
    keyword: str = "",
    event_type: str = "all",
    market: str = "auto",
    days: int = 90,
    limit: int = 12,
    include_content: bool = False,
    fallback_to_web: bool = True,
    project_type: str = "all",
    project_stage: str = "all",
    project_status: str = "all",
) -> dict[str, Any]:
    from api.v1.endpoints._rss_reader import read_feed, read_item

    keyword = str(keyword or "").strip()
    event_type = str(event_type or "all").strip().lower()
    requested_market = str(market or "auto").strip().lower()
    project_type = str(project_type or "all").strip().lower()
    project_stage = str(project_stage or "all").strip().lower()
    project_status = str(project_status or "all").strip().lower()
    days, limit = int(days), int(limit)
    if event_type not in {"all", "disclosure", "inquiry", "project", "listing_notice"}:
        raise ValueError(f"不支持的 event_type: {event_type}")
    if requested_market not in {"auto", "all", "sse", "szse", "bse"}:
        raise ValueError(f"不支持的 market: {requested_market}")
    if (
        project_type not in _PROJECT_TYPE
        or project_stage not in _PROJECT_STAGE
        or project_status not in _PROJECT_STATUS
    ):
        raise ValueError("不支持的项目类型、阶段或状态")
    if not 1 <= days <= 730 or not 1 <= limit <= 50:
        raise ValueError("days 必须为 1..730，limit 必须为 1..50")

    code, name = _resolve_subject(keyword)
    resolved_market = _market(code, requested_market)
    specs = _specs(
        event_type,
        resolved_market,
        code=code,
        keyword=keyword,
        days=days,
        project_type=project_type,
        project_stage=project_stage,
        project_status=project_status,
    )
    errors: list[str] = []
    warnings: list[str] = []
    route_meta: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []

    def fetch(spec: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        result = read_feed(
            route_path=spec["path"],
            params=spec["params"],
            limit=50,
            fallback_to_xml=False,
        )
        return spec, result

    # ``event_type=all`` fans out to as many as ten independent exchange
    # routes. Four workers forced three serial waves and regularly exhausted
    # the Agent's 45-second tool budget even though every upstream was healthy.
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(specs)))) as pool:
        futures = [pool.submit(fetch, spec) for spec in specs]
        for future in as_completed(futures):
            try:
                spec, result = future.result()
            except Exception as exc:
                errors.append(f"官方 RSS 路由读取失败: {exc}")
                continue
            route_errors = [str(error) for error in result.get("errors") or []]
            errors.extend(f"{spec['path']}: {error}" for error in route_errors)
            route_meta.append(
                {
                    "route_path": spec["path"],
                    "params": spec["params"],
                    "exchange": spec["exchange"],
                    "event_type": spec["kind"],
                    "item_count": len(result.get("items") or []),
                    "success": not route_errors,
                    "cached": bool(result.get("_cached")),
                    "errors": route_errors,
                }
            )
            candidates.extend(_normalized_item(item, spec) for item in result.get("items") or [])

    cutoff = datetime.now() - timedelta(days=days)
    filtered: list[dict[str, Any]] = []
    unknown_dates = 0
    for item in candidates:
        published = _published(item.get("published"))
        if published is None:
            unknown_dates += 1
        elif published < cutoff:
            continue
        if _matches(item, keyword, code, name):
            filtered.append(item)
    if unknown_dates:
        warnings.append(f"{unknown_dates} 条官方记录缺少可解析发布时间")
    deduped: dict[str, dict[str, Any]] = {}
    for item in filtered:
        key = re.sub(r"\s+", "", item["title"]).lower() or item["link"]
        existing = deduped.get(key)
        if existing is None or (item["event_type"] == "inquiry" and existing["event_type"] != "inquiry"):
            deduped[key] = item
    items = sorted(deduped.values(), key=lambda row: str(row.get("published") or ""), reverse=True)[:limit]

    rss_acquisition_success = bool(route_meta) and any(route["success"] for route in route_meta)
    failed_routes = sum(not route["success"] for route in route_meta)
    listing_route_failed = any(row["route_path"] == "/szse/notice" and not row["success"] for row in route_meta)
    official_page_fallback = None
    official_page_used = False
    if listing_route_failed and event_type in {"all", "listing_notice"}:
        official_page_fallback = _read_szse_listing_page(days, limit)
        if official_page_fallback["success"]:
            direct_items = [item for item in official_page_fallback["items"] if _matches(item, keyword, code, name)]
            existing = {item.get("link") for item in items}
            items.extend(item for item in direct_items if item.get("link") not in existing)
            items = sorted(items, key=lambda row: str(row.get("published") or ""), reverse=True)[:limit]
            official_page_used = True
            warnings.append("RSSHub 上市公告路由不可用，已直接读取深交所官方上市公司公告页")
        else:
            errors.append(f"深交所上市公告官网直读: {official_page_fallback['error']}")

    if include_content:

        def fetch_content(item: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None, Exception | None]:
            try:
                if item.get("source_type") == "official_exchange_html":
                    from src.tools.webfetch import fetch_url

                    detail = fetch_url(item["link"], format="text")
                else:
                    detail = read_item(
                        route_path=item["rss_route"],
                        params=item["rss_params"],
                        title=item["title"],
                        link=item["link"],
                        list_summary=item["summary"],
                    )
                return item, detail, None
            except Exception as exc:
                return item, None, exc

        detail_items = items[:3]
        # Full-text requests are independent. Serial 45-second RSS detail
        # fallbacks made a healthy three-item result take minutes in the worst
        # case, so run the bounded detail window concurrently.
        with ThreadPoolExecutor(max_workers=max(1, len(detail_items))) as pool:
            futures = [pool.submit(fetch_content, item) for item in detail_items]
            for future in as_completed(futures):
                item, detail, error = future.result()
                if error is not None or detail is None:
                    warnings.append(f"{item['title'][:40]} 正文读取失败: {error}")
                    continue
                if item.get("source_type") == "official_exchange_html":
                    item["content_text"] = detail.get("content") or item["summary"]
                    item["content_fallback"] = True
                else:
                    item["content_text"] = detail.get("content_text") or item["summary"]
                    item["content_fallback"] = bool(detail.get("_fallback"))

    acquisition_success = rss_acquisition_success or bool(official_page_fallback and official_page_fallback["success"])
    fallback_attempted = False
    fallback_used = False
    web_fallback = None
    # An empty, successfully-read official feed is a valid zero-result answer,
    # not a search failure. Web fallback is reserved for complete acquisition
    # failure so old/out-of-window search hits cannot masquerade as regulatory
    # events inside the requested window.
    if not items and not acquisition_success and fallback_to_web and resolved_market != "bse":
        from src.tools.websearch import websearch

        domains = (
            "site:sse.com.cn OR site:szse.cn"
            if resolved_market == "all"
            else "site:sse.com.cn" if resolved_market == "sse" else "site:szse.cn"
        )
        fallback_attempted = True
        intent_label = {
            "all": "监管披露",
            "disclosure": "上市公司公告",
            "inquiry": "监管问询函",
            "project": "IPO 再融资 项目动态",
            "listing_notice": "股票 上市交易 终止上市 公告",
        }[event_type]
        web_fallback = websearch(
            f"{domains} {keyword} {intent_label} {datetime.now().year}".strip(),
            num_results=min(limit, 10),
        )
        rejected_web_results = 0
        for raw in web_fallback.get("results") or []:
            host = (urlparse(str(raw.get("url") or "")).hostname or "").lower()
            title = str(raw.get("title") or "").strip()
            published = _web_result_date(raw)
            candidate = {"title": title, "summary": raw.get("snippet")}
            if (
                not _official_web_host(host, resolved_market)
                or not _matches(candidate, keyword, code, name)
                or published is None
                or published < cutoff
            ):
                rejected_web_results += 1
                continue
            items.append(
                {
                    "title": title,
                    "published": published.isoformat(),
                    "summary": raw.get("snippet"),
                    "link": raw.get("url"),
                    "source": host,
                    "exchange": "SSE" if host == "sse.com.cn" or host.endswith(".sse.com.cn") else "SZSE",
                    "event_type": event_type,
                    "project_status": None,
                    "company_code": code,
                    "official": True,
                    "source_type": "websearch_official_domain",
                    "semantic_status": "model_required",
                }
            )
        items = items[:limit]
        fallback_used = bool(items)
        if fallback_used:
            warnings.append("官方 RSS 路由无匹配记录，已用交易所官网域名搜索兜底")
        elif rejected_web_results:
            warnings.append("网页兜底未找到同时满足官方域名、查询主体和时间窗的监管记录")

    if failed_routes and rss_acquisition_success:
        warnings.append(f"{failed_routes}/{len(route_meta)} 条官方 RSS 路由不可用，结果来自其余可用官方路由")
    if resolved_market == "bse":
        warnings.append("47 条 Infos 路由不包含北交所监管路由；北交所公司公告请使用 get_announcements")
    if acquisition_success and not items and not fallback_used:
        warnings.append("官方路由读取成功，但查询窗口内没有匹配记录")
    known_times = [value for value in (_published(item.get("published")) for item in items) if value]
    latest = max(known_times, default=None)
    retrieved_at = datetime.now().astimezone().isoformat()
    return {
        "keyword": keyword or None,
        "resolved_code": code,
        "resolved_name": name,
        "event_type": event_type,
        "market": resolved_market,
        "days": days,
        "project_filters": {"type": project_type, "stage": project_stage, "status": project_status},
        "items": items,
        "item_count": len(items),
        "has_updates": bool(items),
        "rss_routes": route_meta,
        "source": (
            "交易所官网/websearch"
            if fallback_used
            else (
                "交易所官方披露/RSSHub+官网直读"
                if official_page_used and rss_acquisition_success
                else (
                    "深交所官网直读"
                    if official_page_used
                    else "交易所官方披露/RSSHub" if acquisition_success else "none"
                )
            )
        ),
        "source_scope": "SSE_and_SZSE_official_regulatory_disclosures",
        "success": acquisition_success or fallback_used,
        "partial": bool(errors) and (acquisition_success or fallback_used),
        "data_time": (
            latest.astimezone().isoformat() if latest and latest.tzinfo else latest.isoformat() if latest else None
        ),
        "retrieved_at": retrieved_at,
        "is_stale": latest < datetime.now() - timedelta(days=max(14, days)) if latest else None,
        "freshness_unknown": latest is None,
        "fallback_attempted": fallback_attempted or official_page_fallback is not None,
        "fallback_used": fallback_used or official_page_used,
        "fallback_channel": "websearch" if fallback_used else "official_page" if official_page_used else None,
        "official_page_fallback": official_page_fallback,
        "fallback_recommended": not (acquisition_success or fallback_used),
        "web_fallback": web_fallback,
        "errors": list(dict.fromkeys(errors))[:10],
        "warnings": list(dict.fromkeys(warnings))[:10],
    }


TOOL = ToolSpec(
    name="get_regulatory_updates",
    description=(
        "直接读取上交所和深交所官方披露路由：上市公司公告、监管问询、科创板/创业板IPO与再融资项目动态、"
        "深交所公司上市及终止上市公告。支持代码/简称、日期、交易所和项目阶段过滤；北交所覆盖缺口会明确说明。"
    ),
    parameters=object_schema(
        {
            "keyword": {"type": "string", "default": "", "description": "股票代码、简称或项目关键词"},
            "event_type": {
                "type": "string",
                "enum": ["all", "disclosure", "inquiry", "project", "listing_notice"],
                "default": "all",
            },
            "market": {"type": "string", "enum": ["auto", "all", "sse", "szse", "bse"], "default": "auto"},
            "days": {"type": "integer", "minimum": 1, "maximum": 730, "default": 90},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 12},
            "include_content": {"type": "boolean", "default": False},
            "fallback_to_web": {"type": "boolean", "default": True},
            "project_type": {"type": "string", "enum": list(_PROJECT_TYPE), "default": "all"},
            "project_stage": {"type": "string", "enum": list(_PROJECT_STAGE), "default": "all"},
            "project_status": {"type": "string", "enum": list(_PROJECT_STATUS), "default": "all"},
        }
    ),
    executor=get_regulatory_updates,
    category="regulatory",
)
