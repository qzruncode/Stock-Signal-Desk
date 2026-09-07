"""Corporate actions and institution-attention evidence."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests

from .common import fetch_frame, section_envelope


def _institutional_research_detail_frame(symbol: str, start: str) -> pd.DataFrame:
    """Use AKShare's RPT_ORG_SURVEY contract with a server-side stock predicate.

    Upstream ``stock_jgdy_detail_em`` only accepts a start date and downloads
    every institution-level row in the market (hundreds of thousands for a
    year).  The underlying Eastmoney report supports SECURITY_CODE filtering,
    so the shared adapter keeps the same source fields while bounding work to
    the requested company.
    """
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    start_text = "-".join((start[:4], start[4:6], start[6:]))
    params = {
        "sortColumns": "NOTICE_DATE,RECEIVE_START_DATE,SECURITY_CODE,NUMBERNEW",
        "sortTypes": "-1,-1,1,-1",
        "pageSize": "500",
        "pageNumber": "1",
        "reportName": "RPT_ORG_SURVEY",
        "columns": (
            "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,NOTICE_DATE,RECEIVE_START_DATE,"
            "RECEIVE_OBJECT,RECEIVE_PLACE,RECEIVE_WAY_EXPLAIN,INVESTIGATORS,RECEPTIONIST,ORG_TYPE"
        ),
        "source": "WEB",
        "client": "WEB",
        "filter": (
            f"(IS_SOURCE=\"1\")(RECEIVE_START_DATE>'{start_text}')"
            f'(SECURITY_CODE="{symbol}")'
        ),
    }
    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()
    payload = response.json()
    result = payload.get("result") or {}
    pages = int(result.get("pages") or 0)
    rows: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        params["pageNumber"] = str(page)
        if page == 1:
            page_result = result
        else:
            page_response = requests.get(url, params=params, timeout=20)
            page_response.raise_for_status()
            page_result = page_response.json().get("result") or {}
        rows.extend(page_result.get("data") or [])
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.rename(
        columns={
            "SECURITY_CODE": "代码",
            "SECURITY_NAME_ABBR": "名称",
            "NOTICE_DATE": "公告日期",
            "RECEIVE_START_DATE": "调研日期",
            "RECEIVE_OBJECT": "调研机构",
            "RECEIVE_PLACE": "接待地点",
            "RECEIVE_WAY_EXPLAIN": "接待方式",
            "INVESTIGATORS": "调研人员",
            "RECEPTIONIST": "接待人员",
            "ORG_TYPE": "机构类型",
        }
    )


def _investor_relations_qa(ak: Any, symbol: str) -> dict[str, Any]:
    if symbol.startswith("6"):
        result = fetch_frame(
            "stock_sns_sseinfo",
            f"company:sse_investor_qa:{symbol}",
            lambda: ak.stock_sns_sseinfo(symbol=symbol),
            ttl_seconds=12 * 3600,
            limit=200,
        )
        result["platform"] = "上证e互动"
        return result
    result = fetch_frame(
        "stock_irm_cninfo",
        f"company:cninfo_investor_qa:{symbol}",
        lambda: ak.stock_irm_cninfo(symbol=symbol),
        ttl_seconds=12 * 3600,
        limit=200,
    )
    result["platform"] = "互动易"
    return result


def _latest_suspension_dataset(ak: Any, symbol: str) -> dict[str, Any]:
    today = datetime.now().astimezone().date()
    errors: list[str] = []
    last_success: dict[str, Any] | None = None
    checked_dates: list[str] = []
    for offset in range(10):
        day = (today - timedelta(days=offset)).strftime("%Y%m%d")
        checked_dates.append(day)
        result = fetch_frame(
            "stock_tfp_em",
            f"market:suspensions:{day}",
            lambda day=day: ak.stock_tfp_em(date=day),
            ttl_seconds=6 * 3600,
            symbol=symbol,
            limit=20,
        )
        if result.get("success"):
            last_success = result
            if int(result.get("source_row_count") or 0) > 0:
                result["as_of_trade_date"] = day
                result["checked_dates"] = checked_dates
                if errors:
                    result["prior_attempt_errors"] = errors
                return result
        if result.get("error"):
            errors.append(f"{day}: {result['error']}")
    if last_success is not None:
        last_success["as_of_trade_date"] = checked_dates[0]
        last_success["checked_dates"] = checked_dates
        if errors:
            last_success["prior_attempt_errors"] = errors
        return last_success
    result["error"] = "；".join(errors)[-1200:] or result.get("error")
    return result


def get_corporate_event_evidence(symbol: str, *, days: int = 730) -> dict[str, Any]:
    import akshare as ak

    code = str(symbol)
    today = datetime.now().astimezone().date()
    start = (today - timedelta(days=max(30, min(int(days), 1460)))).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    institutional_detail = fetch_frame(
        "stock_jgdy_detail_em",
        f"company:institutional_research:{code}:{start}",
        lambda: _institutional_research_detail_frame(code, start),
        ttl_seconds=6 * 3600,
        limit=100,
    )
    institutional_detail["adapter_note"] = (
        "沿用 AKShare stock_jgdy_detail_em 的 RPT_ORG_SURVEY 字段契约，"
        "增加服务端股票代码过滤，避免下载全市场机构明细。"
    )
    datasets = {
        "investor_relations_qa": _investor_relations_qa(ak, code),
        "institutional_research_summary": fetch_frame(
            "stock_jgdy_tj_em",
            f"market:institutional_research_summary:{start}",
            lambda: ak.stock_jgdy_tj_em(date=start),
            ttl_seconds=6 * 3600,
            symbol=code,
            limit=100,
        ),
        "institutional_research": institutional_detail,
        "repurchases": fetch_frame(
            "stock_repurchase_em",
            "market:repurchases:all",
            lambda: ak.stock_repurchase_em(),
            ttl_seconds=6 * 3600,
            symbol=code,
            limit=100,
        ),
        "trading_suspensions": _latest_suspension_dataset(ak, code),
        "major_contracts": fetch_frame(
            "stock_zdhtmx_em",
            f"market:major_contracts:{start}:{end}",
            lambda: ak.stock_zdhtmx_em(start_date=start, end_date=end),
            ttl_seconds=6 * 3600,
            symbol=code,
            limit=100,
        ),
        "private_placements": fetch_frame(
            "stock_qbzf_em",
            "market:private_placements:all",
            lambda: ak.stock_qbzf_em(),
            ttl_seconds=12 * 3600,
            symbol=code,
            limit=100,
        ),
    }
    return section_envelope(
        "corporate_events",
        code,
        datasets,
        notes=[
            "互动问答、回购、合同、定增、调研和停复牌只作为结构化事项证据，不由程序预判利好或利空。"
        ],
    )


__all__ = ["get_corporate_event_evidence"]
