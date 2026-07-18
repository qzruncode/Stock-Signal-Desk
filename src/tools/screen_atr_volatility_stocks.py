# -*- coding: utf-8 -*-
"""Exact all-market ATR relative-volatility stock screener tool."""

from src.services.stock_screening.atr_volatility_screener import run_atr_volatility_screen
from src.tools.base import ToolSpec, object_schema


TOOL = ToolSpec(
    name="screen_atr_volatility_stocks",
    description=(
        "自动刷新全市场财务与前复权日线，并严格按14日SMA ATR相对波动率、"
        "60日长期均值/1.27动态警戒线、近250日70%达标率及TTM财务条件筛选A股。"
        "所有计算由工具完成；用于量化条件选股时直接调用，禁止由模型自行计算。"
    ),
    parameters=object_schema({
        "refresh_if_stale": {
            "type": "boolean",
            "description": "必须为 true；先刷新到最近交易日再筛选。",
            "default": True,
        },
    }),
    executor=run_atr_volatility_screen,
    category="analysis",
)
