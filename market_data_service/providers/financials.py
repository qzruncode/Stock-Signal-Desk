"""``get_financials`` — core financial indicators with explicit period basis."""

from __future__ import annotations
from contextvars import copy_context
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any
from market_data_service.providers.common import (
    bare_local_symbol,
    bare_symbol,
    cached_call,
)
from market_data_service.providers.financial_data import (
    _expected_min_report_date,
    fetch_core_indicators,
    get_financial_bundle,
)


def _growth(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round((current - previous) / abs(previous) * 100, 4)


def _build(symbol: str, periods: int) -> dict[str, Any]:
    errors: list[str] = []
    core: list[dict[str, Any]] = []
    bundle: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        core_future = pool.submit(
            copy_context().run, fetch_core_indicators, symbol, periods
        )
        bundle_future = pool.submit(
            copy_context().run, get_financial_bundle, symbol, periods, use_cache=True
        )
        try:
            core = core_future.result()
        except Exception as exc:
            errors.append(f"同花顺核心指标: {exc}")
        try:
            bundle = bundle_future.result()
        except Exception as exc:
            errors.append(f"东方财富财报: {exc}")
    by_date = {
        item["report_date"]: dict(item) for item in core if item.get("report_date")
    }
    for section in ("income_statement", "balance_sheet", "cashflow"):
        for row in bundle.get(section) or []:
            report_date = row.get("report_date")
            if not report_date:
                continue
            item = by_date.setdefault(
                report_date,
                {"report_date": report_date, "report_period": row.get("report_period")},
            )
            if section == "income_statement":
                fields = (
                    "revenue",
                    "revenue_yoy",
                    "parent_net_profit",
                    "parent_net_profit_yoy",
                    "deducted_net_profit",
                    "deducted_net_profit_yoy",
                    "basic_eps",
                    "gross_margin",
                    "net_margin",
                    "research_expense",
                    "asset_impairment_loss",
                )
            elif section == "balance_sheet":
                fields = (
                    "total_assets",
                    "total_liabilities",
                    "parent_equity",
                    "monetary_funds",
                    "accounts_receivable",
                    "inventory",
                    "contract_liabilities",
                    "debt_ratio",
                    "current_ratio",
                    "quick_ratio",
                    "loans_and_advances",
                    "customer_deposits",
                )
            else:
                fields = (
                    "operating_cash_flow",
                    "capital_expenditure_cash_paid",
                    "free_cash_flow",
                    "cash_conversion_ratio",
                )
            for field in fields:
                if item.get(field) is None and row.get(field) is not None:
                    item[field] = row[field]
    items = sorted(by_date.values(), key=lambda item: item["report_date"])[-periods:]
    for index, item in enumerate(items):
        item["flow_basis"] = "single_quarter"
        item["balance_basis"] = "period_end"
        item["reported_ratio_basis"] = (
            "公司披露的截至该报告期指标，ROE等可能为年初至报告期累计口径"
        )
        item["revenue_qoq"] = (
            _growth(item.get("revenue"), items[index - 1].get("revenue"))
            if index
            else None
        )
    now = datetime.now().astimezone()
    success = bool(items)
    sources = []
    if core:
        sources.append("同花顺/AKShare核心指标")
    if bundle.get("success"):
        sources.append("东方财富单季度财报")
    errors.extend(bundle.get("errors") or [])
    return {
        "symbol": symbol,
        "requested_periods": periods,
        "periods": len(items),
        "items": items,
        "amount_unit": "元",
        "ratio_unit": "%",
        "per_share_unit": "元/股",
        "currency": "CNY",
        "flow_basis": "single_quarter",
        "balance_basis": "period_end",
        "source": " + ".join(sources) or "none",
        "sources": sources,
        "success": success,
        "partial": success and bool(errors),
        "errors": errors,
        "data_time": items[-1]["report_date"] if items else None,
        "is_stale": bundle.get("is_stale", not success),
        "fallback_used": bool(core) and (not bundle.get("success")),
        "_cached": False,
        "_fetched_at": now.isoformat(),
    }


def get_financials(
    symbol: str, periods: int = 6, *, use_cache: bool = True
) -> dict[str, Any]:
    code = bare_symbol(symbol)
    if not re.fullmatch("\\d{6}", code):
        raise ValueError("symbol 必须能解析为 6 位股票代码")
    periods = max(2, min(int(periods), 20))
    if not use_cache:
        return _build(code, periods)
    result, cached = cached_call(
        f"core_financials:v3:{code}:{periods}",
        lambda: _build(code, periods),
        ttl_seconds=6 * 3600,
        attempts=2,
    )
    result = dict(result)
    result["_cached"] = cached
    return result


def read_core_financial_indicators_ths(
    symbol: str, periods: int = 6, *, use_cache: bool = True
) -> dict[str, Any]:
    """Read the reported THS indicator table without merging other statements.

    ``get_financials`` deliberately remains available to old HTTP services as
    their convenience merger.  It is not model-callable: a planning model must
    choose this source or one of the three statement reads explicitly.
    """
    code = bare_local_symbol(symbol)
    if not re.fullmatch("\\d{6}", code):
        raise ValueError("symbol 必须能解析为 6 位股票代码")
    limit = max(2, min(int(periods), 20))
    items, cached = (
        cached_call(
            f"core-financial-indicators:ths:v1:{code}:{limit}",
            lambda: fetch_core_indicators(code, limit),
            ttl_seconds=6 * 3600,
            attempts=2,
        )
        if use_cache
        else (fetch_core_indicators(code, limit), False)
    )
    normalized = [
        dict(item)
        for item in items
        if isinstance(item, dict) and item.get("report_date")
    ]
    normalized.sort(key=lambda item: str(item["report_date"]))
    latest_report = str(normalized[-1]["report_date"]) if normalized else None
    now = datetime.now().astimezone()
    return {
        "symbol": code,
        "requested_periods": limit,
        "periods": len(normalized),
        "items": normalized,
        "amount_unit": "元",
        "ratio_unit": "%",
        "per_share_unit": "元/股",
        "source": "同花顺财务摘要/AKShare",
        "source_scope": "reported_core_financial_indicators",
        "success": bool(normalized),
        "partial": False,
        "errors": [] if normalized else ["同花顺核心财务指标没有可用报告期"],
        "warnings": [],
        "data_time": latest_report,
        "is_stale": datetime.fromisoformat(latest_report).date()
        < _expected_min_report_date(now.date())
        if latest_report
        else None,
        "freshness_unknown": latest_report is None,
        "fallback_used": False,
        "_cached": cached,
        "_fetched_at": now.isoformat(),
    }
