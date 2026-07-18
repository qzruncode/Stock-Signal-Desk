# -*- coding: utf-8 -*-
"""Parameterized all-market ATR relative-volatility stock screener tool."""

from src.services.stock_screening.screen_spec import quantitative_screen_spec_schema
from src.services.stock_screening.atr_volatility_screener import run_atr_volatility_screen
from src.tools.base import ToolSpec, object_schema


TOOL = ToolSpec(
    name="screen_atr_volatility_stocks",
    description=(
        "按调用方提供的完整结构化规格刷新并筛选全市场A股。支持可配置ATR周期及SMA/EMA/"
        "Wilder平滑、长期基线、动态线运算、比较符、回看窗口、达标天数/比例、上市历史、"
        "市场/ST范围、TTM财务过滤、排序和输出字段。工具会严格校验并原样回传实际执行规格；"
        "缺少条件、超出能力或数据覆盖不完整时失败关闭，禁止静默套用固定示例参数。"
    ),
    parameters=object_schema({
        "screen_spec": quantitative_screen_spec_schema(),
        "refresh_if_stale": {
            "type": "boolean",
            "description": "必须为 true；先刷新到最近交易日再筛选。",
            "default": True,
        },
    }, required=("screen_spec", "refresh_if_stale")),
    executor=run_atr_volatility_screen,
    category="analysis",
)
