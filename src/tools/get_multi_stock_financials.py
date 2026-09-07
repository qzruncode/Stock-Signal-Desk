"""Single-source financial snapshot reads for the Agent catalog.

The Agent no longer receives a tool that switches from a local synchronized
table to an Eastmoney annual statement according to a ``period_basis`` flag.
Those are two different sources with different freshness semantics, so they
are exposed as two explicit atomic reads.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from src.tools.base import ToolSpec, object_schema
from src.tools.symbols import resolve_local_securities_csv


_MAX_SECURITIES = 24


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _load_period_snapshot(period: str) -> tuple[dict[str, dict[str, Any]], datetime]:
    from src.services.market_data_client import read_source

    result = read_source("financials.fetch_period", {"period": period})
    return result["data"], datetime.fromisoformat(
        result["data_service"]["checked_at"].replace("Z", "+00:00")
    )


def _resolve(symbols: str) -> tuple[list[dict[str, str]], list[str]]:
    resolved, unresolved = resolve_local_securities_csv(symbols)
    return resolved[:_MAX_SECURITIES], unresolved


def _empty_result(*, unresolved: list[str], source: str, error: str) -> dict[str, Any]:
    return {
        "success": False,
        "partial": False,
        "items": [],
        "resolved_entities": [],
        "unresolved_entities": unresolved,
        "requested_count": 0,
        "covered_count": 0,
        "missing_symbols": [],
        "source": source,
        "data_time": None,
        "data_time_provenance": "unavailable",
        "data_time_note": "没有可用的已确认证券实体，因此未读取该数据源。",
        "is_stale": None,
        "freshness_unknown": True,
        "errors": [error],
        "warnings": [],
    }


def _report_time(items: list[dict[str, Any]]) -> str | None:
    dates = [
        str(item.get("report_date") or "")[:10]
        for item in items
        if item.get("report_date")
    ]
    return max(dates) if dates else None


def read_local_financial_snapshot(symbols: str) -> dict[str, Any]:
    """Stable v1 tool identifier; data comes from the independent service."""
    from src.services.market_data_client import get_market_data_client

    resolved, unresolved = _resolve(symbols)
    if not resolved:
        return _empty_result(
            unresolved=unresolved,
            source="market-data-service",
            error="没有可确认的 A 股公司名称或代码",
        )
    codes = [entity["symbol"] for entity in resolved]
    result = get_market_data_client().snapshot(codes, ["financials"])
    items = []
    missing = []
    for entity in resolved:
        code = entity["symbol"]
        row = result["items"].get(code, {}).get("financials")
        if not row:
            missing.append(code)
            continue
        items.append(
            {
                **entity,
                "report_date": row.get("report_date"),
                "financial_fetched_at": row.get("financial_fetched_at"),
                "debt_ratio_pct": row.get("debt_ratio"),
                "revenue_ttm": row.get("revenue_ttm"),
                "deducted_net_profit_ttm": row.get("deducted_net_profit_ttm"),
                "data_versions": result["items"][code]["versions"],
            }
        )
    times = [item["report_date"] for item in items if item.get("report_date")]
    return {
        "success": bool(items),
        "partial": bool(items) and bool(missing or unresolved),
        "items": items,
        "resolved_entities": resolved,
        "unresolved_entities": unresolved,
        "requested_count": len(resolved),
        "covered_count": len(items),
        "missing_symbols": missing,
        "source": "market-data-service",
        "data_time": min(times) if times else None,
        "data_time_provenance": "source" if times else "unavailable",
        "is_stale": False if items else None,
        "freshness_unknown": not bool(times),
        "errors": [
            *[f"无法确认: {name}" for name in unresolved],
            *[f"缺少达标财务快照: {code}" for code in missing],
        ],
        "warnings": [],
    }


def read_annual_financial_snapshot_eastmoney(
    symbols: str, fiscal_year: int
) -> dict[str, Any]:
    """Read one explicit fiscal-year statement snapshot from Eastmoney."""
    if not 1990 <= int(fiscal_year) <= 2100:
        raise ValueError("fiscal_year 必须在 1990 到 2100 之间")
    resolved, unresolved = _resolve(symbols)
    period = f"{int(fiscal_year):04d}-12-31"
    source = f"东方财富财务主指标 {period} 年度快照"
    if not resolved:
        return _empty_result(
            unresolved=unresolved,
            source=source,
            error="没有可在本地证券主数据中确认的 A 股公司名称或代码",
        )

    by_code, fetched_at = _load_period_snapshot(period)
    items: list[dict[str, Any]] = []
    missing_symbols: list[str] = []
    for entity in resolved:
        code = entity["symbol"]
        row = by_code.get(code)
        if not isinstance(row, dict):
            missing_symbols.append(code)
            continue
        items.append(
            {
                "symbol": code,
                "name": entity["name"],
                "input": entity["input"],
                "report_date": str(row.get("REPORT_DATE") or period)[:10],
                "source_fetched_at": fetched_at.isoformat(),
                "debt_ratio_pct": _safe_float(row.get("ZCFZL")),
                "revenue": _safe_float(row.get("TOTALOPERATEREVE")),
                "net_profit": _safe_float(row.get("PARENTNETPROFIT")),
                "deducted_net_profit": _safe_float(row.get("KCFJCXSYJLR")),
            }
        )

    errors: list[str] = []
    if unresolved:
        errors.append(f"无法在本地证券主数据中确认: {', '.join(unresolved)}")
    if missing_symbols:
        errors.append(f"{period} 年度快照缺少: " + ", ".join(missing_symbols))
    data_time = _report_time(items)
    success = bool(items)
    return {
        "success": success,
        "partial": success and bool(errors),
        "items": items,
        "resolved_entities": resolved,
        "unresolved_entities": unresolved,
        "requested_count": len(resolved),
        "covered_count": len(items),
        "missing_symbols": missing_symbols,
        "fiscal_year": int(fiscal_year),
        "source": source,
        "data_time": data_time,
        "data_time_provenance": "source" if data_time else "unavailable",
        "data_time_note": (None if data_time else "东财年度快照未返回可用报告期。"),
        "is_stale": None,
        "freshness_unknown": not bool(data_time),
        "errors": errors if success else [*errors, f"未找到 {period} 年度财务快照"],
        "warnings": [],
    }


TOOLS = (
    ToolSpec(
        name="read_local_financial_snapshot",
        description=(
            "通过独立数据服务读取至多 24 只已确认 A 股的达标财务快照及数据版本。"
            "返回报告期、资产负债率、TTM 营收与 TTM 扣非净利润；业务侧不直接采集、不评分。"
        ),
        parameters=object_schema(
            {
                "symbols": {
                    "type": "string",
                    "minLength": 1,
                    "description": "逗号分隔的已确认 A 股代码或名称，最多 24 只",
                }
            },
            ["symbols"],
        ),
        executor=read_local_financial_snapshot,
        category="financials",
        max_attempts=1,
    ),
    ToolSpec(
        name="read_annual_financial_snapshot_eastmoney",
        description=(
            "从东方财富读取一个明确财年（12 月 31 日）的财务主指标快照，最多 24 只已确认 A 股。"
            "返回资产负债率、营收、归母净利润和扣非净利润原始字段；不读取本地快照、不切换来源、不排名。"
        ),
        parameters=object_schema(
            {
                "symbols": {
                    "type": "string",
                    "minLength": 1,
                    "description": "逗号分隔的已确认 A 股代码或名称，最多 24 只",
                },
                "fiscal_year": {
                    "type": "integer",
                    "minimum": 1990,
                    "maximum": 2100,
                    "description": "明确的年度，例如 2025",
                },
            },
            ["symbols", "fiscal_year"],
        ),
        executor=read_annual_financial_snapshot_eastmoney,
        category="financials",
        max_attempts=1,
    ),
)


__all__ = [
    "TOOLS",
    "_load_period_snapshot",
    "read_annual_financial_snapshot_eastmoney",
    "read_local_financial_snapshot",
]
