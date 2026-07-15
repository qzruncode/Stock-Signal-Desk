# -*- coding: utf-8 -*-
"""``get_consensus_estimates`` — sell-side EPS and net-profit consensus."""

from __future__ import annotations

from typing import Any

import akshare as ak

from src.tools._akshare import bare_symbol, cached_call, frame_records, source_meta
from src.tools.base import ToolSpec, object_schema

DESCRIPTION = (
    "获取券商一致盈利预测，包括未来年度 EPS、净利润预测、覆盖机构数、预测区间和行业均值；"
    "用于估值前瞻、预期差和业绩兑现分析。预测不是公司承诺，必须结合财报与研报日期判断。"
)

_INDICATORS = {
    "eps": "预测年报每股收益",
    "net_profit": "预测年报净利润",
}


def _forecast(code: str, metric: str) -> tuple[list[dict[str, Any]], bool]:
    indicator = _INDICATORS[metric]
    frame, cached = cached_call(
        f"consensus:{code}:{metric}",
        lambda: ak.stock_profit_forecast_ths(symbol=code, indicator=indicator),
        ttl_seconds=6 * 3600,
    )
    return frame_records(frame), cached


def get_consensus_estimates(symbol: str, metric: str = "all") -> dict[str, Any]:
    code = bare_symbol(symbol)
    metrics = list(_INDICATORS) if metric == "all" else [metric]
    data: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    any_cached = False
    for item in metrics:
        try:
            rows, cached = _forecast(code, item)
            data[item] = rows
            any_cached = any_cached or cached
            if not rows:
                errors.append(f"{item} 暂无机构一致预测")
        except Exception as exc:
            data[item] = []
            errors.append(f"{item}: {exc}")
    available = any(data.values())
    return {
        "symbol": code,
        "metrics": data,
        "coverage_available": available,
        "partial": available and bool(errors),
        "errors": errors,
        **source_meta(cached=any_cached, source="AKShare/同花顺", available=available),
    }


TOOL = ToolSpec(
    name="get_consensus_estimates",
    description=DESCRIPTION,
    parameters=object_schema(
        {
            "symbol": {"type": "string", "description": "股票代码或股票名称"},
            "metric": {
                "type": "string",
                "enum": ["all", "eps", "net_profit"],
                "default": "all",
                "description": "预测指标：all、eps 每股收益、net_profit 净利润",
            },
        },
        ["symbol"],
    ),
    executor=get_consensus_estimates,
    category="financials",
)
