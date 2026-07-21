"""Structured evidence acquisition for forward catalyst research.

This module deliberately separates *retrieval* from *judgment*:

* formal periodic-report bodies are read page by page instead of treating an
  announcement title as the document;
* passages are selected only because they contain an explicit calendar window
  inside the requested horizon — no catalyst keyword list decides semantics;
* scheduled reporting dates are returned as verification windows, not silently
  promoted to positive company catalysts.

The evaluator/LLM remains responsible for deciding what the disclosed passage
means for orders, revenue, profit or expectations.
"""
from __future__ import annotations

import logging
import re
import time
from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any

import requests

logger = logging.getLogger(__name__)

_CONTENT_URL = "https://np-cnotice-stock.eastmoney.com/api/content/ann"
_SCHEDULE_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_ART_CODE_RE = re.compile(r"(AN\d{14,24})", re.I)
_SPACE_RE = re.compile(r"\s+")
_EXPLICIT_WINDOW_RE = re.compile(
    r"(?P<year>20\d{2})\s*年\s*"
    r"(?:(?P<month>1[0-2]|0?[1-9])\s*月(?:\s*(?P<day>[0-3]?\d)\s*日)?|"
    r"第?\s*(?P<quarter>[一二三四1-4])\s*季度|"
    r"(?P<half>上半年|下半年))",
    re.I,
)


