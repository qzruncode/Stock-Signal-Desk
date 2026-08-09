# -*- coding: utf-8 -*-
"""Parameterized all-market ATR relative-volatility stock screener tool."""

from __future__ import annotations

from typing import Any, Mapping

from src.services.stock_screening.atr_volatility_screener import run_atr_volatility_screen
from src.services.stock_screening.screen_spec import quantitative_screen_spec_schema
from src.storage import DatabaseManager
from src.tools.base import ToolEffect, ToolSpec, object_schema


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


def _screen_effect(arguments: Mapping[str, Any]) -> ToolEffect:
    """Saving a result group is the only side effect of this tool."""
    return "side_effect" if str(arguments.get("save_group_name") or "").strip() else "read"


TOOL = ToolSpec(
    name="screen_atr_volatility_stocks",
    description=(
        "在全部正常上市 A 股中执行可审计的 ATR 相对波动率选股；"
        "必须提供完整 screen_spec，服务端负责刷新行情、计算指标并返回预览。"
        "若提供 save_group_name，则将全部命中股票保存到该自选分组，保存操作需要用户审批。"
    ),
    parameters=object_schema(
        {
            "screen_spec": quantitative_screen_spec_schema(),
            "refresh_if_stale": {
                "type": "boolean",
                "default": True,
                "description": "必须刷新到最近交易日；保留该参数仅用于契约显式化。",
            },
            "save_group_name": {
                "type": "string",
                "default": "",
                "description": "可选的自选分组名称；留空则只返回筛选结果。",
            },
        },
        required=("screen_spec",),
    ),
    executor=screen_atr_volatility_stocks,
    category="deterministic_calculation",
    effect_resolver=_screen_effect,
    timeout_seconds=900.0,
    max_attempts=1,
)


__all__ = ["TOOL", "screen_atr_volatility_stocks"]
