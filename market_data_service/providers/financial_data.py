# -*- coding: utf-8 -*-
"""Fast, source-correct financial statements for Stock Agent tools.

Eastmoney exposes report-period and single-quarter views separately.  Flow
statements here deliberately use ``reportType=2`` (single quarter), while the
balance sheet remains a period-end stock.  This prevents a common but severe
error: mixing half-year/year-to-date values with individual quarter values.
"""

from __future__ import annotations

from contextvars import copy_context
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Any

import httpx

from market_data_service.providers.common import (
    bare_local_symbol,
    bare_symbol,
    cached_call,
    exchange_prefix,
)

_BASE = "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)

_BS_FIELDS = {
    "total_assets": ("TOTAL_ASSETS",),
    "total_liabilities": ("TOTAL_LIABILITIES",),
    "total_equity": ("TOTAL_EQUITY",),
    "parent_equity": ("TOTAL_PARENT_EQUITY",),
    "monetary_funds": ("MONETARYFUNDS",),
    "accounts_receivable": ("ACCOUNTS_RECE",),
    "inventory": ("INVENTORY",),
    "contract_liabilities": ("CONTRACT_LIAB",),
    "fixed_assets": ("FIXED_ASSET",),
    "short_term_borrowings": ("SHORT_LOAN",),
    "long_term_borrowings": ("LONG_LOAN",),
    "accounts_payable": ("ACCOUNTS_PAYABLE",),
    "noncurrent_liabilities_due_within_one_year": ("NONCURRENT_LIAB_1YEAR",),
    "lease_liabilities": ("LEASE_LIAB",),
    "total_current_assets": ("TOTAL_CURRENT_ASSETS",),
    "total_current_liabilities": ("TOTAL_CURRENT_LIAB",),
    # Financial-institution fields; kept distinct instead of pretending they
    # are industrial-company cash, receivables or borrowings.
    "cash_and_central_bank_deposits": ("CASH_DEPOSIT_PBC",),
    "interbank_deposits_asset": ("DEPOSIT_INTERBANK",),
    "loans_and_advances": ("LOAN_ADVANCE",),
    "customer_deposits": ("ACCEPT_DEPOSIT",),
    "central_bank_borrowings": ("LOAN_PBC",),
    "interbank_deposits_liability": ("IOFI_DEPOSIT",),
}

_IS_FIELDS = {
    "revenue": ("TOTAL_OPERATE_INCOME", "OPERATE_INCOME"),
    "revenue_yoy": ("TOTAL_OPERATE_INCOME_YOY", "OPERATE_INCOME_YOY"),
    "total_cost": ("TOTAL_OPERATE_COST",),
    "operating_cost": ("OPERATE_COST",),
    "operating_profit": ("OPERATE_PROFIT",),
    "total_profit": ("TOTAL_PROFIT",),
    "net_profit": ("NETPROFIT",),
    "net_profit_yoy": ("NETPROFIT_YOY",),
    "parent_net_profit": ("PARENT_NETPROFIT",),
    "parent_net_profit_yoy": ("PARENT_NETPROFIT_YOY",),
    "deducted_net_profit": ("DEDUCT_PARENT_NETPROFIT",),
    "deducted_net_profit_yoy": ("DEDUCT_PARENT_NETPROFIT_YOY",),
    "basic_eps": ("BASIC_EPS",),
    "diluted_eps": ("DILUTED_EPS",),
    "selling_expense": ("SALE_EXPENSE",),
    "administrative_expense": ("MANAGE_EXPENSE",),
    "research_expense": ("RESEARCH_EXPENSE",),
    "finance_expense": ("FINANCE_EXPENSE",),
    "investment_income": ("INVEST_INCOME",),
    "tax_and_surcharges": ("OPERATE_TAX_ADD",),
    "income_tax": ("INCOME_TAX",),
    "asset_impairment_loss": ("ASSET_IMPAIRMENT_LOSS",),
    "net_interest_income": ("INTEREST_NI",),
    "interest_income": ("INTEREST_INCOME",),
    "interest_expense": ("INTEREST_EXPENSE",),
    "net_fee_and_commission_income": ("FEE_COMMISSION_NI",),
}

_CF_FIELDS = {
    "operating_cash_flow": ("NETCASH_OPERATE",),
    "investing_cash_flow": ("NETCASH_INVEST",),
    "financing_cash_flow": ("NETCASH_FINANCE",),
    "capital_expenditure_cash_paid": ("CONSTRUCT_LONG_ASSET",),
}


def _number(value: Any) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _first_number(row: dict[str, Any], candidates: tuple[str, ...]) -> float | None:
    for key in candidates:
        value = _number(row.get(key))
        if value is not None:
            return value
    return None


