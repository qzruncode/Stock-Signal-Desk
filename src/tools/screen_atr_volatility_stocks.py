# -*- coding: utf-8 -*-
"""Parameterized all-market ATR relative-volatility stock screener tool."""

from src.services.stock_screening.screen_spec import quantitative_screen_spec_schema
from src.services.stock_screening.atr_volatility_screener import run_atr_volatility_screen
from src.storage import DatabaseManager
from src.tools.base import ToolSpec, object_schema


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


TOOL = ToolSpec(
    name="screen_atr_volatility_stocks",
    description=(
        "按调用方提供的完整结构化规格刷新并筛选全市场A股。支持可配置ATR周期及SMA/EMA/"
        "Wilder平滑、长期基线、动态线运算、比较符、回看窗口、达标天数/比例、上市历史、"
        "市场/ST范围、TTM财务过滤、排序和输出字段。工具会严格校验并原样回传实际执行规格；"
        "缺少条件、超出能力或数据覆盖不完整时失败关闭，禁止静默套用固定示例参数。"
        "仅当用户明确要求把完整筛选结果保存为自选分组时，才传 save_group_name。"
    ),
    parameters=object_schema(
        {
            "screen_spec": quantitative_screen_spec_schema(),
            "refresh_if_stale": {
                "type": "boolean",
                "description": "必须为 true；先刷新到最近交易日再筛选。",
                "default": True,
            },
            "save_group_name": {
                "type": "string",
                "description": "可选；用户明确要求保存完整结果时提供目标自选分组名称",
            },
        },
        required=("screen_spec", "refresh_if_stale"),
    ),
    executor=screen_atr_volatility_stocks,
    category="analysis",
)


__all__ = ["TOOL", "screen_atr_volatility_stocks"]
