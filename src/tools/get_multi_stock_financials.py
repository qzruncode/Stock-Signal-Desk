"""Single-source financial snapshot reads for the Agent catalog.

The Agent no longer receives a tool that switches from a local synchronized
table to an Eastmoney annual statement according to a ``period_basis`` flag.
Those are two different sources with different freshness semantics, so they
are exposed as two explicit atomic reads.
"""

from __future__ import annotations

import json
import math
import threading
from datetime import datetime, timedelta
from typing import Any

from src.tools.base import ToolSpec, object_schema
from src.tools.symbols import resolve_local_securities_csv


_PERIOD_CACHE_TTL = timedelta(hours=24)
_PERIOD_CACHE_VERSION = "v2"
_PERIOD_CACHE_LOCK = threading.RLock()
_PERIOD_MEMORY_CACHE: dict[str, tuple[datetime, dict[str, dict[str, Any]]]] = {}
_MAX_SECURITIES = 24


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _load_period_snapshot(period: str) -> tuple[dict[str, dict[str, Any]], datetime]:
    """Load one all-market Eastmoney report-period snapshot with caching."""
    now = datetime.now()
    with _PERIOD_CACHE_LOCK:
        in_memory = _PERIOD_MEMORY_CACHE.get(period)
        if in_memory and now - in_memory[0] <= _PERIOD_CACHE_TTL:
            return in_memory[1], in_memory[0]

        from src.storage import DatabaseManager

        db = DatabaseManager.get_instance()
        cache_key = f"financial_period_snapshot:{_PERIOD_CACHE_VERSION}:{period}"
        cached = db.get_tool_cache(cache_key)
        if isinstance(cached, dict):
            updated_at = cached.get("updated_at")
            if isinstance(updated_at, datetime) and now - updated_at <= _PERIOD_CACHE_TTL:
                try:
                    payload = json.loads(bytes(cached["payload"]).decode("utf-8"))
                    rows = payload.get("rows") if isinstance(payload, dict) else None
                    if isinstance(rows, dict):
                        _PERIOD_MEMORY_CACHE[period] = (updated_at, rows)
                        return rows, updated_at
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    pass

        from src.tools._financial_period_snapshot import fetch_financial_period_snapshot

        rows = fetch_financial_period_snapshot(period)
        fetched_at = datetime.now()
        db.save_tool_cache(
            cache_key,
            json.dumps({"period": period, "rows": rows}, ensure_ascii=False).encode("utf-8"),
        )
        _PERIOD_MEMORY_CACHE[period] = (fetched_at, rows)
        return rows, fetched_at


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
    dates = [str(item.get("report_date") or "")[:10] for item in items if item.get("report_date")]
    return max(dates) if dates else None


def read_local_financial_snapshot(symbols: str) -> dict[str, Any]:
    """Read one local ``stock_meta`` snapshot for a bounded security set."""
    resolved, unresolved = _resolve(symbols)
    source = "stock_meta 本地已同步财务快照"
    if not resolved:
        return _empty_result(
            unresolved=unresolved,
            source=source,
            error="没有可在本地证券主数据中确认的 A 股公司名称或代码",
        )

    from src.storage import DatabaseManager, StockMeta

    codes = [item["symbol"] for item in resolved]
    db = DatabaseManager.get_instance()
    with db.get_session() as session:
        rows = session.query(StockMeta).filter(StockMeta.code.in_(codes)).all()
    by_code = {str(row.code): row for row in rows}

    items: list[dict[str, Any]] = []
    missing_symbols: list[str] = []
    for entity in resolved:
        code = entity["symbol"]
        row = by_code.get(code)
        if row is None:
            missing_symbols.append(code)
            continue
        fetched = getattr(row, "financial_fetched_at", None)
        items.append(
            {
                "symbol": code,
                "name": entity["name"],
                "input": entity["input"],
                "report_date": getattr(row, "report_date", None),
                "financial_fetched_at": fetched.isoformat() if isinstance(fetched, datetime) else None,
                "debt_ratio_pct": _safe_float(getattr(row, "debt_ratio", None)),
                "revenue_ttm": _safe_float(getattr(row, "revenue_ttm", None)),
                "deducted_net_profit_ttm": _safe_float(getattr(row, "deducted_net_profit_ttm", None)),
            }
        )

    errors: list[str] = []
    if unresolved:
        errors.append(f"无法在本地证券主数据中确认: {', '.join(unresolved)}")
    if missing_symbols:
        errors.append("本地财务快照缺少: " + ", ".join(missing_symbols))
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
        "source": source,
        # Local synchronization time is intentionally not presented as the
        # financial statement's report date.
        "data_time": data_time,
        "data_time_provenance": "source" if data_time else "unavailable",
        "data_time_note": (
            None
            if data_time
            else "本地快照未返回可用报告期；financial_fetched_at 仅表示同步时间。"
        ),
        "is_stale": None,
        "freshness_unknown": not bool(data_time),
        "errors": errors if success else [*errors, "未找到本地财务快照"],
        "warnings": [],
    }


def read_annual_financial_snapshot_eastmoney(symbols: str, fiscal_year: int) -> dict[str, Any]:
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
        "data_time_note": (
            None if data_time else "东财年度快照未返回可用报告期。"
        ),
        "is_stale": None,
        "freshness_unknown": not bool(data_time),
        "errors": errors if success else [*errors, f"未找到 {period} 年度财务快照"],
        "warnings": [],
    }


TOOLS = (
    ToolSpec(
        name="read_local_financial_snapshot",
        description=(
            "从本地已同步的 stock_meta 财务表读取至多 24 只已确认 A 股的原始财务快照。"
            "返回报告期、资产负债率、TTM 营收与 TTM 扣非净利润；不联网、不切换数据源、不评分。"
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
    "_PERIOD_MEMORY_CACHE",
    "_load_period_snapshot",
    "read_annual_financial_snapshot_eastmoney",
    "read_local_financial_snapshot",
]
