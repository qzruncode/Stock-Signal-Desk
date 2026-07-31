"""Official PBOC open-market-operation announcements from Infos/RSSHub."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urljoin, urlparse

from src.tools.base import ToolSpec, object_schema


_ROUTE = "/gov/pbc/tradeAnnouncement"
_OFFICIAL_LIST_URL = "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/index.html"
_INSTRUMENTS = {
    "outright_reverse_repo": ("买断式逆回购",),
    "reverse_repo": ("逆回购",),
    "mlf": ("中期借贷便利", "MLF"),
    "treasury_deposit": ("国库现金定存", "国库现金管理"),
    "central_bank_bill": ("央行票据",),
}


def _number(text: str, pattern: str) -> float | None:
    match = re.search(pattern, text, flags=re.I)
    return float(match.group(1)) if match else None


def _instrument(text: str) -> tuple[str, str]:
    for key, names in _INSTRUMENTS.items():
        if any(name.lower() in text.lower() for name in names):
            return key, names[0]
    return "other", "其他公开市场操作"


def _operation_legs(text: str) -> list[dict[str, Any]]:
    legs: list[dict[str, Any]] = []
    pattern = re.compile(
        r"(\d+(?:\.\d+)?)\s*(天|个月|月|年)(?:期)?\s+"
        r"(\d+(?:\.\d+)?)\s*%\s+"
        r"(\d+(?:\.\d+)?)\s*亿元\s+"
        r"(\d+(?:\.\d+)?)\s*亿元",
    )
    for term, unit, rate, bid, awarded in pattern.findall(text):
        legs.append(
            {
                "term": float(term),
                "term_unit": "month" if unit in {"个月", "月"} else "year" if unit == "年" else "day",
                "rate_pct": float(rate),
                "bid_amount_yi": float(bid),
                "awarded_amount_yi": float(awarded),
            }
        )
    return legs


def _operation_item(item: dict[str, Any], content: str) -> dict[str, Any]:
    text = re.sub(r"\s+", " ", f"{item.get('title', '')} {content}").strip()
    # PBOC HTML tables sometimes split decimals and units into separate spans
    # (``1. 40 %``, ``7 天``). Normalize only numeric punctuation so the
    # structured parser sees the same values as a human reader.
    text = re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ".", text)
    instrument_code, instrument = _instrument(text)
    legs = _operation_legs(text)
    rate_pct = (
        legs[0]["rate_pct"] if len(legs) == 1 else _number(text, r"(?:中标|操作)利率(?:为)?\s*(\d+(?:\.\d+)?)\s*%")
    )
    if rate_pct is None:
        rate_pct = _number(text, r"\d+(?:\.\d+)?\s*天(?:期)?\s+(\d+(?:\.\d+)?)\s*%")
    if rate_pct is None and instrument_code == "central_bank_bill":
        rate_pct = _number(text, r"(\d+(?:\.\d+)?)\s*%")
    amount = sum(leg["awarded_amount_yi"] for leg in legs) if legs else None
    if amount is None:
        amount = _number(text, r"(?:开展了|操作量为|中标量(?:为)?)\s*(\d+(?:\.\d+)?)\s*亿元")
    if amount is None and instrument_code == "central_bank_bill":
        amount = _number(text, r"央行票据[^。]{0,120}?(\d+(?:\.\d+)?)\s*亿元")
    if len(legs) == 1:
        term_days = legs[0]["term"] if legs[0]["term_unit"] == "day" else None
        term_months = legs[0]["term"] if legs[0]["term_unit"] == "month" else None
    else:
        term_days = _number(text, r"(\d+(?:\.\d+)?)\s*天期")
        term_months = _number(text, r"(\d+(?:\.\d+)?)\s*个?月期")
    if instrument_code == "central_bank_bill" and term_months is None:
        term_months = _number(text, r"(\d+(?:\.\d+)?)\s*个?月")
    bulletin = re.search(r"\[(\d{4})\]第(\d+)号", str(item.get("title") or ""))
    return {
        "title": item.get("title"),
        "published": item.get("published"),
        "link": item.get("link"),
        "bulletin_year": int(bulletin.group(1)) if bulletin else None,
        "bulletin_number": int(bulletin.group(2)) if bulletin else None,
        "instrument_code": instrument_code,
        "instrument": instrument,
        "term_days": term_days,
        "term_months": term_months,
        "amount_yi": amount,
        "rate_pct": rate_pct,
        "operation_legs": legs,
        "tender_method": "fixed_rate_quantity_tender" if "固定利率" in text and "数量招标" in text else None,
        "fully_satisfied": True if "全额满足" in text else None,
        "official": True,
        "source": "中国人民银行",
        "content": content[:2000],
    }


def _time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        return None


def _fetch_official_listing(cutoff: datetime) -> tuple[list[dict[str, Any]], list[str], bool]:
    """Read the PBOC directory directly and prove requested-window coverage."""
    import requests
    from bs4 import BeautifulSoup

    items: list[dict[str, Any]] = []
    errors: list[str] = []
    coverage_complete = False
    seen_links: set[str] = set()
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; DailyStockAnalysis/1.0)",
        "Cache-Control": "no-cache",
    }
    # 20 announcements per page.  Twenty pages safely cover the longest
    # supported 365-day query while remaining bounded.
    for page in range(1, 21):
        url = _OFFICIAL_LIST_URL if page == 1 else _OFFICIAL_LIST_URL.rsplit("/", 1)[0] + f"/17081-{page}.html"
        try:
            response = requests.get(url, timeout=15, headers=headers)
            response.raise_for_status()
            response.encoding = "utf-8"
            soup = BeautifulSoup(response.text, "html.parser")
        except Exception as exc:
            errors.append(f"人民银行公告目录第{page}页: {type(exc).__name__}: {exc}")
            break

        page_dates: list[datetime] = []
        page_count = 0
        for anchor in soup.find_all("a"):
            title = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True))
            if not re.search(r"公开市场业务交易公告\s*\[\d{4}\]第\d+号", title):
                continue
            link = urljoin(_OFFICIAL_LIST_URL, str(anchor.get("href") or ""))
            date_match = re.search(r"/(20\d{6})\d+/index\.html", link)
            if not date_match:
                parent_text = anchor.parent.get_text(" ", strip=True) if anchor.parent else ""
                date_match = re.search(r"(20\d{2})-(\d{2})-(\d{2})", parent_text)
                published = "-".join(date_match.groups()) if date_match else ""
            else:
                raw_date = date_match.group(1)
                published = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
            parsed = _time(published)
            if not parsed or link in seen_links:
                continue
            seen_links.add(link)
            page_dates.append(parsed)
            page_count += 1
            if parsed >= cutoff:
                items.append(
                    {
                        "title": title,
                        "published": published,
                        "link": link,
                        "id": link,
                        "summary": "",
                        "source_type": "official_directory",
                    }
                )
        if not page_count:
            errors.append(f"人民银行公告目录第{page}页没有解析到公告")
            break
        if page_dates and min(page_dates) <= cutoff:
            coverage_complete = True
            break

    items.sort(key=lambda item: str(item.get("published") or ""), reverse=True)
    return items, errors, coverage_complete


def _fetch_official_detail(url: str) -> str:
    import requests
    from bs4 import BeautifulSoup

    response = requests.get(
        url,
        timeout=15,
        headers={"User-Agent": "Mozilla/5.0 (compatible; DailyStockAnalysis/1.0)"},
    )
    response.raise_for_status()
    response.encoding = "utf-8"
    soup = BeautifulSoup(response.text, "html.parser")
    content = soup.select_one("#zoom") or soup.select_one(".content") or soup
    return re.sub(r"\s+", " ", content.get_text(" ", strip=True))


def get_monetary_policy_operations(
    days: int = 30,
    instrument: str = "all",
    limit: int = 20,
    include_content: bool = False,
    fallback_to_web: bool = True,
) -> dict[str, Any]:
    from api.v1.endpoints._rss_reader import read_feed, read_item

    days, limit = int(days), int(limit)
    instrument = str(instrument or "all").strip().lower()
    if not 1 <= days <= 365 or not 1 <= limit <= 50:
        raise ValueError("days 必须为 1..365，limit 必须为 1..50")
    if instrument not in {"all", *_INSTRUMENTS.keys(), "other"}:
        raise ValueError(f"不支持的 instrument: {instrument}")

    warnings: list[str] = []
    cutoff = datetime.now() - timedelta(days=days)
    official_items, official_errors, official_coverage_complete = _fetch_official_listing(cutoff)
    acquisition_type = "official_directory" if official_items else "rsshub"
    if official_items:
        all_feed_items = official_items
        feed_errors = official_errors
    else:
        feed = read_feed(route_path=_ROUTE, params={}, limit=50)
        all_feed_items = feed.get("items") or []
        feed_errors = [*official_errors, *(str(error) for error in feed.get("errors") or [])]
    recent_feed_items: list[dict[str, Any]] = []
    for item in all_feed_items:
        published = _time(item.get("published"))
        if published and published < cutoff:
            continue
        if isinstance(item, dict):
            recent_feed_items.append(item)

    detail_content: dict[int, str] = {}
    if recent_feed_items and (include_content or acquisition_type == "official_directory"):
        # Reading each announcement body serially made ten-item requests exceed
        # the Agent's entire tool timeout. For the unfiltered path only the
        # first ``limit`` rows can be returned; a specific instrument gets a
        # wider bounded candidate window before filtering.
        detail_candidates = recent_feed_items[
            : (limit if instrument == "all" else min(len(recent_feed_items), max(limit * 3, 12)))
        ]

        def _read_detail(index: int, item: dict[str, Any]) -> tuple[int, str, list[str]]:
            summary = str(item.get("summary") or "")
            try:
                if item.get("source_type") == "official_directory":
                    return index, _fetch_official_detail(str(item.get("link") or "")), []
                detail = read_item(
                    route_path=_ROUTE,
                    params={},
                    title=str(item.get("title") or ""),
                    item_id=str(item.get("id") or ""),
                    link=str(item.get("link") or ""),
                    list_summary=summary,
                )
                return (
                    index,
                    str(detail.get("content_text") or summary),
                    [f"正文读取: {error}" for error in detail.get("errors") or []],
                )
            except Exception as exc:
                return index, summary, [f"正文读取失败: {exc}"]

        with ThreadPoolExecutor(max_workers=min(6, len(detail_candidates))) as pool:
            futures = {pool.submit(_read_detail, index, item): index for index, item in enumerate(detail_candidates)}
            detail_warnings: dict[int, list[str]] = {}
            for future in as_completed(futures):
                index, content, item_warnings = future.result()
                detail_content[index] = content
                detail_warnings[index] = item_warnings
        for index in range(len(detail_candidates)):
            warnings.extend(detail_warnings.get(index, []))

    operations: list[dict[str, Any]] = []
    for index, item in enumerate(recent_feed_items):
        content = detail_content.get(index, str(item.get("summary") or ""))
        operation = _operation_item(item, content)
        if not include_content:
            operation["content"] = ""
        if instrument == "all" or operation["instrument_code"] == instrument:
            operations.append(operation)
        if len(operations) >= limit:
            break

    known_feed_times = [value for value in (_time(item.get("published")) for item in all_feed_items) if value]
    earliest_feed = min(known_feed_times, default=None)
    # The upstream PBOC listing itself is bounded, so receiving fewer than the
    # requested 50 rows does not prove the whole requested window was covered.
    coverage_complete = (
        official_coverage_complete
        if acquisition_type == "official_directory"
        else earliest_feed is not None and earliest_feed <= cutoff
    )
    if not coverage_complete:
        if acquisition_type == "official_directory":
            warnings.append(f"人民银行公告目录读取未能证明完整覆盖最近 {days} 天")
        else:
            warnings.append(f"RSSHub 最多读取最近 50 条公告，尚未完整覆盖最近 {days} 天")

    fallback_attempted = False
    fallback_used = False
    web_fallback = None
    if not all_feed_items and fallback_to_web:
        from src.tools.websearch import websearch

        fallback_attempted = True
        web_fallback = websearch("site:pbc.gov.cn 公开市场业务交易公告", num_results=min(limit, 10))
        for raw in web_fallback.get("results") or []:
            host = (urlparse(str(raw.get("url") or "")).hostname or "").lower()
            if not host.endswith("pbc.gov.cn"):
                continue
            item = {"title": raw.get("title"), "published": raw.get("published_date"), "link": raw.get("url")}
            operation = _operation_item(item, str(raw.get("snippet") or ""))
            if instrument == "all" or operation["instrument_code"] == instrument:
                operation["source_type"] = "websearch_official_domain"
                operations.append(operation)
        operations = operations[:limit]
        fallback_used = bool(operations)
        if fallback_used:
            warnings.append("人民银行 RSS 路由不可用，已使用人民银行官网域名搜索兜底")

    latest_values = [value for value in (_time(item.get("published")) for item in operations) if value]
    latest = max(latest_values, default=None)
    # A non-empty official feed remains useful even if the transport reports a
    # secondary warning. An error-free empty feed is also a valid acquisition.
    acquisition_success = bool(all_feed_items) or not feed_errors
    if acquisition_success and not operations:
        warnings.append("人民银行公告源读取成功，但筛选窗口内没有匹配操作")
    total_amount = sum(float(item["amount_yi"]) for item in operations if item.get("amount_yi") is not None)
    retrieved_at = datetime.now().astimezone().isoformat()
    return {
        "days": days,
        "instrument_filter": instrument,
        "operations": operations,
        "items": operations,
        "item_count": len(operations),
        "available_item_count": len(recent_feed_items),
        "result_truncated": (len(recent_feed_items) > len(operations) if instrument == "all" else None),
        "total_operation_amount_yi": round(total_amount, 4),
        "net_liquidity_injection_yi": None,
        "net_liquidity_note": "本路由仅披露当日操作，不含完整到期量，不能据此计算净投放",
        "rss_route": _ROUTE,
        "coverage_start": earliest_feed.isoformat() if earliest_feed else None,
        "coverage_end": max(known_feed_times).isoformat() if known_feed_times else None,
        "coverage_complete": coverage_complete,
        "source": (
            "中国人民银行官网/websearch"
            if fallback_used
            else (
                "中国人民银行官网公告目录"
                if acquisition_type == "official_directory" and acquisition_success
                else "中国人民银行/RSSHub" if acquisition_success else "none"
            )
        ),
        "success": acquisition_success or fallback_used,
        "partial": bool(feed_errors) and acquisition_success,
        "data_time": (
            latest.astimezone().isoformat() if latest and latest.tzinfo else latest.isoformat() if latest else None
        ),
        "retrieved_at": retrieved_at,
        "is_stale": latest < datetime.now() - timedelta(days=14) if latest else None,
        "freshness_unknown": latest is None,
        "fallback_attempted": fallback_attempted,
        "fallback_used": fallback_used,
        "fallback_recommended": not (acquisition_success or fallback_used),
        "web_fallback": web_fallback,
        "errors": list(dict.fromkeys(feed_errors))[:10],
        "warnings": list(dict.fromkeys(warnings))[:10],
    }


TOOL = ToolSpec(
    name="get_monetary_policy_operations",
    description=(
        "读取人民银行官方公开市场交易公告，解析逆回购、买断式逆回购、MLF等操作的期限、金额、利率、"
        "招标方式和公告编号。不会在缺少到期量时虚构净投放；RSS 失败时仅接受人民银行官网结果。"
    ),
    parameters=object_schema(
        {
            "days": {"type": "integer", "minimum": 1, "maximum": 365, "default": 30},
            "instrument": {
                "type": "string",
                "enum": [
                    "all",
                    "reverse_repo",
                    "outright_reverse_repo",
                    "mlf",
                    "treasury_deposit",
                    "central_bank_bill",
                    "other",
                ],
                "default": "all",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
            "include_content": {"type": "boolean", "default": False},
            "fallback_to_web": {"type": "boolean", "default": True},
        }
    ),
    executor=get_monetary_policy_operations,
    category="macro",
)
