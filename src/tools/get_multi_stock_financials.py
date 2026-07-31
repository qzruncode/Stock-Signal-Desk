# -*- coding: utf-8 -*-
"""Bounded, period-aware financial snapshots for A-share collections."""

from __future__ import annotations

import json
import math
import re
import threading
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.tools.base import ToolSpec, TypedToolResult
from src.tools.symbols import resolve_securities_csv


DESCRIPTION = (
    "从内部已同步财务库或指定年度财务快照读取多只 A 股的同口径财务指标，最多 24 只。"
    "支持资产负债率、营业收入、归母净利润和扣非净利润，可按最新报告期、TTM、去年年报或明确年度分批查询；"
    "不拉取实时行情、K线或技术指标。"
)

_METRIC_FIELDS = {
    "debt_ratio": {
        "label": "资产负债率",
        "unit": "percent",
        "local_field": "debt_ratio",
        "annual_field": "ZCFZL",
    },
    "revenue": {
        "label": "营业收入",
        "unit": "cny",
        "local_field": "revenue_ttm",
        "annual_field": "TOTALOPERATEREVE",
    },
    "net_profit": {
        "label": "归母净利润",
        "unit": "cny",
        "local_field": None,
        "annual_field": "PARENTNETPROFIT",
    },
    "deducted_net_profit": {
        "label": "扣非净利润",
        "unit": "cny",
        "local_field": "deducted_net_profit_ttm",
        "annual_field": "KCFJCXSYJLR",
    },
}
_PERIOD_CACHE_TTL = timedelta(hours=24)
_PERIOD_CACHE_VERSION = "v2"
_PERIOD_CACHE_LOCK = threading.RLock()
_PERIOD_MEMORY_CACHE: dict[str, tuple[datetime, dict[str, dict[str, Any]]]] = {}


class GetMultiStockFinancialsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    symbols: str = Field(min_length=1)
    metric: Literal[
        "debt_ratio",
        "revenue",
        "net_profit",
        "deducted_net_profit",
    ]
    period_basis: Literal[
        "latest_report",
        "ttm",
        "previous_fiscal_year",
        "fiscal_year",
    ]
    fiscal_year: int | None = Field(default=None, ge=1990, le=2100)

    @model_validator(mode="after")
    def _validate_period(self) -> "GetMultiStockFinancialsArgs":
        _validate_request(self.metric, self.period_basis, self.fiscal_year)
        return self


class GetMultiStockFinancialsResult(TypedToolResult):
    items: list[dict[str, Any]]
    resolved_entities: list[dict[str, Any]]
    unresolved_entities: list[str]
    requested_count: int = Field(ge=0)
    covered_count: int = Field(ge=0)
    missing_financial_symbols: list[str]
    requested_metric: str | None = None
    requested_period_basis: str | None = None
    requested_fiscal_year: int | None = None
    source: str | None = None
    field_basis: dict[str, str] = Field(default_factory=dict)


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _validate_request(metric: str, period_basis: str, fiscal_year: int | None) -> None:
    if metric not in _METRIC_FIELDS:
        raise ValueError("unsupported financial metric")
    if period_basis not in {
        "latest_report",
        "ttm",
        "previous_fiscal_year",
        "fiscal_year",
    }:
        raise ValueError("unsupported financial period basis")
    if metric == "debt_ratio" and period_basis == "ttm":
        raise ValueError("debt_ratio does not support ttm period basis")
    if metric != "debt_ratio" and period_basis == "latest_report":
        raise ValueError("currency metrics require ttm or a fiscal-year period")
    if metric == "net_profit" and period_basis == "ttm":
        raise ValueError("net_profit currently requires a fiscal-year period")
    if period_basis == "fiscal_year":
        if fiscal_year is None or not 1990 <= int(fiscal_year) <= 2100:
            raise ValueError("fiscal_year must be provided for fiscal_year period basis")
    elif fiscal_year is not None:
        raise ValueError("fiscal_year is only allowed with fiscal_year period basis")


def _annual_period(period_basis: str, fiscal_year: int | None) -> str:
    year = date.today().year - 1 if period_basis == "previous_fiscal_year" else fiscal_year
    if year is None:
        raise ValueError("annual period requires a fiscal year")
    return f"{int(year):04d}-12-31"


