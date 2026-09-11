"""``get_shareholder_structure`` — current, dated shareholder evidence.

The Eastmoney F10 shareholder page exposes the current single-stock snapshot
in one response.  Use that instead of probing many quarter dates or treating a
full-market historical control-change table as the current controller.
"""

from __future__ import annotations
import math
import re
from datetime import date, datetime
from typing import Any
import httpx
from market_data_service.providers.common import (
    bare_local_symbol,
    cached_call,
    exchange_prefix,
    frame_records,
)

_BASE_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch"
_SOURCE_URL = f"{_BASE_URL}/Index"
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
_ORG_TYPE = {
    "00": "合计",
    "01": "基金",
    "02": "QFII",
    "03": "社保",
    "04": "券商",
    "05": "保险",
    "06": "信托",
    "07": "其他机构",
}
DESCRIPTION = "获取有明确报告期的股东结构：股东户数及集中度、前十大股东、机构持仓、实际控制人和重要股东增减持。机构持仓采用已完成披露期，返回占总股本与占流通股两种口径；不会用股东名称关键词猜测机构比例。"


def _number(value: Any) -> float | None:
    if value in (None, "", "-", "--", "未披露"):
        return None
    try:
        number = float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _date_text(value: Any) -> str | None:
    text = str(value or "")[:10]
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return None


def _today() -> date:
    return date.today()


def _request(path: str, code: str, **params: str) -> dict[str, Any]:
    response = httpx.get(
        f"{_BASE_URL}/{path}",
        params={"code": exchange_prefix(code, upper=True), **params},
        headers={"User-Agent": _UA, "Referer": f"{_SOURCE_URL}?type=web"},
        timeout=httpx.Timeout(12.0, connect=3.0),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("status") == -1:
        raise RuntimeError(str(payload.get("message") or "东方财富股东研究返回异常"))
    return payload


def _fetch_f10_profile(code: str) -> dict[str, Any]:
    payload = _request("PageAjax", code)
    returned_codes = {
        str(row.get("SECURITY_CODE") or "")
        for key in ("gdrs", "sdgd", "sjkzr")
        for row in payload.get(key) or []
        if isinstance(row, dict)
    }
    if returned_codes and code not in returned_codes:
        raise RuntimeError("东方财富股东研究返回了其他证券")
    return payload


def _fetch_institution_report(code: str, report_date: str) -> dict[str, Any]:
    return _request("PageJGCC", code, date=report_date)


def _fetch_holder_change_frame(code: str):
    import akshare as ak

    return ak.stock_shareholder_change_ths(symbol=code)


def _expected_latest_report_date(as_of: date) -> date:
    if as_of >= date(as_of.year, 11, 1):
        return date(as_of.year, 9, 30)
    if as_of >= date(as_of.year, 9, 1):
        return date(as_of.year, 6, 30)
    if as_of >= date(as_of.year, 5, 1):
        return date(as_of.year, 3, 31)
    return date(as_of.year - 1, 9, 30)


def _normalize_holder_count(payload: dict[str, Any]) -> dict[str, Any]:
    rows = sorted(
        [row for row in payload.get("gdrs") or [] if isinstance(row, dict)],
        key=lambda row: _date_text(row.get("END_DATE")) or "",
        reverse=True,
    )
    if not rows:
        return {}
    latest = rows[0]
    previous = rows[1] if len(rows) > 1 else {}
    current_count = _integer(latest.get("HOLDER_TOTAL_NUM"))
    previous_count = _integer(previous.get("HOLDER_TOTAL_NUM"))
    return {
        "report_date": _date_text(latest.get("END_DATE")),
        "holder_count": current_count,
        "previous_report_date": _date_text(previous.get("END_DATE")),
        "previous_holder_count": previous_count,
        "change_count": current_count - previous_count
        if current_count is not None and previous_count is not None
        else None,
        "change_pct": _number(latest.get("TOTAL_NUM_RATIO")),
        "average_holding_shares": _integer(latest.get("AVG_FREE_SHARES")),
        "average_holding_market_value_yuan": _number(latest.get("AVG_HOLD_AMT")),
        "provider_concentration_label": str(latest.get("HOLD_FOCUS") or "").strip()
        or None,
        "top10_total_share_ratio_pct": _number(latest.get("HOLD_RATIO_TOTAL")),
        "top10_circulating_share_ratio_pct": _number(
            latest.get("FREEHOLD_RATIO_TOTAL")
        ),
    }


def _change_direction(raw: Any) -> tuple[str | None, float | None]:
    text = str(raw or "").strip()
    if not text or text in {"不变", "--", "-"}:
        return ("不变" if text == "不变" else None, None)
    number = _number(text)
    if number is None:
        return (None, None)
    return ("增持" if number > 0 else "减持" if number < 0 else "不变", number)


def _normalize_top_holders(
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], str | None]:
    free_types = {
        str(row.get("HOLDER_NAME") or ""): str(row.get("HOLDER_TYPE") or "").strip()
        or None
        for row in payload.get("sdltgd") or []
        if isinstance(row, dict)
    }
    rows = [row for row in payload.get("sdgd") or [] if isinstance(row, dict)]
    rows.sort(key=lambda row: _integer(row.get("HOLDER_RANK")) or 999)
    report_date = max((_date_text(row.get("END_DATE")) for row in rows), default=None)
    holders = []
    for row in rows[:10]:
        name = str(row.get("HOLDER_NAME") or "").strip()
        direction, change_shares = _change_direction(row.get("HOLD_NUM_CHANGE"))
        holders.append(
            {
                "rank": _integer(row.get("HOLDER_RANK")),
                "holder_name": name or None,
                "holder_type": free_types.get(name),
                "share_type": str(row.get("SHARES_TYPE") or "").strip() or None,
                "holding_shares": _integer(row.get("HOLD_NUM")),
                "holding_ratio_pct": _number(row.get("HOLD_NUM_RATIO")),
                "change_direction": direction,
                "change_shares": _integer(change_shares),
                "change_ratio_pct": _number(row.get("CHANGE_RATIO")),
            }
        )
    return (holders, report_date)


