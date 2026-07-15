# -*- coding: utf-8 -*-
"""``get_peer_comparison`` — AKShare industry peer matrices."""

from __future__ import annotations

from typing import Any, Callable

import akshare as ak

from src.tools._akshare import bare_symbol, cached_call, exchange_prefix, frame_records, source_meta
from src.tools.base import ToolSpec, object_schema

DESCRIPTION = (
    "获取公司与同行在成长性、估值和杜邦盈利能力上的横向比较，保留行业平均/中值及排名；"
    "用于识别相对优势和估值溢价，避免只看单家公司绝对数。"
)

_DIMENSIONS: dict[str, tuple[str, Callable[..., Any]]] = {
    "growth": ("成长性", ak.stock_zh_growth_comparison_em),
    "valuation": ("估值", ak.stock_zh_valuation_comparison_em),
    "profitability": ("杜邦盈利能力", ak.stock_zh_dupont_comparison_em),
}


def get_peer_comparison(symbol: str, dimension: str = "all") -> dict[str, Any]:
    code = bare_symbol(symbol)
    prefixed = exchange_prefix(code, upper=True)
    dimensions = list(_DIMENSIONS) if dimension == "all" else [dimension]
    matrices: dict[str, Any] = {}
    errors: list[str] = []
    any_cached = False
    for key in dimensions:
        label, fn = _DIMENSIONS[key]
        try:
            frame, cached = cached_call(
                f"peer:{code}:{key}",
                lambda fn=fn: fn(symbol=prefixed),
                ttl_seconds=2 * 3600,
            )
            rows = frame_records(frame)
            matrices[key] = {"label": label, "items": rows[:12], "item_count": len(rows)}
            any_cached = any_cached or cached
            if not rows:
                errors.append(f"{label}暂无同行数据")
        except Exception as exc:
            matrices[key] = {"label": label, "items": [], "item_count": 0}
            errors.append(f"{label}: {exc}")
    available = any(bucket.get("items") for bucket in matrices.values())
    return {
        "symbol": code,
        "dimensions": matrices,
        "partial": available and bool(errors),
        "errors": errors,
        **source_meta(cached=any_cached, source="AKShare/东方财富同行比较", available=available),
    }


TOOL = ToolSpec(
    name="get_peer_comparison",
    description=DESCRIPTION,
    parameters=object_schema(
        {
            "symbol": {"type": "string", "description": "股票代码或股票名称"},
            "dimension": {
                "type": "string",
                "enum": ["all", "growth", "valuation", "profitability"],
                "default": "all",
                "description": "比较维度：成长性、估值、杜邦盈利能力或全部",
            },
        },
        ["symbol"],
    ),
    executor=get_peer_comparison,
    category="analysis",
)
