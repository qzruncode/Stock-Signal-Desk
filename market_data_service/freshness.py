"""Freshness is per dataset/security, never the maximum date of a whole table."""

from datetime import datetime, time
from zoneinfo import ZoneInfo

from market_data_service.calendar import (
    expected_trade_day,
    latest_completed_trade_day,
    is_trading_time,
)
from market_data_service.control_models import utcnow


def state_status(state, policy, *, now=None):
    now = now or utcnow()
    if state is None or not state.last_success_at:
        return "failed" if state and state.error else "missing"
    if state.status in {"partial", "unknown"}:
        return state.status
    local_now = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(
        ZoneInfo("Asia/Shanghai")
    )
    if state.dataset == "kline":
        try:
            expected = latest_completed_trade_day(local_now).isoformat()
        except RuntimeError:
            return "unknown"
        if not state.data_time or state.data_time[:10] < expected:
            return "stale"
        return (
            "fresh"
            if (now - state.last_success_at).total_seconds() <= policy.max_age_seconds
            else "stale"
        )
    if state.dataset == "quotes":
        if not state.data_time:
            return "unknown"
        try:
            parsed = datetime.fromisoformat(state.data_time.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            parsed = parsed.astimezone(local_now.tzinfo)
            if (
                parsed > local_now.replace(microsecond=0)
                and (parsed - local_now).total_seconds() > 60
            ):
                return "unknown"
            expected = expected_trade_day(local_now)
            if parsed.date() < expected:
                return "stale"
            if (
                is_trading_time(local_now)
                and (local_now - parsed).total_seconds() > policy.max_age_seconds
            ):
                return "stale"
            if not is_trading_time(local_now):
                # During lunch or after close, the latest quote must at least
                # cover the completed session, not any quote from that date.
                cutoff = (
                    time(11, 30)
                    if expected == local_now.date() and local_now.time() < time(13)
                    else time(15)
                )
                if (
                    parsed.date() == expected
                    and parsed.timetz().replace(tzinfo=None) < cutoff
                ):
                    return "stale"
        except (ValueError, RuntimeError):
            return "unknown"
        return "fresh"
    if state.dataset == "financials":
        from market_data_service.providers.financial_data import (
            _expected_min_report_date,
        )

        if not state.data_time:
            return "unknown"
        if (
            state.data_time[:10]
            < _expected_min_report_date(local_now.date()).isoformat()
        ):
            return "stale"
    if (now - state.last_success_at).total_seconds() > policy.max_age_seconds:
        return "stale"
    return "fresh"


def iso(value):
    return value.isoformat() + "Z" if value else None