def _normalize_controller(payload: dict[str, Any]) -> dict[str, Any]:
    row = next((row for row in payload.get("sjkzr") or [] if isinstance(row, dict)), {})
    name = str(row.get("HOLDER_NAME") or "").strip() or None
    return {
        "available": bool(name),
        "name": name,
        "holding_ratio_pct": _number(row.get("HOLD_RATIO")),
        "report_date": None,
        "date_note": "东方财富当前F10档案未提供实际控制人的披露日期",
    }


def _normalize_institution(
    payload: dict[str, Any], report_date: str | None
) -> dict[str, Any]:
    rows = [row for row in payload.get("jgcc") or [] if isinstance(row, dict)]
    total = next((row for row in rows if str(row.get("ORG_TYPE")) == "00"), {})
    breakdown = []
    for row in rows:
        code = str(row.get("ORG_TYPE") or "")
        if code == "00":
            continue
        breakdown.append(
            {
                "institution_type": _ORG_TYPE.get(code, f"类型{code}"),
                "institution_type_code": code,
                "institution_count": _integer(row.get("TOTAL_ORG_NUM")),
                "holding_shares": _integer(row.get("TOTAL_FREE_SHARES")),
                "percent_of_circulating_shares": _number(row.get("TOTAL_SHARES_RATIO")),
                "percent_of_total_shares": _number(row.get("ALL_SHARES_RATIO")),
            }
        )
    return {
        "available": bool(total),
        "report_date": report_date,
        "institution_count": _integer(total.get("TOTAL_ORG_NUM")),
        "holding_shares": _integer(total.get("TOTAL_FREE_SHARES")),
        "percent_of_circulating_shares": _number(total.get("TOTAL_SHARES_RATIO")),
        "percent_of_total_shares": _number(total.get("ALL_SHARES_RATIO")),
        "breakdown": breakdown,
    }


def _share_amount(value: Any) -> float | None:
    text = str(value or "").replace(",", "").strip()
    match = re.search("([+-]?[0-9]+(?:\\.[0-9]+)?)", text)
    if not match:
        return None
    number = float(match.group(1))
    if "亿" in text:
        number *= 100000000
    elif "万" in text:
        number *= 10000
    return number


def _normalize_holder_changes(frame: Any) -> list[dict[str, Any]]:
    items = []
    for row in frame_records(frame):
        raw_change = str(row.get("变动数量") or "").strip()
        amount = _share_amount(raw_change)
        if "减持" in raw_change:
            direction = "减持"
            signed = -abs(amount) if amount is not None else None
        elif "增持" in raw_change:
            direction = "增持"
            signed = abs(amount) if amount is not None else None
        else:
            direction = None
            signed = amount
        items.append(
            {
                "announcement_date": _date_text(row.get("公告日期")),
                "holder_name": str(row.get("变动股东") or "").strip() or None,
                "change_direction": direction,
                "change_shares": int(abs(amount)) if amount is not None else None,
                "signed_change_shares": int(signed) if signed is not None else None,
                "average_price_yuan": _number(row.get("交易均价")),
                "remaining_shares": _integer(_share_amount(row.get("剩余股份总数"))),
                "change_period": str(row.get("变动期间") or "").strip() or None,
                "transaction_method": str(row.get("变动途径") or "").strip() or None,
            }
        )
    items.sort(key=lambda item: item.get("announcement_date") or "", reverse=True)
    return items[:20]


