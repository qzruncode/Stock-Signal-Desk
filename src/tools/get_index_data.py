"""Business-correct mainland index history plus current-session snapshot."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.tools._akshare import cached_call
from src.tools._macro_common import get_db, latest_date, number, ordered
from src.tools.base import ToolSpec, object_schema


INDEX_MAP = {
    "000001": ("上证指数", "sh000001"),
    "399001": ("深证成指", "sz399001"),
    "399006": ("创业板指", "sz399006"),
    "000688": ("科创50", "sh000688"),
}


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
                "change_amount": round(close - previous, 4) if close is not None and previous is not None else None,
                "pct_chg": round((close / previous - 1) * 100, 4) if close is not None and previous else None,
                "record_type": "daily_close",
            }
        )
    return rows[-days:]


def _expected_session_date(now: datetime) -> str | None:
    try:
        from src.tools._trading_calendar import _fetch_trade_dates, expected_trade_day

        return expected_trade_day(now, _fetch_trade_dates()).isoformat()
    except Exception:
        return now.date().isoformat() if now.weekday() < 5 and now.hour >= 9 else None


def _spot_record(frame: Any, index_code: str, session_date: str | None) -> dict[str, Any] | None:
    if frame is None or frame.empty or not session_date:
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
    if close is None or not any(value for value in (open_, high, low)):
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


def _merge_snapshot(history: list[dict[str, Any]], snapshot: dict[str, Any] | None, days: int) -> list[dict[str, Any]]:
    if not snapshot:
        return history[-days:]
    by_date = {str(item.get("date")): dict(item) for item in history}
    existing = by_date.get(snapshot["date"])
    if existing:
        by_date[snapshot["date"]] = {**existing, **snapshot}
    else:
        previous = history[-1] if history else None
        previous_close = number(previous.get("close")) if previous else None
        quoted_previous = number(snapshot.get("previous_close"))
        # Only append a current-session quote when it connects to the fetched
        # daily series. This prevents a stale holiday snapshot from becoming a
        # fabricated new trading day.
        if (
            previous_close is not None
            and quoted_previous is not None
            and abs(previous_close - quoted_previous) <= max(0.01, abs(previous_close) * 1e-5)
        ):
            by_date[snapshot["date"]] = snapshot
    return ordered(list(by_date.values()), "date")[-days:]


def get_index_data(index_code: str = "000001", days: int = 20) -> dict[str, Any]:
    index_code = str(index_code or "").strip()
    days = int(days)
    if index_code not in INDEX_MAP:
        raise ValueError(f"不支持的指数代码: {index_code}")
    if not 5 <= days <= 250:
        raise ValueError("days 必须在 5 到 250 之间")

    errors: list[str] = []
    warnings: list[str] = []
    daily_cached = spot_cached = False
    daily = spot = None
    try:
        daily, daily_cached = cached_call(
            f"index-daily:{index_code}", lambda: _daily_frame(index_code), ttl_seconds=900
        )
    except Exception as exc:
        errors.append(f"新浪指数日线: {exc}")
    try:
        spot, spot_cached = cached_call("index-spot-sina", _spot_frame, ttl_seconds=30, attempts=1)
    except Exception as exc:
        warnings.append(f"新浪指数快照不可用: {exc}")

    history = _daily_records(daily, days)
    snapshot = _spot_record(spot, index_code, _expected_session_date(datetime.now()))
    fallback_used = False
    if history:
        history = _merge_snapshot(history, snapshot, days)
        try:
            get_db().save_macro_index_daily(index_code, history, data_source="AKShare/新浪")
        except Exception as exc:
            warnings.append(f"指数本地缓存写入失败: {exc}")
    else:
        fallback_used = True
        try:
            history = ordered(get_db().get_macro_index_daily(index_code, limit=days) or [], "date")[-days:]
        except Exception as exc:
            errors.append(f"指数本地缓存: {exc}")
            history = []

    latest = history[-1] if history else {}
    data_date = latest_date(history, "date")
    expected = _expected_session_date(datetime.now())
    success = bool(history)
    if success and data_date and expected and data_date.isoformat() < expected:
        warnings.append(f"最新指数记录为 {data_date.isoformat()}，尚未取得预期交易日 {expected} 数据")
    retrieved_at = datetime.now().astimezone().isoformat()
    return {
        "index_code": index_code,
        "index_name": INDEX_MAP[index_code][0],
        "days": days,
        "latest": latest,
        "history": history,
        "history_count": len(history),
        "units": {"price": "index_point", "volume": "source_reported", "amount": "CNY", "pct_chg": "%"},
        "source": "AKShare/新浪指数日线+实时快照" if not fallback_used else "本地历史缓存",
        "source_chain": (
            ["AKShare stock_zh_index_daily", "AKShare stock_zh_index_spot_sina"]
            if not fallback_used
            else ["本地指数日线缓存"]
        ),
        "success": success,
        "partial": success and bool(errors or warnings),
        "data_time": data_date.isoformat() if data_date else None,
        "retrieved_at": retrieved_at,
        "is_stale": (expected is not None and data_date.isoformat() < expected if data_date else None),
        "freshness_unknown": data_date is None,
        "fallback_used": fallback_used,
        "fallback_recommended": not success,
        "errors": errors[:10],
        "warnings": warnings[:10],
        "_cached": bool(daily_cached and spot_cached) if not fallback_used else True,
        "_fetched_at": retrieved_at,
    }


TOOL = ToolSpec(
    name="get_index_data",
    description=(
        "获取上证、深证、创业板或科创50的日线与当前交易日快照。实时快照只会在能与上一日收盘价对齐时"
        "并入历史，避免把今日行情覆盖到昨日日期；返回价格、涨跌幅、成交量和成交额的明确单位与数据时间。"
    ),
    parameters=object_schema(
        {
            "index_code": {"type": "string", "enum": list(INDEX_MAP), "default": "000001"},
            "days": {"type": "integer", "minimum": 5, "maximum": 250, "default": 20},
        }
    ),
    executor=get_index_data,
    category="macro",
)
