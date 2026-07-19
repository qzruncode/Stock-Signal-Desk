"""Explain the datasets maintained automatically for Agent analysis."""

from __future__ import annotations

from typing import Any

from src.services.data_maintenance import get_data_health as _get_health
from src.tools.base import ToolSpec, object_schema


def get_data_health() -> dict[str, Any]:
    health = _get_health()
    universe = health["stock_universe"]
    warnings = []
    if universe["total"] == 0:
        warnings.append("股票基础库为空；下一次股票搜索或全市场分析会自动初始化")
    elif universe["is_stale"]:
        warnings.append("股票基础库已过期；下一次依赖股票池的工具会自动刷新")
    return {
        "success": True,
        "partial": bool(warnings),
        **health,
        "data_time": universe.get("data_time"),
        "is_stale": universe.get("is_stale") if universe.get("data_time") else None,
        "freshness_unknown": universe.get("data_time") is None,
        "errors": [],
        "warnings": warnings,
    }


TOOL = ToolSpec(
    name="get_data_health",
    description="查看助手自动维护的股票池、K线和财务数据覆盖率、数据时间及最近维护任务。",
    parameters=object_schema(),
    executor=get_data_health,
    category="data",
)


__all__ = ["TOOL", "get_data_health"]
