"""Function group 1 extracted from src/services/catalyst_evidence.py."""

from __future__ import annotations

from src.services.catalyst_evidence import (
    BytesIO,
    logging,
    re,
    time,
    monthrange,
    ThreadPoolExecutor,
    as_completed,
    date,
    datetime,
    timedelta,
    lru_cache,
    Any,
    requests,
    logger,
    _CONTENT_URL,
    _SCHEDULE_URL,
    _CNINFO_QUERY_URL,
    _CNINFO_STOCK_URL,
    _ART_CODE_RE,
    _SPACE_RE,
    _EXPLICIT_WINDOW_RE,
    __all__,
 )

__all__ = ['_clean_text', '_request_json', '_art_code', '_is_full_periodic_report', '_periodic_priority', 'select_formal_documents', '_fetch_content_page', '_extract_pdf_text', 'fetch_formal_document', '_window_bounds', '_context_around', 'extract_business_passages', 'extract_forward_window_passages', '_normalized_title', '_cninfo_org_map', '_cninfo_formal_pdf_url', '_official_document_url']

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
        item
        for item in items
        if isinstance(item, dict) and _art_code(item) and _is_full_periodic_report(str(item.get("title") or ""))
    ]
    candidates.sort(
        key=lambda item: (
            str(item.get("publish_date") or item.get("date") or ""),
            _periodic_priority(str(item.get("title") or "")),
        ),
        reverse=True,
    )

    selected: list[dict[str, Any]] = []
    latest_annual = next((item for item in candidates if _periodic_priority(str(item.get("title") or "")) == 4), None)
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
    return selected[: max(1, limit)]

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

def _extract_pdf_text(url: str, *, max_pages: int = 160) -> tuple[str, int]:
    """Extract a bounded formal-report PDF when the metadata API has no body."""
    response = requests.get(
        url,
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0 formal-report-reader/1.0"},
    )
    response.raise_for_status()
    if len(response.content) > 60 * 1024 * 1024:
        raise ValueError("formal report PDF exceeds 60MB safety limit")

    import pdfplumber

    pages: list[str] = []
    with pdfplumber.open(BytesIO(response.content)) as pdf:
        page_count = min(len(pdf.pages), max(1, max_pages))
        for page in pdf.pages[:page_count]:
            pages.append(str(page.extract_text(x_tolerance=2, y_tolerance=3) or ""))
    return "\n".join(pages), page_count

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
            futures = {pool.submit(_fetch_content_page, art_code, index): index for index in range(2, page_count + 1)}
            for future in as_completed(futures):
                index = futures[future]
                pages[index] = str(future.result().get("notice_content") or "")

    document_url = str(
        item.get("preferred_document_url")
        or first.get("attach_url_web")
        or first.get("attach_url")
        or item.get("url")
        or ""
    )
    content = "\n".join(pages[index] for index in sorted(pages))
    if len(_clean_text(content)) < 500 and document_url.lower().split("?", 1)[0].endswith(".pdf"):
        pdf_content, pdf_page_count = _extract_pdf_text(
            document_url,
            max_pages=max_pages,
        )
        if len(_clean_text(pdf_content)) > len(_clean_text(content)):
            content = pdf_content
            page_count = pdf_page_count

    return {
        "art_code": art_code,
        "title": _clean_text(first.get("notice_title") or item.get("title")),
        "publish_date": str(first.get("notice_date") or item.get("publish_date") or "")[:10] or None,
        "content": content,
        "page_count": page_count,
        "document_url": document_url,
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
    stops = [
        position
        for position in (
            text.find("。", end, right_ceiling),
            text.find("；", end, right_ceiling),
            text.find("\n", end, right_ceiling),
        )
        if position >= 0
    ]
    right = min(stops) + 1 if stops else right_ceiling
    return _clean_text(text[left:right])[:1600]

def extract_business_passages(
    content: str,
    *,
    thesis: str,
    thesis_context: dict[str, Any] | None,
    research_scope: dict[str, Any] | None = None,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Return broad document spans without lexical relevance matching.

    The thesis parameters remain for API compatibility, but the program never
    scores text against them.  A bounded set of evenly distributed source spans
    is sent to the analysis model, which performs the semantic judgment.
    """
    del thesis, thesis_context, research_scope
    text = _clean_text(content)
    if not text:
        return []
    span_size = 1_800
    span_count = max(1, min(int(limit), 12))
    if len(text) <= span_size:
        starts = [0]
    else:
        last_start = max(0, len(text) - span_size)
        starts = sorted({round(last_start * index / max(1, span_count - 1)) for index in range(span_count)})
    return [
        {
            "excerpt": text[start : start + span_size],
            "document_offset_start": start,
            "document_offset_end": min(len(text), start + span_size),
            "selection_method": "uniform_document_coverage",
        }
        for start in starts
    ]

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
        passages.append(
            {
                "time_window": _clean_text(match.group(0)),
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
                "excerpt": excerpt,
            }
        )
        if len(passages) >= limit:
            break
    return passages

def _normalized_title(value: str) -> str:
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", value).lower()

@lru_cache(maxsize=1)
def _cninfo_org_map() -> dict[str, str]:
    payload = _request_json(_CNINFO_STOCK_URL, params={}, attempts=3)
    rows = payload.get("stockList") or []
    return {
        str(item.get("code") or "").strip(): str(item.get("orgId") or "").strip()
        for item in rows
        if isinstance(item, dict) and item.get("code") and item.get("orgId")
    }

def _cninfo_formal_pdf_url(
    symbol: str,
    publish_date: str | None,
    title: str,
) -> str | None:
    """Resolve the primary CNInfo PDF for a selected formal report."""
    code_match = re.search(r"\d{6}", str(symbol or ""))
    if not code_match or not publish_date:
        return None
    code = code_match.group(0)
    org_id = _cninfo_org_map().get(code)
    if not org_id:
        return None
    try:
        observed = date.fromisoformat(str(publish_date)[:10])
    except ValueError:
        return None
    payload = {
        "pageNum": "1",
        "pageSize": "50",
        "column": "szse",
        "tabName": "fulltext",
        "plate": "",
        "stock": f"{code},{org_id}",
        "searchkey": "",
        "secid": "",
        "category": "",
        "trade": "",
        "seDate": f"{observed - timedelta(days=2)}~{observed + timedelta(days=2)}",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    response = requests.post(
        _CNINFO_QUERY_URL,
        data=payload,
        headers={
            "User-Agent": "Mozilla/5.0 formal-report-reader/1.0",
            "Referer": "https://www.cninfo.com.cn/",
        },
        timeout=20,
    )
    response.raise_for_status()
    rows = response.json().get("announcements") or []
    target = _normalized_title(title)
    for row in rows:
        if not isinstance(row, dict):
            continue
        candidate_title = str(row.get("announcementTitle") or "")
        if not _is_full_periodic_report(candidate_title):
            continue
        adjunct = str(row.get("adjunctUrl") or "").strip()
        if not adjunct:
            continue
        candidate = _normalized_title(candidate_title)
        if candidate == target:
            return "https://static.cninfo.com.cn/" + adjunct.lstrip("/")
    return None

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
        for row in rows:
            candidate = _normalized_title(str(row.get("公告标题") or ""))
            if candidate == target:
                return str(row.get("网址") or "") or None
        return None
    except Exception as exc:
        logger.info("official announcement URL resolution failed: %s", exc)
        return None
