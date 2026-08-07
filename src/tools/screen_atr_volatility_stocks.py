# -*- coding: utf-8 -*-
"""Parameterized all-market ATR relative-volatility stock screener tool."""

from src.services.stock_screening.atr_volatility_screener import run_atr_volatility_screen
from src.storage import DatabaseManager


def screen_atr_volatility_stocks(
    screen_spec: dict,
    refresh_if_stale: bool = True,
    save_group_name: str = "",
) -> dict:
    """Run the full screen and optionally persist every match as a custom group."""
    group_name = str(save_group_name or "").strip()
    result = run_atr_volatility_screen(
        screen_spec=screen_spec,
        refresh_if_stale=refresh_if_stale,
        include_matched_codes=bool(group_name),
    )
    matched_codes = list(result.pop("matched_codes", []) or [])
    if not group_name:
        return result
    if result.get("success") is not True:
        return result
    if not matched_codes:
        result.setdefault("warnings", []).append("筛选结果为空，未创建自选分组。")
        result["saved_group"] = None
        return result
    group = DatabaseManager.get_instance().upsert_watchlist_group(
        group_name,
        matched_codes,
        "agent_screener",
    )
    result["saved_group"] = {
        **group,
        "count": len(group.get("codes") or []),
    }
    return result


__all__ = ["screen_atr_volatility_stocks"]
