"""Business projections over an immutable, authoritative market snapshot."""

from typing import Any
from src.services.market_data_client import read_source


def get_market_snapshot(*, force=False, now=None):
    if now is not None:
        raise ValueError(
            "Historical replay must use a stored data version, not a fabricated current clock"
        )
    return read_source("market.snapshot", {"force": force})


def market_status_view(snapshot: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "market_date",
        "is_trading_time",
        "up_count",
        "down_count",
        "flat_count",
        "halt_count",
        "limit_up_count",
        "limit_down_count",
        "total_amount",
        "total_amount_unit",
        "turnover_scope",
        "indices",
        "sh_index",
        "breadth_scope",
        "breadth_source",
        "north_flow",
        "north_flow_available",
        "north_flow_note",
        "source",
        "errors",
        "warnings",
        "data_time",
        "is_stale",
        "fallback_used",
        "_fetched_at",
        "_cached",
    )
    result = {key: snapshot.get(key) for key in fields}
    result["success"] = snapshot.get("up_count") is not None or bool(
        snapshot.get("indices")
    )
    result["partial"] = result["success"] and bool(snapshot.get("errors"))
    return result


def market_breadth_view(snapshot: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "market_date",
        "up_count",
        "down_count",
        "flat_count",
        "halt_count",
        "advance_decline_ratio",
        "advance_rate_pct",
        "decline_rate_pct",
        "market_activity_pct",
        "limit_up_count",
        "limit_down_count",
        "real_limit_up_count",
        "real_limit_down_count",
        "broken_board_count",
        "broken_board_rate",
        "consecutive_up_days",
        "consecutive_down_days",
        "total_amount",
        "total_amount_unit",
        "turnover_scope",
        "breadth_scope",
        "breadth_source",
        "source",
        "errors",
        "warnings",
        "data_time",
        "is_stale",
        "fallback_used",
        "_fetched_at",
        "_cached",
    )
    result = {key: snapshot.get(key) for key in fields}
    result["success"] = (
        snapshot.get("up_count") is not None and snapshot.get("down_count") is not None
    )
    result["partial"] = result["success"] and bool(snapshot.get("errors"))
    return result
