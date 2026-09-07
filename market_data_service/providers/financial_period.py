"""One-provider financial report-period snapshot reader.

This is deliberately independent from the legacy quantitative screener.  It
reads one Eastmoney report-period table, handles pagination/revisions inside
that one source, and returns raw normalized rows for source-level Agent tools.
"""

from __future__ import annotations

from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import threading
import time
from typing import Any

import requests


_EASTMONEY_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_PAGE_WORKERS = 8
_HTTP_LOCAL = threading.local()


def _http_session() -> requests.Session:
    session = getattr(_HTTP_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0"})
        _HTTP_LOCAL.session = session
    return session


def _reset_http_session() -> None:
    session = getattr(_HTTP_LOCAL, "session", None)
    if session is not None:
        session.close()
    _HTTP_LOCAL.session = None


def _fetch_page(period: str, page: int) -> tuple[list[dict[str, Any]], int]:
    params = {
        "reportName": "RPT_F10_FINANCE_MAINFINADATA",
        "columns": (
            "SECURITY_CODE,REPORT_DATE,UPDATE_DATE,TOTALOPERATEREVE,"
            "PARENTNETPROFIT,KCFJCXSYJLR,ZCFZL"
        ),
        "filter": f"(REPORT_DATE='{period}')",
        "pageNumber": page,
        "pageSize": 500,
        # Revised filings can coexist. Stable pagination preserves the exact
        # source set before the per-field latest-revision merge below.
        "sortColumns": "SECURITY_CODE,UPDATE_DATE",
        "sortTypes": "1,-1",
        "source": "WEB",
        "client": "WEB",
    }
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            response = _http_session().get(
                _EASTMONEY_URL,
                params=params,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://data.eastmoney.com/",
                },
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("success") is not True:
                raise RuntimeError(
                    str(payload.get("message") or "东财财务数据返回失败")
                )
            result = payload.get("result") or {}
            return list(result.get("data") or []), int(result.get("pages") or 0)
        except Exception as exc:
            last_error = exc
            _reset_http_session()
            if attempt < 3:
                time.sleep(0.4 * attempt)
    assert last_error is not None
    raise last_error


def fetch_financial_period_snapshot(period: str) -> dict[str, dict[str, Any]]:
    """Read and normalize one Eastmoney financial report period.

    Parallel page requests are pagination for the same source query, not a
    provider fallback or a multi-step investment analysis.
    """
    if not re.fullmatch(r"\d{4}-(?:03-31|06-30|09-30|12-31)", period):
        raise ValueError("period must be a supported financial report date")
    first, pages = _fetch_page(period, 1)
    rows = list(first)
    if pages > 1:
        with ThreadPoolExecutor(max_workers=_PAGE_WORKERS) as pool:
            futures = [
                pool.submit(copy_context().run, _fetch_page, period, page)
                for page in range(2, pages + 1)
            ]
            for future in as_completed(futures):
                page_rows, _ = future.result()
                rows.extend(page_rows)

    by_code: dict[str, dict[str, Any]] = {}
    for row in sorted(
        rows,
        key=lambda item: (
            str(item.get("SECURITY_CODE") or ""),
            str(item.get("UPDATE_DATE") or ""),
        ),
    ):
        code = str(row.get("SECURITY_CODE") or "").zfill(6)
        if not re.fullmatch(r"\d{6}", code):
            continue
        merged = by_code.setdefault(code, {"SECURITY_CODE": code})
        for field in (
            "REPORT_DATE",
            "UPDATE_DATE",
            "TOTALOPERATEREVE",
            "PARENTNETPROFIT",
            "KCFJCXSYJLR",
            "ZCFZL",
        ):
            if row.get(field) is not None:
                merged[field] = row[field]
    return by_code
