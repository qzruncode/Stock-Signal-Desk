# -*- coding: utf-8 -*-
"""Multi-cycle A-share market-regime evidence from AKShare source feeds."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Callable

from src.services.akshare_evidence.common import dataset_errors, fetch_frame, latest_data_time
from src.tools.base import ToolSpec, object_schema


def _tail(result: dict[str, Any], count: int) -> dict[str, Any]:
    items = result.get("items") or []
    result["items"] = items[-count:]
    result["item_count"] = len(result["items"])
    result["latest"] = result["items"][-1] if result["items"] else None
    return result


def get_market_regime(index: str = "沪深300", history_points: int = 120) -> dict[str, Any]:
    import akshare as ak

    history_points = max(20, min(int(history_points), 500))
    plans: dict[str, tuple[str, str, Callable[[], Any], int]] = {
        "new_high_low": (
            "stock_a_high_low_statistics",
            "market:high_low:all",
            lambda: ak.stock_a_high_low_statistics(symbol="all"),
            1800,
        ),
        "equity_bond_spread": ("stock_ebs_lg", "market:equity_bond_spread", lambda: ak.stock_ebs_lg(), 6 * 3600),
        "shanghai_pe": ("stock_market_pe_lg", "market:pe:shanghai", lambda: ak.stock_market_pe_lg(symbol="上证"), 6 * 3600),
        "shenzhen_pe": ("stock_market_pe_lg", "market:pe:shenzhen", lambda: ak.stock_market_pe_lg(symbol="深证"), 6 * 3600),
        "shanghai_pb": ("stock_market_pb_lg", "market:pb:shanghai", lambda: ak.stock_market_pb_lg(symbol="上证"), 6 * 3600),
        "shenzhen_pb": ("stock_market_pb_lg", "market:pb:shenzhen", lambda: ak.stock_market_pb_lg(symbol="深证"), 6 * 3600),
        "index_pe": ("stock_index_pe_lg", f"market:index_pe:{index}", lambda: ak.stock_index_pe_lg(symbol=index), 6 * 3600),
        "congestion": ("stock_a_congestion_lg", "market:congestion", lambda: ak.stock_a_congestion_lg(), 3600),
    }
    datasets: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(fetch_frame, api, cache_key, factory, ttl_seconds=ttl): name
            for name, (api, cache_key, factory, ttl) in plans.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                datasets[name] = _tail(future.result(), history_points)
            except Exception as exc:
                datasets[name] = {
                    "success": False,
                    "partial": False,
                    "source_api": f"AKShare.{plans[name][0]}",
                    "items": [],
                    "item_count": 0,
                    "source_row_count": None,
                    "cached": False,
                    "latest": None,
                    "error": f"{type(exc).__name__}: {str(exc)[:400]}",
                }
    datasets = {name: datasets[name] for name in plans}
    errors = dataset_errors(datasets)
    successful = [dataset for dataset in datasets.values() if dataset.get("success")]
    data_time = latest_data_time(datasets)
    return {
        "index": index,
        "datasets": datasets,
        "available_dataset_count": len(successful),
        "required_dataset_count": len(datasets),
        "coverage_complete": all(
            dataset.get("success") is True and dataset.get("partial") is not True
            for dataset in datasets.values()
        ),
        "interpretation_contract": {
            "new_high_low": "20/60/120 日创新高与新低家数，按源字段原样报告，不从涨跌家数伪造。",
            "equity_bond_spread": "股债利差是跨资产相对估值证据，不是择时阈值。",
            "valuation": "市场和指数 PE/PB 仅描述估值位置，不自动推出涨跌。",
            "congestion": "拥挤度用于观察交易集中与过热风险，不作为单一买卖信号。",
        },
        "source": "AKShare market regime feeds",
        "source_repository": "https://github.com/akfamily/akshare",
        "success": bool(successful),
        "partial": bool(successful) and bool(errors),
        "errors": errors,
        "warnings": (["部分市场状态源不可用，不得把缺失值解释为 0。"] if errors else []),
        "data_time": data_time,
        "retrieved_at": datetime.now().astimezone().isoformat(),
        "is_stale": None,
        "freshness_unknown": data_time is None,
    }


TOOL = ToolSpec(
    name="get_market_regime",
    description=(
        "获取 A 股多周期市场状态：20/60/120 日创新高新低、股债利差、沪深市场 PE/PB、"
        "指定指数 PE 和市场拥挤度。保留各源覆盖与失败边界，不用涨跌家数伪造高低点。"
    ),
    parameters=object_schema(
        {
            "index": {
                "type": "string",
                "enum": ["上证50", "沪深300", "中证500", "中证1000", "中证800", "深证100", "创业板50"],
                "default": "沪深300",
            },
            "history_points": {"type": "integer", "minimum": 20, "maximum": 500, "default": 120},
        }
    ),
    executor=get_market_regime,
    category="market",
)


__all__ = ["TOOL", "get_market_regime"]