def _validated_code(symbol: str) -> str:
    code = bare_local_symbol(symbol)
    if not re.fullmatch("\\d{6}", code):
        raise ValueError("symbol 必须能解析为 6 位股票代码")
    return code


def read_shareholder_f10_profile_eastmoney(
    symbol: str, *, use_cache: bool = True
) -> dict[str, Any]:
    """Read the single Eastmoney F10 PageAjax shareholder profile."""
    code = _validated_code(symbol)
    payload, cached = (
        cached_call(
            f"shareholders:f10:v2:{code}",
            lambda: _fetch_f10_profile(code),
            ttl_seconds=6 * 3600,
            attempts=2,
        )
        if use_cache
        else (_fetch_f10_profile(code), False)
    )
    holder_count = _normalize_holder_count(payload)
    top_holders, top_holders_report_date = _normalize_top_holders(payload)
    controller = _normalize_controller(payload)
    holder_report_date = holder_count.get("report_date") or top_holders_report_date
    return {
        "symbol": code,
        "holder_count": holder_count,
        "top_holders_report_date": top_holders_report_date,
        "top_holders": top_holders,
        "top_holder_item_count": len(top_holders),
        "actual_controller": controller,
        "source": "东方财富F10股东研究",
        "source_url": f"{_SOURCE_URL}?type=web&code={exchange_prefix(code, upper=True)}",
        "source_scope": "shareholder_f10_profile",
        "success": True,
        "partial": False,
        "errors": [],
        "warnings": [str(controller["date_note"])]
        if controller.get("available")
        else [],
        "data_time": holder_report_date,
        "holder_report_date": holder_report_date,
        "freshness_unknown": holder_report_date is None,
        "is_stale": holder_report_date
        < _expected_latest_report_date(_today()).isoformat()
        if holder_report_date
        else None,
        "_cached": cached,
        "_fetched_at": datetime.now().astimezone().isoformat(),
    }


def read_institutional_holdings_eastmoney(
    symbol: str, report_date: str, *, use_cache: bool = True
) -> dict[str, Any]:
    """Read one explicit disclosure-period institutional-holdings query."""
    code = _validated_code(symbol)
    try:
        normalized_date = (
            datetime.fromisoformat(str(report_date)[:10]).date().isoformat()
        )
    except ValueError as exc:
        raise ValueError("report_date 必须是 YYYY-MM-DD") from exc
    payload, cached = (
        cached_call(
            f"shareholders:institution:v2:{code}:{normalized_date}",
            lambda: _fetch_institution_report(code, normalized_date),
            ttl_seconds=12 * 3600,
            attempts=2,
        )
        if use_cache
        else (_fetch_institution_report(code, normalized_date), False)
    )
    institution = _normalize_institution(payload, normalized_date)
    return {
        "symbol": code,
        "institution_holding": institution,
        "source": "东方财富F10机构持仓",
        "source_url": f"{_SOURCE_URL}?type=web&code={exchange_prefix(code, upper=True)}",
        "source_scope": "institutional_holdings_for_explicit_report_date",
        "success": True,
        "partial": False,
        "errors": [],
        "warnings": []
        if institution.get("available")
        else ["该披露期未返回机构持仓汇总"],
        "data_time": normalized_date,
        "freshness_unknown": False,
        "is_stale": None,
        "_cached": cached,
        "_fetched_at": datetime.now().astimezone().isoformat(),
    }


def read_major_shareholder_changes_ths(
    symbol: str, *, use_cache: bool = True
) -> dict[str, Any]:
    """Read the one THS important-shareholder-change source."""
    code = _validated_code(symbol)
    frame, cached = (
        cached_call(
            f"shareholders:changes:v2:{code}",
            lambda: _fetch_holder_change_frame(code),
            ttl_seconds=6 * 3600,
            attempts=2,
        )
        if use_cache
        else (_fetch_holder_change_frame(code), False)
    )
    items = _normalize_holder_changes(frame)
    latest = next(
        (
            item.get("announcement_date")
            for item in items
            if item.get("announcement_date")
        ),
        None,
    )
    return {
        "symbol": code,
        "holder_changes": items,
        "holder_change_item_count": len(items),
        "source": "AKShare/同花顺重要股东增减持",
        "source_url": f"https://basic.10jqka.com.cn/new/{code}/event.html",
        "source_scope": "major_shareholder_changes",
        "success": True,
        "partial": False,
        "errors": [],
        "warnings": [] if items else ["该数据源未返回重要股东增减持记录"],
        "data_time": latest,
        "freshness_unknown": latest is None,
        "is_stale": None,
        "_cached": cached,
        "_fetched_at": datetime.now().astimezone().isoformat(),
    }
