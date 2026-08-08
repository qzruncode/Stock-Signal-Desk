"""Legacy aggregate maintenance dashboard helper.

It intentionally is not an Agent tool: one dashboard response combines several
unrelated local datasets and maintenance jobs, so it cannot be represented as
a single atomic observation in the model catalog.
"""

from __future__ import annotations

from typing import Any

from src.services.data_maintenance import get_data_health as _get_health


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

__all__ = ["get_data_health"]
