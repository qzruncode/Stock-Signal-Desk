"""Persisted exchange calendar, with explicit failure instead of weekday guessing."""

from bisect import bisect_left, bisect_right
from datetime import datetime, time, timedelta
from functools import lru_cache
import time as clock
from zoneinfo import ZoneInfo

from sqlalchemy import select

from market_data_service.control_models import (
    TradingDay,
    DataState,
    DatasetPolicy,
    utcnow,
)
from market_data_service.database import get_database

SHANGHAI = ZoneInfo("Asia/Shanghai")


def trade_dates():
    return _calendar_bucket(int(clock.time() // 60))


@lru_cache(maxsize=2)
def _calendar_bucket(_bucket):
    from datetime import date

    with get_database().get_session() as session:
        state = session.scalar(
            select(DataState).where(
                DataState.dataset == "calendar", DataState.symbol == "all"
            )
        )
        policy = session.get(DatasetPolicy, "calendar")
        if (
            not state
            or not state.last_success_at
            or (utcnow() - state.last_success_at).total_seconds()
            > policy.max_age_seconds
        ):
            raise RuntimeError("交易日历尚未同步或已过期，无法确认数据时效")
        values = list(session.scalars(select(TradingDay.day).order_by(TradingDay.day)))
    if not values:
        raise RuntimeError("交易日历尚未就绪，无法确认数据时效")
    return [date.fromisoformat(value) for value in values]


def latest_completed_trade_day(now=None, calendar=None):
    now = now or datetime.now(SHANGHAI)
    days = calendar if calendar is not None else trade_dates()
    cutoff = (
        now.date() if now.time() >= time(15, 15) else now.date() - timedelta(days=1)
    )
    index = bisect_right(days, cutoff) - 1
    if index < 0 or days[-1].year < now.year:
        raise RuntimeError("交易日历未覆盖当前日期")
    return days[index]


def expected_trade_day(now=None, calendar=None):
    now = now or datetime.now(SHANGHAI)
    days = calendar if calendar is not None else trade_dates()
    cutoff = now.date() if now.time() >= time(9, 15) else now.date() - timedelta(days=1)
    index = bisect_right(days, cutoff) - 1
    if index < 0 or days[-1].year < now.year:
        raise RuntimeError("交易日历未覆盖当前日期")
    return days[index]


def is_trading_time(now=None, calendar=None):
    now = now or datetime.now(SHANGHAI)
    days = calendar if calendar is not None else trade_dates()
    index = bisect_left(days, now.date())
    return (
        index < len(days)
        and days[index] == now.date()
        and (
            time(9, 15) <= now.time() <= time(11, 30)
            or time(13) <= now.time() <= time(15, 5)
        )
    )


_fetch_trade_dates = trade_dates
# Legacy adapters use this name; an official calendar is still required.
fallback_trade_day = expected_trade_day
_fallback_trade_day = expected_trade_day
