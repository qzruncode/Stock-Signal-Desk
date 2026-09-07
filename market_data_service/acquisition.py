"""Source-aware incremental acquisition over the existing provider APIs."""

import math
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from market_data_service.control_models import DataState, Observation, utcnow
from market_data_service.database import get_database


def collect_kline(provider, arguments, previous=None, *, force_full=False, now=None):
    """Validate overlapping qfq bars before joining an existing immutable window.

    Corporate actions change old adjusted prices. A mismatched basis, provider
    switch, absent overlap, or weekly reconciliation always reads a full window.
    """
    now = now or utcnow()
    arguments = dict(arguments)
    count = int(arguments.get("count", 500))
    old = previous.payload if previous else {}
    rows = old.get("data") or []
    explicit_range = bool(arguments.get("start_date") or arguments.get("end_date"))
    selected = arguments.get("source_id", "auto")
    full_at = old.get("full_checked_at") or (
        previous.fetched_at.isoformat() if previous else None
    )
    try:
        reconcile_due = not full_at or now - datetime.fromisoformat(
            full_at
        ) >= timedelta(days=7)
    except (TypeError, ValueError):
        reconcile_due = True

    def full(reason):
        result = provider.kline(**arguments)
        return {
            **result,
            "sync_mode": "full",
            "sync_reason": reason,
            "full_checked_at": now.isoformat(),
        }

    if force_full or explicit_range or len(rows) < count or reconcile_due:
        return full("explicit_or_bootstrap_or_reconciliation")
    if selected not in {"auto", old.get("source")}:
        return full("source_changed")
    # Keep enough overlapping sessions to detect adjustments and recent fixes.
    overlap = rows[-20:]
    incremental = {**arguments, "start_date": str(overlap[0]["date"])[:10]}
    delta = provider.kline(**incremental)
    new_rows = delta.get("data") or []
    if (
        not new_rows
        or delta.get("success") is False
        or delta.get("source") != old.get("source")
    ):
        return full("source_or_window_changed")
    indexed = {str(row["date"])[:10]: row for row in new_rows}
    old_index = {str(row["date"])[:10]: row for row in rows}
    common = set(indexed).intersection(old_index)
    expected_overlap = {str(row["date"])[:10] for row in overlap}
    if not expected_overlap.issubset(common) or max(indexed) < max(old_index):
        return full("incomplete_overlap")
    try:
        same_basis = all(
            math.isclose(
                float(indexed[day][key]),
                float(old_index[day][key]),
                rel_tol=1e-6,
                abs_tol=0.001,
            )
            for day in common
            for key in ("open", "high", "low", "close")
        )
    except (TypeError, KeyError, ValueError):
        same_basis = False
    if not same_basis:
        return full("adjustment_or_historical_revision")
    merged = old_index | indexed
    result_rows = [merged[day] for day in sorted(merged)][-count:]
    return {
        **delta,
        "data": result_rows,
        "count": len(result_rows),
        "requested_count": count,
        "requested_start": str(result_rows[0]["date"])[:10],
        "data_time": str(result_rows[-1]["date"])[:10],
        "partial": len(result_rows) < count,
        "sync_mode": "incremental",
        "incremental_from": incremental["start_date"],
        "full_checked_at": full_at,
    }


def read_kline(arguments, *, force_full=False):
    from redis import Redis
    from redis.exceptions import LockNotOwnedError

    from market_data_service.providers.live import get_provider
    from market_data_service.settings import get_settings

    settings, database = get_settings(), get_database()
    # The symbol lock is shared by scheduled jobs and on-demand requests even
    # when their request-window keys differ. The existing source lock alone
    # cannot fence these two acquisition paths.
    with Redis.from_url(settings.broker_url) as redis:
        lock = redis.lock(
            f"market-data:kline:{arguments['symbol']}",
            timeout=190,
            blocking_timeout=settings.request_timeout,
        )
        if not lock.acquire():
            raise RuntimeError("该证券的日线正在采集，请等待当前采集完成")
        try:
            with database.get_session() as session:
                state = session.scalar(
                    select(DataState).where(
                        DataState.dataset == "kline",
                        DataState.symbol == arguments["symbol"],
                    )
                )
                previous = (
                    session.get(Observation, state.version)
                    if state and state.version
                    else None
                )
            return collect_kline(
                get_provider(), arguments, previous, force_full=force_full
            )
        finally:
            try:
                lock.release()
            except LockNotOwnedError:
                pass


def next_source_run(dataset, interval, *, now=None):
    now = now or utcnow()
    normal = now + timedelta(seconds=interval)
    if dataset != "quotes":
        return normal
    from bisect import bisect_left

    from market_data_service.calendar import is_trading_time, trade_dates

    local = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("Asia/Shanghai"))
    try:
        days = trade_dates()
        if is_trading_time(local):
            return normal
    except RuntimeError:
        return normal
    for day in days[bisect_left(days, local.date()) :]:
        for hour, minute in ((9, 15), (13, 0)):
            start = datetime.combine(day, time(hour, minute), local.tzinfo)
            if start > local:
                return max(
                    normal, start.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
                )
    return normal