def _load_period_snapshot(period: str) -> tuple[dict[str, dict[str, Any]], datetime]:
    """Load one all-market report-period snapshot with process and DB caching."""
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

        from src.services.stock_screening.atr_volatility_screener import (
            fetch_financial_period_snapshot,
        )

        rows = fetch_financial_period_snapshot(period)
        fetched_at = datetime.now()
        db.save_tool_cache(
            cache_key,
            json.dumps({"period": period, "rows": rows}, ensure_ascii=False).encode("utf-8"),
        )
        _PERIOD_MEMORY_CACHE[period] = (fetched_at, rows)
        return rows, fetched_at


def get_multi_stock_financials(
    symbols: str,
    metric: str = "debt_ratio",
    period_basis: str = "latest_report",
    fiscal_year: int | None = None,
) -> dict[str, Any]:
    _validate_request(metric, period_basis, fiscal_year)
    resolved, unresolved = resolve_securities_csv(symbols)
    resolved = resolved[:24]
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

    metric_definition = _METRIC_FIELDS[metric]
    annual = period_basis in {"previous_fiscal_year", "fiscal_year"}
    by_code: dict[str, Any]
    snapshot_time: datetime | None = None
    report_period: str | None = None
    if annual:
        report_period = _annual_period(period_basis, fiscal_year)
        by_code, snapshot_time = _load_period_snapshot(report_period)
    else:
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
        if annual:
            value = _safe_float(row.get(metric_definition["annual_field"]) if isinstance(row, dict) else None)
            row_report_date = (
                str(row.get("REPORT_DATE") or report_period)[:10] if isinstance(row, dict) else report_period
            )
            fetched_at = snapshot_time.isoformat() if snapshot_time is not None else None
            compatibility_fields: dict[str, Any] = {}
        else:
            value = _safe_float(getattr(row, str(metric_definition["local_field"]), None) if row is not None else None)
            row_report_date = getattr(row, "report_date", None) if row is not None else None
            fetched = getattr(row, "financial_fetched_at", None) if row is not None else None
            fetched_at = fetched.isoformat() if isinstance(fetched, datetime) else None
            compatibility_fields = {
                "debt_ratio_pct": getattr(row, "debt_ratio", None) if row is not None else None,
                "revenue_ttm": getattr(row, "revenue_ttm", None) if row is not None else None,
                "deducted_net_profit_ttm": (getattr(row, "deducted_net_profit_ttm", None) if row is not None else None),
            }
        if fetched_at:
            fetched_times.append(fetched_at)
        if value is None:
            missing_financial_symbols.append(code)
        items.append(
            {
                "symbol": code,
                "name": entity["name"],
                "input": entity["input"],
                "metric": metric,
                "period_basis": period_basis,
                "financial_value": value,
                "value_unit": metric_definition["unit"],
                "report_date": row_report_date,
                "financial_fetched_at": fetched_at,
                **compatibility_fields,
            }
        )

    errors: list[str] = []
    if unresolved:
        errors.append(f"无法解析: {', '.join(unresolved)}")
    if missing_financial_symbols:
        errors.append(f"缺少{metric_definition['label']}数据: " + ", ".join(missing_financial_symbols))
    covered_count = len(items) - len(missing_financial_symbols)
    success = covered_count > 0
    source = (
        f"内部财务数据源 {report_period} 年度快照（按证券代码合并最新修订值）"
        if annual
        else "stock_meta 本地已同步最新财务快照"
    )
    return {
        "success": success,
        "partial": success and bool(errors),
        "items": items,
        "resolved_entities": resolved,
        "unresolved_entities": unresolved,
        "requested_count": len(resolved),
        "covered_count": covered_count,
        "missing_financial_symbols": missing_financial_symbols,
        "requested_metric": metric,
        "requested_period_basis": period_basis,
        "requested_fiscal_year": int(report_period[:4]) if annual and report_period else None,
        "data_time": max(fetched_times) if fetched_times else None,
        "is_stale": False if fetched_times else None,
        "source": source,
        "field_basis": {
            "financial_value": (
                f"{metric_definition['label']}，单位"
                + ("%" if metric_definition["unit"] == "percent" else "元")
                + f"，报告期口径 {period_basis}"
            ),
        },
        "errors": errors,
        "warnings": [],
    }


TOOL = ToolSpec(
    name="get_multi_stock_financials",
    description=DESCRIPTION,
    parameters=None,
    executor=get_multi_stock_financials,
    category="financials",
    args_model=GetMultiStockFinancialsArgs,
    result_model=GetMultiStockFinancialsResult,
)


__all__ = [
    "GetMultiStockFinancialsArgs",
    "GetMultiStockFinancialsResult",
    "TOOL",
    "get_multi_stock_financials",
]
