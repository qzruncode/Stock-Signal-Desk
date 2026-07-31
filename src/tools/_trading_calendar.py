# -*- coding: utf-8 -*-
"""Single A-share trading-calendar and session-time contract for tools."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache


@lru_cache(maxsize=1)
def trade_dates() -> list[date]:
    """Return the official mainland trading-day series exposed by AKShare."""
    import akshare as ak

    frame = ak.tool_trade_date_hist_sina()
    if frame is None or frame.empty or "trade_date" not in frame.columns:
        raise RuntimeError("交易日历为空")
    values: list[date] = []
    for value in frame["trade_date"].tolist():
        if isinstance(value, datetime):
            values.append(value.date())
        elif isinstance(value, date):
            values.append(value)
        else:
            try:
                values.append(datetime.fromisoformat(str(value)[:10]).date())
            except ValueError:
                continue
    if not values:
        raise RuntimeError("交易日历没有可解析日期")
    return sorted(set(values))


def expected_trade_day(now: datetime, calendar: list[date] | None = None) -> date:
    """Return the active trade date, switching at the 09:15 call auction."""
    calendar = sorted(day for day in (calendar or trade_dates()) if day <= now.date())
    if not calendar:
        raise RuntimeError("交易日历中找不到当前日期之前的交易日")
    if now.date() in calendar and now.time() >= time(9, 15):
        return now.date()
    earlier = [day for day in calendar if day < now.date()]
    return earlier[-1] if earlier else calendar[-1]


def fallback_trade_day(now: datetime) -> date:
    """Weekday-only fallback used when the official calendar is unavailable."""
    day = now.date()
    if day.weekday() < 5 and now.time() >= time(9, 15):
        return day
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def is_trading_time(now: datetime | None = None, calendar: list[date] | None = None) -> bool:
    """Whether *now* is inside an official A-share continuous session."""
    now = now or datetime.now().astimezone()
    current = now.time()
    in_session = time(9, 30) <= current <= time(11, 30) or time(13, 0) <= current <= time(15, 0)
    if not in_session or now.weekday() >= 5:
        return False
    try:
        active_calendar = calendar if calendar is not None else trade_dates()
        return now.date() in active_calendar
    except Exception:
        return now.weekday() < 5


_fetch_trade_dates = trade_dates
_fallback_trade_day = fallback_trade_day


__all__ = [
    "trade_dates",
    "expected_trade_day",
    "fallback_trade_day",
    "is_trading_time",
    "_fetch_trade_dates",
    "_fallback_trade_day",
]
