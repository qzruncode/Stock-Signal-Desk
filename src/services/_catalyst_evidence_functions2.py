"""Function group 2 extracted from src/services/catalyst_evidence.py."""

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

__all__ = ['get_formal_forward_evidence', 'get_formal_business_evidence', 'get_report_schedule']

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
            cninfo_pdf_url = _cninfo_formal_pdf_url(
                symbol,
                str(item.get("publish_date") or item.get("date") or "")[:10] or None,
                str(item.get("title") or ""),
            )
            document, _ = cached_call(
                f"catalyst-formal-document:v2:{art_code}",
                lambda: fetch_formal_document(
                    {
                        **item,
                        "preferred_document_url": cninfo_pdf_url,
                    }
                ),
                ttl_seconds=7 * 24 * 60 * 60,
                attempts=3,
            )
            official_url = cninfo_pdf_url or _official_document_url(
                symbol, document.get("publish_date"), str(document.get("title") or "")
            )
            source_url = official_url or document.get("document_url") or document.get("metadata_url")
            extracted = extract_forward_window_passages(document.get("content") or "", as_of=as_of)
            documents.append(
                {
                    "art_code": document.get("art_code"),
                    "title": document.get("title"),
                    "publish_date": document.get("publish_date"),
                    "page_count": document.get("page_count"),
                    "url": source_url,
                    "official_url": official_url,
                    "document_url": document.get("document_url"),
                    "passage_count": len(extracted),
                }
            )
            for passage in extracted:
                passages.append(
                    {
                        **passage,
                        "title": document.get("title"),
                        "date": document.get("publish_date"),
                        "source": "交易所正式公告正文" if official_url else "公司定期报告正文",
                        "url": source_url,
                        "art_code": document.get("art_code"),
                    }
                )
        except Exception as exc:
            errors.append(f"{item.get('title') or '定期报告'}: {type(exc).__name__}: {str(exc)[:180]}")
    return {
        "items": passages,
        "documents": documents,
        "selected_document_count": len(selected),
        "success": bool(documents) or not selected,
        "errors": errors,
    }

def get_formal_business_evidence(
    symbol: str,
    announcements: list[dict[str, Any]],
    *,
    thesis: str,
    thesis_context: dict[str, Any] | None = None,
    research_scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read current formal reports and retrieve thesis-relevant business facts."""
    selected = select_formal_documents(announcements, limit=2)
    passages: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    errors: list[str] = []
    for item in selected:
        try:
            from src.tools._akshare import cached_call

            art_code = _art_code(item)
            cninfo_pdf_url = _cninfo_formal_pdf_url(
                symbol,
                str(item.get("publish_date") or item.get("date") or "")[:10] or None,
                str(item.get("title") or ""),
            )
            document, _ = cached_call(
                f"catalyst-formal-document:v2:{art_code}",
                lambda: fetch_formal_document(
                    {
                        **item,
                        "preferred_document_url": cninfo_pdf_url,
                    }
                ),
                ttl_seconds=7 * 24 * 60 * 60,
                attempts=3,
            )
            official_url = cninfo_pdf_url or _official_document_url(
                symbol, document.get("publish_date"), str(document.get("title") or "")
            )
            source_url = official_url or document.get("document_url") or document.get("metadata_url")
            extracted = extract_business_passages(
                document.get("content") or "",
                thesis=thesis,
                thesis_context=thesis_context,
                research_scope=research_scope,
            )
            documents.append(
                {
                    "art_code": document.get("art_code"),
                    "title": document.get("title"),
                    "publish_date": document.get("publish_date"),
                    "page_count": document.get("page_count"),
                    "url": source_url,
                    "official_url": official_url,
                    "passage_count": len(extracted),
                }
            )
            for passage in extracted:
                passages.append(
                    {
                        **passage,
                        "title": document.get("title"),
                        "date": document.get("publish_date"),
                        "source": "交易所正式公告正文" if official_url else "公司定期报告正文",
                        "url": source_url,
                        "art_code": document.get("art_code"),
                    }
                )
        except Exception as exc:
            errors.append(f"{item.get('title') or '定期报告'}: " f"{type(exc).__name__}: {str(exc)[:180]}")
    return {
        "items": passages[:12],
        "documents": documents,
        "selected_document_count": len(selected),
        "success": bool(documents) or not selected,
        "errors": errors,
        "retrieval_only": True,
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
        items.append(
            {
                "event": str(row.get("REPORT_TYPE_NAME") or "定期报告") + "预约披露",
                "time_window": scheduled_text,
                "scheduled_date": scheduled_text,
                "report_period": report_date or None,
                "date": str(row.get("EITIME") or "")[:10] or None,
                "source": "东方财富财报预约披露数据",
                "url": (
                    f"https://data.eastmoney.com/bbsj/{period}/yysj.html"
                    if period
                    else "https://data.eastmoney.com/bbsj/yysj.html"
                ),
                "schedule_status": "已披露" if str(row.get("IS_PUBLISH")) == "1" else "预约",
            }
        )
    items.sort(key=lambda item: str(item.get("scheduled_date") or ""))
    return {
        "items": items,
        "success": payload.get("success") is True,
        "errors": [] if payload.get("success") is True else [str(payload.get("message") or "预约披露数据获取失败")],
    }