def _date_text(value: Any) -> str | None:
    text = str(value or "")[:10]
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return None


def _quarter_name(report_date: str | None) -> str | None:
    if not report_date:
        return None
    parsed = datetime.fromisoformat(report_date).date()
    return f"{parsed.year}Q{(parsed.month + 2) // 3}"


def _company_type(prefixed: str) -> str:
    response = httpx.get(
        f"{_BASE}/Index",
        params={"type": "web", "code": prefixed.lower()},
        headers={"User-Agent": _UA},
        timeout=12,
    )
    response.raise_for_status()
    match = re.search(r'id=["\']hidctype["\'][^>]*value=["\']([^"\']+)', response.text)
    if not match:
        match = re.search(
            r'value=["\']([^"\']+)["\'][^>]*id=["\']hidctype["\']', response.text
        )
    if not match:
        raise RuntimeError("无法识别东方财富财报公司类型")
    return match.group(1)


def _request_json(path: str, params: dict[str, Any]) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = httpx.get(
                f"{_BASE}/{path}",
                params=params,
                headers={"User-Agent": _UA, "Referer": f"{_BASE}/Index"},
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("财报接口返回格式异常")
            return payload
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                import time

                time.sleep(0.35)
    raise RuntimeError(f"东方财富财报接口失败: {last_error}")


def _fetch_section(
    prefixed: str, company_type: str, section: str, periods: int
) -> list[dict[str, Any]]:
    config = {
        "balance_sheet": ("zcfzb", "0", "1"),
        "income_statement": ("lrb", "2", "2"),
        "cashflow": ("xjllb", "2", "2"),
    }[section]
    stem, date_type, report_type = config
    date_payload = _request_json(
        f"{stem}DateAjaxNew",
        {"companyType": company_type, "reportDateType": date_type, "code": prefixed},
    )
    date_values = []
    for row in date_payload.get("data") or []:
        parsed = _date_text(row.get("REPORT_DATE") if isinstance(row, dict) else None)
        if parsed and parsed not in date_values:
            date_values.append(parsed)
    date_values = sorted(date_values, reverse=True)[:periods]
    if not date_values:
        raise RuntimeError(f"{section} 没有报告期")

    rows: list[dict[str, Any]] = []
    for offset in range(0, len(date_values), 5):
        batch = date_values[offset : offset + 5]
        payload = _request_json(
            f"{stem}AjaxNew",
            {
                "companyType": company_type,
                "reportDateType": "0",
                "reportType": report_type,
                "dates": ",".join(batch),
                "code": prefixed,
            },
        )
        rows.extend(row for row in (payload.get("data") or []) if isinstance(row, dict))
    return rows


def _base_record(row: dict[str, Any], basis: str) -> dict[str, Any]:
    report_date = _date_text(row.get("REPORT_DATE"))
    return {
        "report_date": report_date,
        "report_period": _quarter_name(report_date),
        "notice_date": _date_text(row.get("NOTICE_DATE")),
        "update_date": _date_text(row.get("UPDATE_DATE")),
        "currency": str(row.get("CURRENCY") or "CNY"),
        "basis": basis,
    }


def _normalize_balance(row: dict[str, Any]) -> dict[str, Any]:
    item = _base_record(row, "period_end")
    item.update(
        {
            field: _first_number(row, candidates)
            for field, candidates in _BS_FIELDS.items()
        }
    )
    assets = item.get("total_assets")
    liabilities = item.get("total_liabilities")
    equity = item.get("total_equity")
    current_assets = item.get("total_current_assets")
    current_liabilities = item.get("total_current_liabilities")
    inventory = item.get("inventory")
    # Zero is valid financial data, not a missing-value marker.  Only reject a
    # ratio when its denominator is absent or zero; keep zero numerators as 0.
    item["debt_ratio"] = (
        round(liabilities / assets * 100, 4)
        if liabilities is not None and assets not in (None, 0)
        else None
    )
    item["equity_multiplier"] = (
        round(assets / equity, 4)
        if assets is not None and equity not in (None, 0)
        else None
    )
    item["current_ratio"] = (
        round(current_assets / current_liabilities, 4)
        if current_assets is not None and current_liabilities not in (None, 0)
        else None
    )
    item["quick_ratio"] = (
        round((current_assets - (inventory or 0)) / current_liabilities, 4)
        if current_assets is not None and current_liabilities not in (None, 0)
        else None
    )
    return item


def _normalize_income(row: dict[str, Any]) -> dict[str, Any]:
    item = _base_record(row, "single_quarter")
    item.update(
        {
            field: _first_number(row, candidates)
            for field, candidates in _IS_FIELDS.items()
        }
    )
    revenue = item.get("revenue")
    operating_cost = item.get("operating_cost")
    parent_profit = item.get("parent_net_profit") or item.get("net_profit")
    item["gross_profit"] = (
        revenue - operating_cost
        if revenue is not None and operating_cost is not None
        else None
    )
    item["gross_margin"] = (
        round((revenue - operating_cost) / revenue * 100, 4)
        if revenue and operating_cost is not None
        else None
    )
    item["net_margin"] = (
        round(parent_profit / revenue * 100, 4)
        if revenue and parent_profit is not None
        else None
    )
    return item


def _normalize_cashflow(row: dict[str, Any]) -> dict[str, Any]:
    item = _base_record(row, "single_quarter")
    item.update(
        {
            field: _first_number(row, candidates)
            for field, candidates in _CF_FIELDS.items()
        }
    )
    operating = item.get("operating_cash_flow")
    capex = item.get("capital_expenditure_cash_paid")
    item["free_cash_flow"] = (
        operating - capex if operating is not None and capex is not None else None
    )
    return item


def _expected_min_report_date(now: date) -> date:
    if now.month <= 4:
        return date(now.year - 1, 9, 30)
    if now.month <= 8:
        return date(now.year, 3, 31)
    if now.month <= 10:
        return date(now.year, 6, 30)
    return date(now.year, 9, 30)


def _bundle(symbol: str, periods: int) -> dict[str, Any]:
    code = bare_symbol(symbol)
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("symbol 必须能解析为 6 位股票代码")
    prefixed = exchange_prefix(code, upper=True)
    company_type = _company_type(prefixed)
    raw: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(
                copy_context().run,
                _fetch_section,
                prefixed,
                company_type,
                section,
                periods,
            ): section
            for section in ("balance_sheet", "income_statement", "cashflow")
        }
        for future in as_completed(futures):
            section = futures[future]
            try:
                raw[section] = future.result()
            except Exception as exc:
                raw[section] = []
                errors.append(f"{section}: {exc}")

    normalizers = {
        "balance_sheet": _normalize_balance,
        "income_statement": _normalize_income,
        "cashflow": _normalize_cashflow,
    }
    normalized: dict[str, list[dict[str, Any]]] = {}
    for section, normalizer in normalizers.items():
        items = [normalizer(row) for row in raw.get(section, [])]
        items = [item for item in items if item.get("report_date")]
        normalized[section] = sorted(items, key=lambda item: item["report_date"])[
            -periods:
        ]

    income_by_date = {
        item["report_date"]: item for item in normalized["income_statement"]
    }
    for item in normalized["cashflow"]:
        income = income_by_date.get(item["report_date"], {})
        net_profit = income.get("parent_net_profit") or income.get("net_profit")
        item["net_profit"] = net_profit
        operating = item.get("operating_cash_flow")
        item["cash_conversion_ratio"] = (
            round(operating / net_profit, 4)
            if operating is not None and net_profit
            else None
        )

    report_dates = [
        item["report_date"]
        for section in normalized.values()
        for item in section
        if item.get("report_date")
    ]
    latest_report = max(report_dates) if report_dates else None
    now = datetime.now().astimezone()
    stale = (
        datetime.fromisoformat(latest_report).date()
        < _expected_min_report_date(now.date())
        if latest_report
        else None
    )
    complete = all(normalized[section] for section in normalized)
    success = any(normalized[section] for section in normalized)
    return {
        "symbol": code,
        "requested_periods": periods,
        "periods": min((len(items) for items in normalized.values()), default=0),
        **normalized,
        "amount_unit": "元",
        "ratio_unit": "%",
        "currency": "CNY",
        "income_statement_basis": "single_quarter",
        "cashflow_basis": "single_quarter",
        "balance_sheet_basis": "period_end",
        "source": "东方财富财务分析公开接口",
        "source_url": f"{_BASE}/Index?type=web&code={prefixed.lower()}",
        "success": success,
        "partial": success and not complete,
        "errors": errors,
        "data_time": latest_report,
        "is_stale": stale,
        "fallback_used": False,
        "_cached": False,
        "_fetched_at": now.isoformat(),
    }


