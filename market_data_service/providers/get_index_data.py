"""Business-correct mainland index history plus current-session snapshot."""

from __future__ import annotations
from datetime import datetime
from typing import Any
from market_data_service.providers.market_index_catalog import A_SHARE_INDEX_MAP
from market_data_service.providers.common import cached_call
from market_data_service.providers._macro_common import (
    latest_date,
    number,
)

INDEX_MAP = A_SHARE_INDEX_MAP


def _daily_frame(index_code: str):
    import akshare as ak

    return ak.stock_zh_index_daily(symbol=INDEX_MAP[index_code][1])


def _spot_frame():
    import akshare as ak

    return ak.stock_zh_index_spot_sina()


def _daily_records(frame: Any, days: int) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    source_rows = frame.tail(days + 1).reset_index(drop=True)
    rows: list[dict[str, Any]] = []
    for index, row in source_rows.iterrows():
        close = number(row.get("close"))
        previous = number(source_rows.iloc[index - 1].get("close")) if index else None
        rows.append(
            {
                "date": str(row.get("date") or "")[:10],
                "open": number(row.get("open")),
                "high": number(row.get("high")),
                "low": number(row.get("low")),
                "close": close,
                "volume": number(row.get("volume")),
                "amount": number(row.get("amount")),
                "change_amount": round(close - previous, 4)
                if close is not None and previous is not None
                else None,
                "pct_chg": round((close / previous - 1) * 100, 4)
                if close is not None and previous
                else None,
                "record_type": "daily_close",
            }
        )
    return rows[-days:]


def _expected_session_date(now: datetime) -> str | None:
    try:
        from market_data_service.calendar import _fetch_trade_dates, expected_trade_day

        return expected_trade_day(now, _fetch_trade_dates()).isoformat()
    except Exception:
        return now.date().isoformat() if now.weekday() < 5 and now.hour >= 9 else None


def _spot_record(
    frame: Any, index_code: str, session_date: str | None
) -> dict[str, Any] | None:
    if frame is None or frame.empty or (not session_date):
        return None
    code = INDEX_MAP[index_code][1]
    matched = frame[frame["代码"] == code] if "代码" in frame.columns else None
    if matched is None or matched.empty:
        return None
    row = matched.iloc[0]
    close = number(row.get("最新价"))
    open_ = number(row.get("今开"))
    high = number(row.get("最高"))
    low = number(row.get("最低"))
    if close is None or not any((value for value in (open_, high, low))):
        return None
    return {
        "date": session_date,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "previous_close": number(row.get("昨收")),
        "volume": number(row.get("成交量")),
        "amount": number(row.get("成交额")),
        "change_amount": number(row.get("涨跌额")),
        "pct_chg": number(row.get("涨跌幅")),
        "record_type": "realtime_snapshot",
    }


def _validated_index_code(index_code: str) -> str:
    normalized = str(index_code or "").strip()
    if normalized not in INDEX_MAP:
        raise ValueError(f"不支持的指数代码: {normalized}")
    return normalized


def read_index_daily_history_sina(
    index_code: str = "000001", days: int = 20, *, use_cache: bool = True
) -> dict[str, Any]:
    """Read the daily-history endpoint only; no live quote is merged into it."""
    code = _validated_index_code(index_code)
    limit = int(days)
    if not 5 <= limit <= 250:
        raise ValueError("days 必须在 5 到 250 之间")
    frame, cached = (
        cached_call(
            f"index-daily:atomic:v1:{code}", lambda: _daily_frame(code), ttl_seconds=900
        )
        if use_cache
        else (_daily_frame(code), False)
    )
    history = _daily_records(frame, limit)
    data_date = latest_date(history, "date")
    now = datetime.now().astimezone()
    expected = _expected_session_date(now)
    return {
        "index_code": code,
        "index_name": INDEX_MAP[code][0],
        "days": limit,
        "items": history,
        "item_count": len(history),
        "units": {
            "price": "index_point",
            "volume": "source_reported",
            "amount": "CNY",
            "pct_chg": "%",
        },
        "source": "新浪指数日线/AKShare",
        "source_scope": "index_daily_history",
        "success": bool(history),
        "partial": False,
        "errors": [] if history else ["新浪指数日线没有可用记录"],
        "warnings": [],
        "data_time": data_date.isoformat() if data_date else None,
        "is_stale": expected is not None and data_date.isoformat() < expected
        if data_date
        else None,
        "freshness_unknown": data_date is None,
        "fallback_used": False,
        "_cached": cached,
        "_fetched_at": now.isoformat(),
    }


def read_index_quote_sina(
    index_code: str = "000001", *, use_cache: bool = True
) -> dict[str, Any]:
    """Read the Sina real-time index quote endpoint only."""
    code = _validated_index_code(index_code)
    frame, cached = (
        cached_call(
            "index-spot-sina:atomic:v1", _spot_frame, ttl_seconds=30, attempts=1
        )
        if use_cache
        else (_spot_frame(), False)
    )
    now = datetime.now().astimezone()
    session_date = _expected_session_date(now)
    item = _spot_record(frame, code, session_date)
    return {
        "index_code": code,
        "index_name": INDEX_MAP[code][0],
        "item": item,
        "units": {
            "price": "index_point",
            "volume": "source_reported",
            "amount": "CNY",
            "pct_chg": "%",
        },
        "source": "新浪指数实时行情/AKShare",
        "source_scope": "index_realtime_quote",
        "success": item is not None,
        "partial": False,
        "errors": [] if item else ["新浪指数实时行情没有返回该指数"],
        "warnings": [],
        "data_time": session_date if item else None,
        "data_time_inferred": item is not None,
        "is_stale": None,
        "freshness_unknown": True,
        "fallback_used": False,
        "_cached": cached,
        "_fetched_at": now.isoformat(),
    }