def _clean_text(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip()


def _request_json(
    url: str,
    *,
    params: dict[str, Any],
    timeout: float = 15.0,
    attempts: int = 3,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            response = requests.get(
                url,
                params=params,
                timeout=timeout,
                headers={"User-Agent": "Mozilla/5.0 catalyst-evidence-reader/1.0"},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("upstream response is not a JSON object")
            return payload
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.5 * (attempt + 1))
    assert last_error is not None
    raise last_error


def _art_code(item: dict[str, Any]) -> str | None:
    for value in (item.get("art_code"), item.get("url"), item.get("link")):
        match = _ART_CODE_RE.search(str(value or ""))
        if match:
            return match.group(1).upper()
    return None


def _is_full_periodic_report(title: str) -> bool:
    compact = re.sub(r"\s+", "", title)
    if any(marker in compact for marker in ("摘要", "审计报告", "审核报告", "更正公告", "取消审核")):
        return False
    return bool(re.search(r"(?:年度报告|半年度报告|季度报告)$", compact))


def _periodic_priority(title: str) -> int:
    compact = re.sub(r"\s+", "", title)
    if "年度报告" in compact and "半年度报告" not in compact:
        return 4
    if "半年度报告" in compact:
        return 3
    if "第三季度报告" in compact:
        return 2
    if "第一季度报告" in compact:
        return 1
    return 0


def select_formal_documents(items: list[dict[str, Any]], *, limit: int = 3) -> list[dict[str, Any]]:
    """Select complete periodic reports independent of catalyst vocabulary.

    The latest annual report is always preferred because it contains the
    board-approved operating plan.  Later interim/quarterly reports are also
    included when available so subsequent changes are not missed.
    """
    candidates = [
        item for item in items
        if isinstance(item, dict)
        and _art_code(item)
        and _is_full_periodic_report(str(item.get("title") or ""))
    ]
    candidates.sort(
        key=lambda item: (
            str(item.get("publish_date") or item.get("date") or ""),
            _periodic_priority(str(item.get("title") or "")),
        ),
        reverse=True,
    )

    selected: list[dict[str, Any]] = []
    latest_annual = next((
        item for item in candidates
        if _periodic_priority(str(item.get("title") or "")) == 4
    ), None)
    if latest_annual is not None:
        selected.append(latest_annual)
    annual_date = str(latest_annual.get("publish_date") or "") if latest_annual else ""
    for item in candidates:
        if item in selected:
            continue
        # Once a complete annual report is available, older quarterlies cannot
        # add a newer forward plan.  Keep only reports published afterwards.
        if annual_date and str(item.get("publish_date") or "") < annual_date:
            continue
        # The first-quarter report contains compact historical statements and
        # is commonly filed within days of the annual report.  It does not
        # replace the annual operating plan, so do not reread it as a second
        # long-document dependency.  Later half-year/Q3 reports remain eligible
        # because they can update the plan materially.
        if annual_date and _periodic_priority(str(item.get("title") or "")) == 1:
            continue
        selected.append(item)
        if len(selected) >= max(1, limit):
            break
    return selected[:max(1, limit)]


def _fetch_content_page(art_code: str, page_index: int) -> dict[str, Any]:
    payload = _request_json(
        _CONTENT_URL,
        params={
            "art_code": art_code,
            "client_source": "web",
            "page_index": page_index,
        },
    )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError(f"announcement content unavailable for {art_code} page {page_index}")
    return data


def fetch_formal_document(item: dict[str, Any], *, max_pages: int = 120) -> dict[str, Any]:
    """Fetch a complete announcement body from Eastmoney's document API."""
    art_code = _art_code(item)
    if not art_code:
        raise ValueError("announcement URL does not contain an art code")

    first = _fetch_content_page(art_code, 1)
    page_count = max(1, min(int(first.get("page_size") or 1), max_pages))
    pages: dict[int, str] = {1: str(first.get("notice_content") or "")}
    if page_count > 1:
        with ThreadPoolExecutor(max_workers=min(8, page_count - 1)) as pool:
            futures = {
                pool.submit(_fetch_content_page, art_code, index): index
                for index in range(2, page_count + 1)
            }
            for future in as_completed(futures):
                index = futures[future]
                pages[index] = str(future.result().get("notice_content") or "")

    return {
        "art_code": art_code,
        "title": _clean_text(first.get("notice_title") or item.get("title")),
        "publish_date": str(first.get("notice_date") or item.get("publish_date") or "")[:10] or None,
        "content": "\n".join(pages[index] for index in sorted(pages)),
        "page_count": page_count,
        "document_url": str(first.get("attach_url_web") or first.get("attach_url") or item.get("url") or ""),
        "metadata_url": str(item.get("url") or ""),
    }


def _window_bounds(match: re.Match[str]) -> tuple[date, date]:
    year = int(match.group("year"))
    if match.group("month"):
        month = int(match.group("month"))
        day = int(match.group("day") or 1)
        start = date(year, month, max(1, min(day, monthrange(year, month)[1])))
        if month == 12:
            end = date(year, 12, 31)
        else:
            end = date(year, month + 1, 1) - timedelta(days=1)
        return start, end
    if match.group("quarter"):
        quarter_text = match.group("quarter")
        quarter = {"一": 1, "二": 2, "三": 3, "四": 4}.get(quarter_text)
        if quarter is None:
            quarter = int(quarter_text)
        start_month = (quarter - 1) * 3 + 1
        start = date(year, start_month, 1)
        end = date(year, start_month + 3, 1) - timedelta(days=1) if start_month < 10 else date(year, 12, 31)
        return start, end
    if match.group("half") == "上半年":
        return date(year, 1, 1), date(year, 6, 30)
    return date(year, 7, 1), date(year, 12, 31)


def _context_around(text: str, start: int, end: int, *, radius: int = 620) -> str:
    left_floor = max(0, start - radius)
    right_ceiling = min(len(text), end + radius)
    left = max(text.rfind(mark, left_floor, start) for mark in ("。", "；", "\n"))
    if left < left_floor:
        left = left_floor
    else:
        left += 1
    stops = [position for position in (
        text.find("。", end, right_ceiling),
        text.find("；", end, right_ceiling),
        text.find("\n", end, right_ceiling),
    ) if position >= 0]
    right = min(stops) + 1 if stops else right_ceiling
    return _clean_text(text[left:right])[:1600]


def extract_forward_window_passages(
    content: str,
    *,
    as_of: date | None = None,
    horizon_days: int = 366,
    limit: int = 18,
) -> list[dict[str, Any]]:
    """Return every bounded future-window passage, without semantic filtering."""
    today = as_of or datetime.now().astimezone().date()
    horizon_end = today + timedelta(days=horizon_days)
    passages: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in _EXPLICIT_WINDOW_RE.finditer(content):
        window_start, window_end = _window_bounds(match)
        if window_end < today or window_start > horizon_end:
            continue
        excerpt = _context_around(content, match.start(), match.end())
        dedupe_key = re.sub(r"\W+", "", excerpt).lower()[:260]
        if not excerpt or dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        passages.append({
            "time_window": _clean_text(match.group(0)),
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "excerpt": excerpt,
        })
        if len(passages) >= limit:
            break
    return passages


def _normalized_title(value: str) -> str:
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", value).lower()


def _official_document_url(symbol: str, publish_date: str | None, title: str) -> str | None:
    if not publish_date:
        return None
    try:
        from src.tools.get_announcements import _fetch_exchange_rss

        rows, _, _ = _fetch_exchange_rss(
            symbol,
            publish_date,
            publish_date,
            limit=50,
        )
        target = _normalized_title(title)
        best_url: str | None = None
        best_score = 0.0
        for row in rows:
            candidate = _normalized_title(str(row.get("公告标题") or ""))
            score = SequenceMatcher(None, target, candidate).ratio()
            if target.endswith(candidate) or candidate.endswith(target):
                score = max(score, 0.95)
            if score > best_score:
                best_score = score
                best_url = str(row.get("网址") or "") or None
        return best_url if best_score >= 0.62 else None
    except Exception as exc:
        logger.info("official announcement URL resolution failed: %s", exc)
        return None


def get_formal_forward_evidence(
    symbol: str,
    announcements: list[dict[str, Any]],
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Read selected formal reports and expose explicit future passages."""
    selected = select_formal_documents(announcements)
    passages: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    errors: list[str] = []
    for item in selected:
        try:
            from src.tools._akshare import cached_call

            art_code = _art_code(item)
            document, _ = cached_call(
                f"catalyst-formal-document:{art_code}",
                lambda: fetch_formal_document(item),
                ttl_seconds=7 * 24 * 60 * 60,
                attempts=3,
            )
            official_url = _official_document_url(
                symbol,
                document.get("publish_date"),
                str(document.get("title") or ""),
            )
            source_url = official_url or document.get("document_url") or document.get("metadata_url")
            extracted = extract_forward_window_passages(document.get("content") or "", as_of=as_of)
            documents.append({
                "art_code": document.get("art_code"),
                "title": document.get("title"),
                "publish_date": document.get("publish_date"),
                "page_count": document.get("page_count"),
                "url": source_url,
                "official_url": official_url,
                "document_url": document.get("document_url"),
                "passage_count": len(extracted),
            })
            for passage in extracted:
                passages.append({
                    **passage,
                    "title": document.get("title"),
                    "date": document.get("publish_date"),
                    "source": "交易所正式公告正文" if official_url else "公司定期报告正文",
                    "url": source_url,
                    "art_code": document.get("art_code"),
                })
        except Exception as exc:
            errors.append(f"{item.get('title') or '定期报告'}: {type(exc).__name__}: {str(exc)[:180]}")
    return {
        "items": passages,
        "documents": documents,
        "selected_document_count": len(selected),
        "success": bool(documents) or not selected,
        "errors": errors,
    }


def get_report_schedule(symbol: str, *, as_of: date | None = None) -> dict[str, Any]:
    """Get future scheduled financial-disclosure dates for one A-share."""
    code_match = re.search(r"\d{6}", str(symbol or ""))
    if not code_match:
        raise ValueError(f"无法识别 A 股代码: {symbol}")
    code = code_match.group(0)
    today = as_of or datetime.now().astimezone().date()
    payload = _request_json(
        _SCHEDULE_URL,
        params={
            "reportName": "RPT_PUBLIC_BS_APPOIN",
            "columns": "ALL",
            "filter": f'(SECURITY_CODE="{code}")',
            "pageNumber": 1,
            "pageSize": 50,
            "sortColumns": "APPOINT_PUBLISH_DATE",
            "sortTypes": 1,
        },
    )
    result = payload.get("result")
    rows = result.get("data") if isinstance(result, dict) else []
    items: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        scheduled_text = str(row.get("APPOINT_PUBLISH_DATE") or row.get("FIRST_APPOINT_DATE") or "")[:10]
        try:
            scheduled_date = date.fromisoformat(scheduled_text)
        except ValueError:
            continue
        if scheduled_date < today or scheduled_date > today + timedelta(days=366):
            continue
        report_date = str(row.get("REPORT_DATE") or "")[:10]
        period = report_date[:7].replace("-", "")
        items.append({
            "event": str(row.get("REPORT_TYPE_NAME") or "定期报告") + "预约披露",
            "time_window": scheduled_text,
            "scheduled_date": scheduled_text,
            "report_period": report_date or None,
            "date": str(row.get("EITIME") or "")[:10] or None,
            "source": "东方财富财报预约披露数据",
            "url": f"https://data.eastmoney.com/bbsj/{period}/yysj.html" if period else "https://data.eastmoney.com/bbsj/yysj.html",
            "schedule_status": "已披露" if str(row.get("IS_PUBLISH")) == "1" else "预约",
        })
    items.sort(key=lambda item: str(item.get("scheduled_date") or ""))
    return {
        "items": items,
        "success": payload.get("success") is True,
        "errors": [] if payload.get("success") is True else [str(payload.get("message") or "预约披露数据获取失败")],
    }


__all__ = [
    "extract_forward_window_passages",
    "fetch_formal_document",
    "get_formal_forward_evidence",
    "get_report_schedule",
    "select_formal_documents",
]