def _section_result(symbol: str, section: str, periods: int) -> dict[str, Any]:
    """Fetch exactly one statement for a statement-specific Agent tool."""
    if section not in {"balance_sheet", "income_statement", "cashflow"}:
        raise ValueError(f"未知财务报表类型: {section}")
    code = bare_symbol(symbol)
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("symbol 必须能解析为 6 位股票代码")
    prefixed = exchange_prefix(code, upper=True)
    company_type = _company_type(prefixed)
    normalizer = {
        "balance_sheet": _normalize_balance,
        "income_statement": _normalize_income,
        "cashflow": _normalize_cashflow,
    }[section]
    rows = _fetch_section(prefixed, company_type, section, periods)
    items = [normalizer(row) for row in rows]
    items = sorted(
        (item for item in items if item.get("report_date")),
        key=lambda item: item["report_date"],
    )[-periods:]
    latest_report = items[-1]["report_date"] if items else None
    now = datetime.now().astimezone()
    basis = {
        "balance_sheet": "period_end",
        "income_statement": "single_quarter",
        "cashflow": "single_quarter",
    }[section]
    return {
        "symbol": code,
        "requested_periods": periods,
        "periods": len(items),
        section: items,
        "amount_unit": "元",
        "ratio_unit": "%",
        "currency": "CNY",
        "basis": basis,
        "source": "东方财富财务分析公开接口",
        "source_url": f"{_BASE}/Index?type=web&code={prefixed.lower()}",
        "success": bool(items),
        "partial": False,
        "errors": [] if items else [f"{section} 没有可用报告期"],
        "data_time": latest_report,
        "is_stale": (
            datetime.fromisoformat(latest_report).date()
            < _expected_min_report_date(now.date())
            if latest_report
            else None
        ),
        "fallback_used": False,
        "_cached": False,
        "_fetched_at": now.isoformat(),
    }


