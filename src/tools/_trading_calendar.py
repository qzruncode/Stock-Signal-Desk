"""Exchange-session rules over the data service's authoritative calendar."""

from datetime import date, datetime, time
from functools import lru_cache
import time as clock
from zoneinfo import ZoneInfo
from src.services.market_data_client import get_market_data_client

SHANGHAI = ZoneInfo("Asia/Shanghai")


@lru_cache(maxsize=2)
def _calendar_bucket(_bucket):
    return [date.fromisoformat(day) for day in get_market_data_client().calendar()]


def trade_dates():
    return _calendar_bucket(int(clock.time() // 60))


def latest_completed_trade_day(now=None, calendar=None):
    now = now or datetime.now(SHANGHAI)
    days = calendar if calendar is not None else trade_dates()
    eligible = [
        day
        for day in days
        if day < now.date() or (day == now.date() and now.time() >= time(15, 15))
    ]
    if not eligible or max(days).year < now.year:
        raise RuntimeError("交易日历未覆盖当前日期")
    return max(eligible)


def expected_trade_day(now=None, calendar=None):
    now = now or datetime.now(SHANGHAI)
    days = calendar if calendar is not None else trade_dates()
    eligible = [
        day
        for day in days
        if day < now.date() or (day == now.date() and now.time() >= time(9, 15))
    ]
    if not eligible:
        raise RuntimeError("交易日历未覆盖当前日期")
    return max(eligible)


def is_trading_time(now=None, calendar=None):
    now = now or datetime.now(SHANGHAI)
    return now.date() in (calendar if calendar is not None else trade_dates()) and (
        time(9, 15) <= now.time() <= time(11, 30)
        or time(13) <= now.time() <= time(15, 5)
    )


_fetch_trade_dates = trade_dates
# Legacy adapters use this name; an official calendar is still required.
fallback_trade_day = expected_trade_day
_fallback_trade_day = expected_trade_day
