# -*- coding: utf-8 -*-
"""Fast local financial snapshots for a bounded set of A-share securities."""

from __future__ import annotations

from typing import Any

from src.tools.base import ToolSpec, object_schema
from src.tools.symbols import resolve_securities_csv


DESCRIPTION = (
    "从本地已同步股票池一次读取多只 A 股的最新报告期财务字段，最多 12 只。"
    "适用于对上文候选股按资产负债率等财务指标分批筛选；不拉取实时行情、K线或技术指标。"
)


def get_multi_stock_financials(symbols: str) -> dict[str, Any]:
    resolved, unresolved = resolve_securities_csv(symbols)
    resolved = resolved[:12]
    codes = [item["symbol"] for item in resolved]
    if not codes:
        return {
            "success": False,
            "partial": False,
            "items": [],
            "resolved_entities": [],
            "unresolved_entities": unresolved,
            "requested_count": 0,
            "covered_count": 0,
            "missing_financial_symbols": [],
            "errors": ["没有可验证的 A 股公司名称或代码"],
            "warnings": [],
            "data_time": None,
            "is_stale": None,
        }

    from src.storage import DatabaseManager, StockMeta

    db = DatabaseManager.get_instance()
    with db.get_session() as session:
        rows = session.query(StockMeta).filter(StockMeta.code.in_(codes)).all()
        by_code = {row.code: row for row in rows}

    items: list[dict[str, Any]] = []
    missing_financial_symbols: list[str] = []
    fetched_times: list[str] = []
    for entity in resolved:
        code = entity["symbol"]
        row = by_code.get(code)
        fetched_at = (
            row.financial_fetched_at.isoformat()
            if row is not None and row.financial_fetched_at is not None
            else None
        )
        if fetched_at:
            fetched_times.append(fetched_at)
        debt_ratio = row.debt_ratio if row is not None else None
        if row is None or debt_ratio is None:
            missing_financial_symbols.append(code)
        items.append({
            "symbol": code,
            "name": entity["name"],
            "input": entity["input"],
            "report_date": row.report_date if row is not None else None,
            "debt_ratio_pct": debt_ratio,
            "revenue_ttm": row.revenue_ttm if row is not None else None,
            "deducted_net_profit_ttm": (
                row.deducted_net_profit_ttm if row is not None else None
            ),
            "financial_fetched_at": fetched_at,
        })

    errors: list[str] = []
    if unresolved:
        errors.append(f"无法解析: {', '.join(unresolved)}")
    if missing_financial_symbols:
        errors.append(
            "缺少最新财务数据: " + ", ".join(missing_financial_symbols)
        )
    covered_count = len(items) - len(missing_financial_symbols)
    success = covered_count > 0
    return {
        "success": success,
        "partial": success and bool(errors),
        "items": items,
        "resolved_entities": resolved,
        "unresolved_entities": unresolved,
        "requested_count": len(resolved),
        "covered_count": covered_count,
        "missing_financial_symbols": missing_financial_symbols,
        "data_time": max(fetched_times) if fetched_times else None,
        "is_stale": None,
        "source": "stock_meta 本地已同步最新报告期财务快照",
        "field_basis": {
            "debt_ratio_pct": "资产负债率(%)；百分数口径，例如 70 表示 70%",
            "revenue_ttm": "最近十二个月营业收入，单位元",
            "deducted_net_profit_ttm": "最近十二个月扣非净利润，单位元",
        },
        "errors": errors,
        "warnings": [],
    }


TOOL = ToolSpec(
    name="get_multi_stock_financials",
    description=DESCRIPTION,
    parameters=object_schema(
        {
            "symbols": {
                "type": "string",
                "description": "股票代码或公司名称，多个用逗号分隔，最多 12 只",
            },
        },
        ["symbols"],
    ),
    executor=get_multi_stock_financials,
    category="financials",
)


__all__ = ["TOOL", "get_multi_stock_financials"]
