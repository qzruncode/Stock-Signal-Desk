"""Official PBOC open-market-operation announcements from Infos/RSSHub."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from src.tools.base import ToolSpec, object_schema


_ROUTE = "/gov/pbc/tradeAnnouncement"
_INSTRUMENTS = {
    "outright_reverse_repo": ("买断式逆回购",),
    "reverse_repo": ("逆回购",),
    "mlf": ("中期借贷便利", "MLF"),
    "treasury_deposit": ("国库现金定存", "国库现金管理"),
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
        legs.append({
            "term": float(term), "term_unit": "month" if unit in {"个月", "月"} else "year" if unit == "年" else "day",
            "rate_pct": float(rate), "bid_amount_yi": float(bid), "awarded_amount_yi": float(awarded),
        })
    return legs


def _operation_item(item: dict[str, Any], content: str) -> dict[str, Any]:
    text = re.sub(r"\s+", " ", f"{item.get('title', '')} {content}").strip()
    instrument_code, instrument = _instrument(text)
    legs = _operation_legs(text)
    rate_pct = legs[0]["rate_pct"] if len(legs) == 1 else _number(text, r"(?:中标|操作)利率(?:为)?\s*(\d+(?:\.\d+)?)\s*%")
    if rate_pct is None:
        rate_pct = _number(text, r"\d+(?:\.\d+)?\s*天(?:期)?\s+(\d+(?:\.\d+)?)\s*%")
    amount = sum(leg["awarded_amount_yi"] for leg in legs) if legs else None
    if amount is None:
        amount = _number(text, r"(?:开展了|操作量为|中标量(?:为)?)\s*(\d+(?:\.\d+)?)\s*亿元")
    if len(legs) == 1:
        term_days = legs[0]["term"] if legs[0]["term_unit"] == "day" else None
        term_months = legs[0]["term"] if legs[0]["term_unit"] == "month" else None
    else:
        term_days = _number(text, r"(\d+(?:\.\d+)?)\s*天期")
        term_months = _number(text, r"(\d+(?:\.\d+)?)\s*个?月期")
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

    feed = read_feed(route_path=_ROUTE, params={}, limit=50)
    feed_errors = [str(error) for error in feed.get("errors") or []]
    warnings: list[str] = []
    cutoff = datetime.now() - timedelta(days=days)
    all_feed_items = feed.get("items") or []
    operations: list[dict[str, Any]] = []
    for item in all_feed_items:
        published = _time(item.get("published"))
        if published and published < cutoff:
            continue
        content = str(item.get("summary") or "")
        if include_content:
            try:
                detail = read_item(
                    route_path=_ROUTE, params={}, title=str(item.get("title") or ""),
                    item_id=str(item.get("id") or ""), link=str(item.get("link") or ""),
                    list_summary=content,
                )
                content = str(detail.get("content_text") or content)
                warnings.extend(f"正文读取: {error}" for error in detail.get("errors") or [])
            except Exception as exc:
                warnings.append(f"正文读取失败: {exc}")
        operation = _operation_item(item, content)
        if instrument == "all" or operation["instrument_code"] == instrument:
            operations.append(operation)
        if len(operations) >= limit:
            break

    known_feed_times = [value for value in (_time(item.get("published")) for item in all_feed_items) if value]
    earliest_feed = min(known_feed_times, default=None)
    # The upstream PBOC listing itself is bounded, so receiving fewer than the
    # requested 50 rows does not prove the whole requested window was covered.
    coverage_complete = earliest_feed is not None and earliest_feed <= cutoff
    if not coverage_complete:
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
        "total_operation_amount_yi": round(total_amount, 4),
        "net_liquidity_injection_yi": None,
        "net_liquidity_note": "本路由仅披露当日操作，不含完整到期量，不能据此计算净投放",
        "rss_route": _ROUTE,
        "coverage_start": earliest_feed.isoformat() if earliest_feed else None,
        "coverage_end": max(known_feed_times).isoformat() if known_feed_times else None,
        "coverage_complete": coverage_complete,
        "source": (
            "中国人民银行官网/websearch" if fallback_used
            else "中国人民银行/RSSHub" if acquisition_success
            else "none"
        ),
        "success": acquisition_success or fallback_used,
        "partial": bool(feed_errors) and acquisition_success,
        "data_time": latest.astimezone().isoformat() if latest and latest.tzinfo else latest.isoformat() if latest else None,
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
    parameters=object_schema({
        "days": {"type": "integer", "minimum": 1, "maximum": 365, "default": 30},
        "instrument": {"type": "string", "enum": ["all", "reverse_repo", "outright_reverse_repo", "mlf", "treasury_deposit", "other"], "default": "all"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
        "include_content": {"type": "boolean", "default": False},
        "fallback_to_web": {"type": "boolean", "default": True},
    }),
    executor=get_monetary_policy_operations,
    category="macro",
)
