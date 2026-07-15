# -*- coding: utf-8 -*-
"""``get_business_segments`` — reported revenue/profit composition."""

from __future__ import annotations

from typing import Any

import akshare as ak

from src.tools._akshare import bare_symbol, cached_call, exchange_prefix, frame_records, source_meta
from src.tools.base import ToolSpec, object_schema

DESCRIPTION = (
    "获取上市公司最新报告期的主营构成，按产品、行业或地区展示收入、成本、利润、"
    "收入占比、利润占比和毛利率；用于判断业务结构、收入集中度和利润来源。数据来自 AKShare。"
)


def get_business_segments(symbol: str, category: str = "all", periods: int = 2) -> dict[str, Any]:
    code = bare_symbol(symbol)
    try:
        frame, cached = cached_call(
            f"business_segments:{code}",
            lambda: ak.stock_zygc_em(symbol=exchange_prefix(code, upper=True)),
            ttl_seconds=6 * 3600,
        )
    except Exception as exc:
        return {
            "symbol": code,
            "items": [],
            "periods": [],
            "errors": [str(exc)],
            **source_meta(cached=False, available=False),
        }
    records = frame_records(frame)
    if not records:
        return {
            "symbol": code,
            "items": [],
            "periods": [],
            "errors": ["AKShare 未返回主营构成数据"],
            **source_meta(cached=cached, available=False),
        }

    report_key = next((key for key in ("报告日期", "报告期", "REPORT_DATE") if key in records[0]), None)
    type_key = next((key for key in ("分类类型", "分类方向", "MAINOP_TYPE") if key in records[0]), None)
    wanted = {"product": "产品", "industry": "行业", "region": "地区"}.get(category)
    available_periods = sorted({str(row.get(report_key)) for row in records if report_key and row.get(report_key)}, reverse=True)
    selected_periods = available_periods[: max(1, min(int(periods), 8))]
    selected = [
        row for row in records
        if (not report_key or str(row.get(report_key)) in selected_periods)
        and (not wanted or not type_key or wanted in str(row.get(type_key) or ""))
    ]
    return {
        "symbol": code,
        "category": category,
        "periods": selected_periods,
        "items": selected[:80],
        "item_count": len(selected),
        "errors": [],
        **source_meta(cached=cached),
    }


TOOL = ToolSpec(
    name="get_business_segments",
    description=DESCRIPTION,
    parameters=object_schema(
        {
            "symbol": {"type": "string", "description": "股票代码或股票名称，如 600519 或 贵州茅台"},
            "category": {
                "type": "string",
                "enum": ["all", "product", "industry", "region"],
                "default": "all",
                "description": "主营分类：all 全部、product 产品、industry 行业、region 地区",
            },
            "periods": {"type": "integer", "minimum": 1, "maximum": 8, "default": 2, "description": "返回最近报告期数量"},
        },
        ["symbol"],
    ),
    executor=get_business_segments,
    category="financials",
)
