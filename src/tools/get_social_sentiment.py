"""Transparent public-discussion sample for one A-share stock.

The tool collects and labels source provenance only.  It deliberately does not
infer sentiment from word lists; semantic polarity is evaluated later by the
model together with context and negation.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any

from src.tools._akshare import bare_symbol, cached_call, frame_records
from src.tools.base import ToolSpec, object_schema


def _parse_count(value: Any) -> int:
    text = str(value or "").strip().replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return 0
    number = float(match.group(1))
    if "万" in text:
        number *= 10_000
    return int(number)


def _post_time(value: str, *, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now().astimezone()
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    for pattern, fmt in (
        (r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}", "%Y-%m-%d %H:%M"),
        (r"\d{2}-\d{2}\s+\d{2}:\d{2}", "%m-%d %H:%M"),
    ):
        match = re.search(pattern, text)
        if not match:
            continue
        if fmt.startswith("%m"):
            parsed = datetime.strptime(f"{now.year}-{match.group(0)}", "%Y-%m-%d %H:%M")
            if parsed > now.replace(tzinfo=None) + timedelta(days=2):
                parsed = parsed.replace(year=now.year - 1)
        else:
            parsed = datetime.strptime(match.group(0), fmt)
        return parsed.astimezone()
    return None


def _fetch_guba_page(code: str, page_number: int) -> dict[str, Any]:
    from scrapling.fetchers import Fetcher

    url = f"https://guba.eastmoney.com/list,{code},{page_number},f.html"
    page = Fetcher.get(
        url,
        impersonate="chrome",
        timeout=15,
        retries=2,
        follow_redirects="safe",
    )
    if int(page.status) != 200:
        raise RuntimeError(f"HTTP {page.status}")
    items: list[dict[str, Any]] = []
    for row in page.css(".listitem"):
        anchor = row.css("a[data-postid]").first
        if anchor is None:
            continue
        title = re.sub(r"\s+", " ", str(anchor.css("::text").get() or "")).strip()
        href = str(anchor.attrib.get("href") or "").strip()
        if not title or not href:
            continue
        author_node = row.css(".author").first
        author = re.sub(
            r"\s+",
            " ",
            str(author_node.get_all_text(strip=True) if author_node is not None else ""),
        ).strip()
        raw_time = str(row.css(".update::text").get() or "").strip()
        published = _post_time(raw_time)
        items.append({
            "title": title,
            "source": "东方财富股吧",
            "author": author or None,
            "post_kind": "public_discussion",
            "url": row.urljoin(href),
            "read_count": _parse_count(row.css(".read::text").get()),
            "reply_count": _parse_count(row.css(".reply::text").get()),
            "publish_time": published.isoformat() if published else None,
            "semantic_status": "model_required",
            "page_number": page_number,
        })
    return {"items": items, "url": url, "status": int(page.status)}


def _fetch_guba_sample(
    code: str,
    *,
    days: int,
    max_pages: int,
    use_cache: bool,
) -> tuple[list[dict[str, Any]], bool, list[str], bool, bool]:
    errors: list[str] = []
    pages: list[dict[str, Any]] = []
    any_cached = True
    with ThreadPoolExecutor(max_workers=min(3, max_pages)) as pool:
        futures = {}
        for page_number in range(1, max_pages + 1):
            future = pool.submit(
                cached_call,
                f"guba:{code}:page:{page_number}",
                lambda page_number=page_number: _fetch_guba_page(code, page_number),
                ttl_seconds=600 if use_cache else 0,
                attempts=1,
            )
            futures[future] = page_number
        for future in as_completed(futures):
            page_number = futures[future]
            try:
                page, cached = future.result()
                any_cached = any_cached and cached
                pages.append(page)
            except Exception as exc:
                errors.append(f"东方财富股吧第 {page_number} 页: {exc}")
    pages.sort(key=lambda page: min((item.get("page_number", 0) for item in page.get("items") or []), default=0))
    cutoff = datetime.now().astimezone() - timedelta(days=days)
    raw_items = [item for page in pages for item in page.get("items") or []]
    raw_times = [
        datetime.fromisoformat(str(item["publish_time"]))
        for item in raw_items
        if item.get("publish_time")
    ]
    reached_cutoff = bool(raw_times) and min(raw_times) <= cutoff
    if pages and any(len(page.get("items") or []) < 80 for page in pages):
        reached_cutoff = True
    items = [
        item
        for item in raw_items
        if not item.get("publish_time")
        or datetime.fromisoformat(str(item["publish_time"])) >= cutoff
    ]
    return items, bool(pages), errors, any_cached if pages else False, reached_cutoff


def _fetch_stock_news_fallback(
    code: str,
    *,
    days: int,
    limit: int,
    use_cache: bool,
) -> tuple[list[dict[str, Any]], bool]:
    """Return company-news summaries when the public Guba list is unavailable.

    These rows are explicitly marked as syndicated information.  They preserve
    useful context for the Agent but never masquerade as user-authored social
    sentiment evidence.
    """
    import akshare as ak

    def fetch():
        frame = ak.stock_news_em(symbol=code)
        if frame is None or frame.empty:
            raise RuntimeError("AKShare stock_news_em 没有返回公司新闻")
        return frame

    frame, cached = cached_call(
        f"social-news-fallback:{code}",
        fetch,
        ttl_seconds=1800 if use_cache else 0,
        attempts=2,
    )
    cutoff = datetime.now().astimezone() - timedelta(days=days)
    items: list[dict[str, Any]] = []
    for row in frame_records(frame):
        title = re.sub(r"\s+", " ", str(row.get("新闻标题") or "")).strip()
        if not title:
            continue
        summary = re.sub(r"\s+", " ", str(row.get("新闻内容") or "")).strip()[:700]
        published = None
        raw_time = str(row.get("发布时间") or "").strip()
        if raw_time:
            try:
                published = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                if published.tzinfo is None:
                    published = published.astimezone()
            except ValueError:
                published = _post_time(raw_time)
        if published and published < cutoff:
            continue
        items.append({
            "title": title,
            "summary": summary,
            "source": str(row.get("文章来源") or "东方财富公司新闻").strip(),
            "author": None,
            "post_kind": "syndicated_info",
            "url": str(row.get("新闻链接") or "").strip(),
            "read_count": None,
            "reply_count": None,
            "publish_time": published.isoformat() if published else None,
            "semantic_status": "not_social_evidence",
            "page_number": None,
        })
    items.sort(key=lambda item: item.get("publish_time") or "", reverse=True)
    return items[:limit], cached


def _fetch_xueqiu_mentions(code: str, name: str | None, *, days: int) -> tuple[list[dict[str, Any]], list[str]]:
    from api.v1.endpoints._rss_reader import read_feed

    result = read_feed(route_path="/xueqiu/hots", params={}, limit=50)
    terms = [code, *([name] if name else [])]
    cutoff = datetime.now().astimezone() - timedelta(days=days)
    items: list[dict[str, Any]] = []
    for raw in result.get("items") or []:
        title = re.sub(r"\s+", " ", str(raw.get("title") or "")).strip()
        summary = re.sub(r"\s+", " ", str(raw.get("summary") or "")).strip()
        if not any(term and term in f"{title} {summary}" for term in terms):
            continue
        published_text = str(raw.get("published") or "").strip()
        try:
            published = datetime.fromisoformat(published_text.replace("Z", "+00:00")) if published_text else None
        except ValueError:
            published = None
        if published and published.tzinfo is None:
            published = published.astimezone()
        if published and published < cutoff:
            continue
        items.append({
            "title": title,
            "source": "雪球热榜/RSSHub",
            "author": str(raw.get("author") or "").strip() or None,
            "post_kind": "public_discussion",
            "url": str(raw.get("link") or "").strip(),
            "read_count": None,
            "reply_count": None,
            "publish_time": published.isoformat() if published else None,
            "semantic_status": "model_required",
            "page_number": None,
        })
    return items, [str(error) for error in result.get("errors") or []]


def _fetch_diagnose_score(code: str, *, days: int) -> tuple[list[dict[str, Any]], float | None, list[str]]:
    import httpx

    try:
        response = httpx.get(
            "https://datacenter-web.eastmoney.com/api/data/v1/get",
            params={
                "filter": f'(SECURITY_CODE="{code}")',
                "columns": "DIAGNOSE_DATE,TOTAL_SCORE,CLOSE",
                "source": "WEB",
                "client": "WEB",
                "reportName": "RPT_STOCK_HISTORYMARK",
                "sortColumns": "DIAGNOSE_DATE",
                "sortTypes": "1",
                "pageSize": min(max(days * 2, 30), 500),
            },
            timeout=15,
        )
        response.raise_for_status()
        rows = ((response.json().get("result") or {}).get("data") or [])
    except Exception as exc:
        return [], None, [f"东方财富千股千评: {exc}"]
    cutoff = datetime.now() - timedelta(days=days)
    trend: list[dict[str, Any]] = []
    for row in rows:
        raw_date = str(row.get("DIAGNOSE_DATE") or "")[:10]
        try:
            date_value = datetime.fromisoformat(raw_date)
        except ValueError:
            continue
        if date_value < cutoff:
            continue
        try:
            score = float(row["TOTAL_SCORE"]) if row.get("TOTAL_SCORE") is not None else None
        except (TypeError, ValueError):
            score = None
        try:
            close = float(row["CLOSE"]) if row.get("CLOSE") is not None else None
        except (TypeError, ValueError):
            close = None
        trend.append({"date": raw_date, "score": round(score, 1) if score is not None else None, "close": close})
    trend.sort(key=lambda item: item["date"])
    current = next((item["score"] for item in reversed(trend) if item.get("score") is not None), None)
    return trend, current, []


def get_social_sentiment(
    symbol: str,
    days: int = 30,
    limit: int = 50,
    max_pages: int = 3,
    use_cache: bool = True,
) -> dict[str, Any]:
    code = bare_symbol(symbol)
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError(f"无法识别 A 股代码: {symbol}")
    days = int(days)
    limit = int(limit)
    max_pages = int(max_pages)
    if not 1 <= days <= 180:
        raise ValueError("days 必须在 1 到 180 之间")
    if not 1 <= limit <= 100:
        raise ValueError("limit 必须在 1 到 100 之间")
    if not 1 <= max_pages <= 10:
        raise ValueError("max_pages 必须在 1 到 10 之间")

    try:
        from src.data.stock_index_loader import get_index_stock_name

        name = get_index_stock_name(code)
    except Exception:
        name = None

    guba_items, guba_available, guba_errors, cached, coverage_complete = _fetch_guba_sample(
        code,
        days=days,
        max_pages=max_pages,
        use_cache=use_cache,
    )
    fallback_items: list[dict[str, Any]] = []
    fallback_cached = False
    fallback_used = False
    if not guba_available:
        try:
            fallback_items, fallback_cached = _fetch_stock_news_fallback(
                code,
                days=days,
                limit=limit,
                use_cache=use_cache,
            )
            fallback_used = bool(fallback_items)
        except Exception as exc:
            guba_errors.append(f"AKShare公司新闻降级: {exc}")
    xueqiu_items, xueqiu_errors = _fetch_xueqiu_mentions(code, name, days=days)
    score_trend, diagnose_score, diagnose_errors = _fetch_diagnose_score(code, days=days)

    combined = [*guba_items, *xueqiu_items, *fallback_items]
    combined.sort(key=lambda item: item.get("publish_time") or "", reverse=True)
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in combined:
        key = str(item.get("url") or "").strip() or re.sub(r"\s+", "", str(item.get("title") or "")).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    discussion_items = [
        item for item in deduped if item.get("post_kind") == "public_discussion"
    ]
    syndicated_sample = [item for item in deduped if item.get("post_kind") == "syndicated_info"]
    items = [*discussion_items, *syndicated_sample][:limit]
    total_read = sum(int(item.get("read_count") or 0) for item in deduped)
    total_reply = sum(int(item.get("reply_count") or 0) for item in deduped)
    known_times = [
        datetime.fromisoformat(str(item["publish_time"]))
        for item in deduped
        if item.get("publish_time")
    ]
    earliest_time = min(known_times) if known_times else None
    latest_time = max(known_times) if known_times else None
    earliest = earliest_time.isoformat() if earliest_time else None
    latest = latest_time.isoformat() if latest_time else None
    cutoff = datetime.now().astimezone() - timedelta(days=days)
    if guba_available and not coverage_complete:
        coverage_warning = (
            f"股吧仅采样最近 {max_pages} 页，实际最早帖子为 {earliest or '未知'}，"
            f"未覆盖完整的最近 {days} 天"
        )
    else:
        coverage_warning = None

    errors = list(dict.fromkeys([*guba_errors, *diagnose_errors]))
    warnings = [f"RSSHub/雪球热榜: {error}" for error in xueqiu_errors]
    if fallback_used:
        warnings.append("东方财富股吧抓取失败，已降级为 AKShare/东方财富公司新闻摘要；这些资讯不计入用户情绪分数")
    if coverage_warning:
        warnings.append(coverage_warning)
    if not discussion_items:
        warnings.append("时间窗内没有取得可供模型研判的公开讨论样本")
    acquisition_succeeded = guba_available or bool(xueqiu_items) or fallback_used
    if not items and acquisition_succeeded:
        warnings.append("已完成公开讨论源采样，但没有取得时间窗内可用帖子")
    daily: dict[str, dict[str, int]] = {}
    for item in deduped:
        date_key = str(item.get("publish_time") or "")[:10]
        if not date_key:
            continue
        row = daily.setdefault(date_key, {
            "total": 0, "read_total": 0, "reply_total": 0,
        })
        row["total"] += 1
        row["read_total"] += int(item.get("read_count") or 0)
        row["reply_total"] += int(item.get("reply_count") or 0)
    score_by_date = {str(item.get("date")): item.get("score") for item in score_trend}
    daily_trend = [
        {"date": date_key, **values, "diagnose_score": score_by_date.get(date_key)}
        for date_key, values in sorted(daily.items())
    ]
    retrieved_at = datetime.now().astimezone().isoformat()
    return {
        "symbol": code,
        "name": name,
        "days": days,
        "limit": limit,
        "max_pages": max_pages,
        "items": items,
        "item_count": len(items),
        "total_discussion": len(deduped),
        "user_post_count": len(discussion_items),
        "syndicated_info_count": len(syndicated_sample),
        "xueqiu_hot_count": sum(
            1 for item in discussion_items if item.get("source") == "雪球热榜/RSSHub"
        ),
        "returned_item_order": "public_discussion_then_syndicated_information_each_newest_first",
        "sentiment_sample_scope": "bounded_public_discussion_for_model_synthesis",
        "sentiment_score": None,
        "overall_score": None,
        "sentiment_confidence": None,
        "positive_count": None,
        "negative_count": None,
        "neutral_count": None,
        "total_read": total_read,
        "total_reply": total_reply,
        "eastmoney_diagnose_score": diagnose_score,
        "diagnose_score": diagnose_score,
        "diagnose_score_semantics": "东方财富千股千评综合评分，不是社交情绪分数",
        "score_trend": score_trend,
        "daily_trend": daily_trend,
        "analysis": {
            "semantic_status": "model_required",
            "classification_method": None,
            "limitations": [
                "股吧为有限页公开样本，不代表全部投资者",
                "大多数股吧列表项只有标题，必须结合上下文由模型判断",
                "阅读和回复数仅表示抓取时点可见热度",
            ],
        },
        "coverage_start": earliest,
        "coverage_end": latest,
        "coverage_complete": coverage_complete,
        "source": " + ".join([
            *(["东方财富股吧/Scrapling"] if guba_available else []),
            *(["AKShare/东方财富公司新闻摘要"] if fallback_used else []),
            "RSSHub:/xueqiu/hots",
            "东方财富千股千评",
        ]),
        "sources": [
            *(["东方财富股吧/Scrapling"] if guba_available else []),
            *(["AKShare/东方财富公司新闻摘要"] if fallback_used else []),
            "RSSHub:/xueqiu/hots",
            "东方财富千股千评",
        ],
        "source_scope": "bounded_public_discussion_sample",
        "success": acquisition_succeeded,
        "partial": bool(errors) and acquisition_succeeded,
        "data_time": latest,
        "retrieved_at": retrieved_at,
        "is_stale": latest_time < cutoff if latest_time else None,
        "freshness_unknown": not bool(known_times),
        "fallback_used": fallback_used,
        "fallback_recommended": not acquisition_succeeded,
        "errors": errors[:10],
        "warnings": list(dict.fromkeys(warnings))[:10],
        "_cached": fallback_cached if fallback_used else cached,
        "_fetched_at": retrieved_at,
    }


TOOL = ToolSpec(
    name="get_social_sentiment",
    description=(
        "采样单只 A 股的东方财富股吧公开帖子，并补充 47 条 Infos 路由中的雪球热榜精确提及；"
        "返回公开讨论与资讯来源拆分、阅读回复热度、实际时间覆盖和东方财富千股千评趋势。"
        "工具不使用词典判断情绪；语义极性由最终模型结合上下文分析。"
    ),
    parameters=object_schema({
        "symbol": {"type": "string", "description": "A 股代码或名称"},
        "days": {"type": "integer", "minimum": 1, "maximum": 180, "default": 30},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
        "max_pages": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3, "description": "最多采样股吧列表页数"},
    }, ["symbol"]),
    executor=get_social_sentiment,
    category="sentiment",
)