def get_financial_bundle(
    symbol: str, periods: int = 6, *, use_cache: bool = True
) -> dict[str, Any]:
    periods = max(2, min(int(periods), 20))
    code = bare_symbol(symbol)
    if use_cache:
        value, cached = cached_call(
            f"financial_bundle:v3:{code}:{periods}",
            lambda: _bundle(code, periods),
            ttl_seconds=6 * 3600,
            attempts=2,
        )
        value = dict(value)
        value["_cached"] = cached
        return value
    return _bundle(code, periods)


def get_financial_section(
    symbol: str,
    section: str,
    periods: int = 4,
    *,
    use_cache: bool = True,
    local_identity: bool = False,
) -> dict[str, Any]:
    periods = max(2, min(int(periods), 20))
    code = bare_local_symbol(symbol) if local_identity else bare_symbol(symbol)
    if use_cache:
        value, cached = cached_call(
            f"financial_section:v1:{section}:{code}:{periods}",
            lambda: _section_result(code, section, periods),
            ttl_seconds=6 * 3600,
            attempts=2,
        )
        value = dict(value)
        value["_cached"] = cached
        return value
    return _section_result(code, section, periods)


def fetch_core_indicators(symbol: str, periods: int) -> list[dict[str, Any]]:
    """THS reported indicators; flow fields are explicitly single-quarter."""
    import akshare as ak

    frame = ak.stock_financial_abstract_new_ths(symbol=symbol, indicator="按报告期")
    if frame is None or frame.empty:
        raise RuntimeError("同花顺核心财务指标为空")
    metric_map = {
        "operating_income_total": "revenue",
        "parent_holder_net_profit": "parent_net_profit",
        "index_deduct_holder_net_profit": "deducted_net_profit",
        "basic_eps": "basic_eps",
        "calc_per_net_assets": "book_value_per_share",
        "sale_net_interest_ratio": "net_margin",
        "sale_gross_margin": "gross_margin",
        "index_weighted_avg_roe": "roe",
        "index_full_diluted_roe": "roe_diluted",
        "current_ratio": "current_ratio",
        "quick_ratio": "quick_ratio",
        "assets_debt_ratio": "debt_ratio",
        "calculate_operating_income_total_yoy_growth_ratio": "revenue_yoy",
        "calculate_parent_holder_net_profit_yoy_growth_ratio": "parent_net_profit_yoy",
        "deduct_net_profit_yoy_growth_ratio": "deducted_net_profit_yoy",
    }
    flow_fields = {"revenue", "parent_net_profit", "deducted_net_profit", "basic_eps"}
    rows: dict[str, dict[str, Any]] = {}
    for _, raw in frame.iterrows():
        field = metric_map.get(str(raw.get("metric_name") or "").strip())
        report_date = _date_text(raw.get("report_date"))
        if not field or not report_date:
            continue
        item = rows.setdefault(
            report_date,
            {"report_date": report_date, "report_period": _quarter_name(report_date)},
        )
        value = (
            _number(raw.get("single"))
            if field in flow_fields
            else _number(raw.get("value"))
        )
        if value is None:
            value = _number(raw.get("value"))
        item[field] = value
    return sorted(rows.values(), key=lambda item: item["report_date"])[-periods:]
