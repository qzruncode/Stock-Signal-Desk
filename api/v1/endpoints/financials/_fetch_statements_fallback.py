# -*- coding: utf-8 -*-
"""Financial statements: fallback sources and orchestrator."""
from __future__ import annotations

import logging
import sys
import time as _time
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

from ._helpers import (
    _normalize_symbol, _to_em_symbol, _safe_float, _safe_str, _safe_amount,
    _safe_pct, _pick_col, _parse_date,
)
from ._cache import _fins_cache_get, _fins_cache_put, FINS_STATEMENTS_CACHE_KEY
from ._fetch_statements import (
    _pick_quarters, _fetch_balance_sheet, _fetch_income_statement, _fetch_cashflow,
    _fetch_from_em, _fetch_from_ths_triple, _fetch_from_sina_full,
    _normalize_balance_debt_fields,
)

# Late-bound reference for test monkey-patching compatibility
_pkg = sys.modules[__package__]

_THS_NEW_BALANCE_METRICS = {
    'report_date': 'report_date',
    'total_assets': 'total_assets',
    'total_debt': 'total_liabilities',
    'equity_total': 'total_equity',
    'monetary_fund': 'monetary_funds',
    'accounts_receivable': 'accounts_receivable',
    'inventory': 'inventory',
    'contract_liability': 'contract_liabilities',
    'fixed_assets_total': 'fixed_asset',
    'short_term_loans': 'short_loan',
    'long_term_loan': 'long_loan',
    'accounts_payable': 'accounts_payable',
    'year_non_current_debt': 'noncurrent_liab_1year',
    'lease_debt': 'lease_liab',
    'current_nets_total': 'total_current_assets',
    'current_total_debt': 'total_current_liabilities',
}


def _fetch_from_ths_new_balance(symbol: str, periods: int) -> dict:
    """Fetch balance sheet details from 同花顺新版长表.

    This source exposes some line items (e.g. year_non_current_debt,
    lease_debt) that are easy to miss in the wide-table endpoint.
    """
    import time as _time
    import akshare as ak

    t0 = _time.time()
    result: dict = {
        'balance_sheet': [],
        'income_statement': [],
        'cashflow': [],
    }

    df = ak.stock_financial_debt_new_ths(symbol=symbol, indicator='按报告期')
    if df is None or df.empty:
        raise ValueError("同花顺新版资产负债长表返回空数据")

    if 'report_date' not in df.columns or 'metric_name' not in df.columns or 'value' not in df.columns:
        raise ValueError("同花顺新版资产负债长表字段不完整")

    rows_by_date: dict[str, dict] = {}
    for _, row in df.iterrows():
        rd = _safe_str(row.get('report_date'))[:10]
        metric = _safe_str(row.get('metric_name'))
        dst = _THS_NEW_BALANCE_METRICS.get(metric)
        if not rd or not dst:
            continue
        item = rows_by_date.setdefault(rd, {'report_date': rd})
        if dst == 'report_date':
            continue
        value = _safe_float(row.get('value'))
        if value is not None:
            item[dst] = value

    items = sorted(rows_by_date.values(), key=lambda item: item.get('report_date') or '')[-periods:]
    for item in items:
        rd = item.get('report_date')
        if rd:
            item['report_date_name'] = rd[:4] + 'Q' + str((int(rd[5:7]) + 2) // 3)
        ta = item.get('total_assets')
        tl = item.get('total_liabilities')
        te = item.get('total_equity')
        if ta and ta != 0:
            if tl is not None:
                item['debt_ratio'] = round(tl / ta * 100, 2)
            if te is not None and te != 0:
                item['equity_multiplier'] = round(ta / te, 2)
        result['balance_sheet'].append(item)

    if not result['balance_sheet']:
        raise ValueError("同花顺新版资产负债长表无可映射字段")

    result['source'] = '同花顺新版资产负债'
    logger.info(f"[FinancialStatements] THS new balance {_time.time() - t0:.1f}s for {symbol}: "
                f"BS={len(result['balance_sheet'])}")
    return result


def _fetch_from_ths_abstract(symbol: str, periods: int) -> dict:
    """Last resort: fetch from 同花顺 abstract indicators.

    Only provides income statement data (revenue, profit, margins, ROE, EPS).
    Balance sheet only has debt_ratio. Cash flow is empty.
    """
    import time as _time

    t0 = _time.time()
    result: dict = {
        'balance_sheet': [],
        'income_statement': [],
        'cashflow': [],
    }

    items = _pkg._fetch_from_ths(symbol, periods)
    if not items:
        raise ValueError("同花顺聚合指标返回空数据")

    for item in items:
        rd = item.get('report_date', '')
        rdn = (rd[:4] + 'Q' + str((int(rd[5:7]) + 2) // 3)) if len(rd) >= 10 else ''

        result['balance_sheet'].append({
            'report_date': rd, 'report_date_name': rdn,
            'total_assets': None, 'total_liabilities': None, 'total_equity': None,
            'parent_equity': None, 'monetary_funds': None, 'accounts_receivable': None,
            'inventory': None, 'contract_liabilities': None, 'fixed_asset': None, 'short_loan': None,
            'long_loan': None, 'accounts_payable': None,
            'noncurrent_liab_1year': None, 'lease_liab': None,
            'total_current_assets': None, 'total_current_liabilities': None,
            'debt_ratio': item.get('debt_ratio'), 'equity_multiplier': None,
        })
        result['income_statement'].append({
            'report_date': rd, 'report_date_name': rdn,
            'revenue': item.get('revenue'), 'total_cost': None,
            'operate_cost': None, 'operate_profit': None, 'total_profit': None,
            'net_profit': item.get('net_profit'),
            'net_profit_yoy': item.get('net_profit_yoy'),
            'parent_net_profit': None,
            'parent_net_profit_yoy': item.get('net_profit_yoy'),
            'deducted_net_profit': item.get('deducted_profit'),
            'deducted_net_profit_yoy': item.get('deducted_profit_yoy'),
            'basic_eps': item.get('eps'), 'diluted_eps': None,
            'sale_expense': None, 'manage_expense': None, 'research_expense': None,
            'finance_expense': None, 'invest_income': None,
            'operate_tax_add': None, 'income_tax': None,
            'asset_impairment_loss': None,
            'gross_profit': None, 'gross_margin': item.get('gross_margin'),
            'net_margin': item.get('net_margin'),
            'revenue_yoy': item.get('revenue_yoy'),
        })
        result['cashflow'].append({
            'report_date': rd, 'report_date_name': rdn,
            'operating_cf': None, 'investing_cf': None, 'financing_cf': None,
            'capex': None, 'net_profit': item.get('net_profit'),
            'free_cashflow': None, 'cf_quality': None,
        })

    result['source'] = '同花顺(聚合)'
    logger.info(f"[FinancialStatements] THS abstract {_time.time() - t0:.1f}s for {symbol}: "
                f"{len(result['income_statement'])} periods")
    return result


def _backfill_cf_net_profit(result: dict) -> None:
    """Backfill cashflow.net_profit from income_statement where null.

    东方财富的现金流表对一季报/三季报不返回 NETPROFIT，
    用利润表的 net_profit 回补，使经营CF/净利润可计算。
    """
    is_lookup: dict[str, float | None] = {}
    for item in result.get('income_statement', []):
        rd = item.get('report_date')
        if rd:
            is_lookup[rd] = item.get('net_profit')

    for cf_item in result.get('cashflow', []):
        if cf_item.get('net_profit') is None:
            rd = cf_item.get('report_date')
            if rd and rd in is_lookup:
                cf_item['net_profit'] = is_lookup[rd]

    # Re-derive cf_quality after backfill
    for cf_item in result.get('cashflow', []):
        ocf = cf_item.get('operating_cf')
        np_val = cf_item.get('net_profit')
        if ocf and np_val and np_val != 0:
            cf_item['cf_quality'] = round(ocf / np_val, 2)


def _merge_statement_items(base_items: list[dict], supplement_items: list[dict]) -> int:
    """Fill missing fields in base rows by matching report_date from another source."""
    supplement_by_date = {
        item.get('report_date'): item
        for item in supplement_items
        if item.get('report_date')
    }
    filled = 0
    for base in base_items:
        rd = base.get('report_date')
        if not rd or rd not in supplement_by_date:
            continue
        supplement = supplement_by_date[rd]
        for key, value in supplement.items():
            if key == 'report_date' or value is None:
                continue
            if base.get(key) is None:
                base[key] = value
                filled += 1
    return filled


def _merge_financial_statement_sources(base: dict, supplement: dict) -> int:
    filled = 0
    for section in ('balance_sheet', 'income_statement', 'cashflow'):
        filled += _merge_statement_items(base.get(section, []), supplement.get(section, []))
    return filled


def _finalize_balance_sheet_items(result: dict) -> None:
    for item in result.get('balance_sheet', []):
        _normalize_balance_debt_fields(item)


# --- Orchestrator ---


def _fetch_financial_statements(symbol: str, periods: int = 12) -> dict:
    """Fetch all three financial statements with multi-source fallback.

    Fallback chain:
      1. 东方财富 stock_*_by_report_em() — 三张完整报表，单季度
      2. 同花顺 stock_financial_{debt,benefit,cash}_ths() — 三张完整报表，单季度
      3. 新浪财经 stock_financial_report_sina() — 三张完整报表，单季度
      4. 同花顺 stock_financial_debt_new_ths() — 新版长表，补充负债细项
      5. 同花顺 stock_financial_abstract_ths() — 聚合指标，仅利润表有数据
    """
    import time as _time

    t0 = _time.time()
    code = _normalize_symbol(symbol)
    result: dict = {
        'symbol': code,
        'periods': periods,
        'balance_sheet': [],
        'income_statement': [],
        'cashflow': [],
        '_fetched_at': datetime.now().isoformat(),
        '_cached': False,
    }
    errors: list[str] = []

    sources = [
        ('东方财富', _fetch_from_em),
        ('同花顺', _fetch_from_ths_triple),
        ('新浪财经', _fetch_from_sina_full),
        ('同花顺新版资产负债', _fetch_from_ths_new_balance),
        ('同花顺(聚合)', _fetch_from_ths_abstract),
    ]

    used_sources: list[str] = []
    base_loaded = False

    for src_name, src_fn in sources:
        try:
            src_data = src_fn(code, periods)
            if not base_loaded:
                result.update(src_data)
                used_sources.append(src_name)
                base_loaded = True
            else:
                filled = _merge_financial_statement_sources(result, src_data)
                if filled > 0:
                    used_sources.append(f"{src_name}字段补充")
        except Exception as e:
            errors.append(f"{src_name}: {e}")
            logger.warning(f"[FinancialStatements] {src_name} failed for {symbol}: {e}")

    if not base_loaded:
        result['_errors'] = errors
        logger.warning(f"[FinancialStatements] all sources failed for {symbol}: {'; '.join(errors)}")
        return result

    _backfill_cf_net_profit(result)
    _finalize_balance_sheet_items(result)
    result['source'] = ' / '.join(used_sources)
    if used_sources and used_sources[0] != '东方财富':
        result['_fallback'] = True
    if errors:
        result['_errors'] = errors
    logger.info(f"[FinancialStatements] total {_time.time() - t0:.1f}s for {symbol} (source: {result['source']})")
    return result


def _safe_growth_pct(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return round((current - previous) / abs(previous) * 100, 2)


def _statements_payload_supports_summary_enrichment(statements: dict) -> bool:
    balance_items = statements.get('balance_sheet') or []
    income_items = statements.get('income_statement') or []

    balance_has_contract = any('contract_liabilities' in item for item in balance_items if isinstance(item, dict))
    income_has_impairment = any('asset_impairment_loss' in item for item in income_items if isinstance(item, dict))
    income_has_parent_yoy = any('parent_net_profit_yoy' in item for item in income_items if isinstance(item, dict))
    income_has_deducted_yoy = any('deducted_net_profit_yoy' in item for item in income_items if isinstance(item, dict))
    return balance_has_contract and income_has_impairment and income_has_parent_yoy and income_has_deducted_yoy


def _enrich_financial_items_with_statements(symbol: str, items: list[dict]) -> None:
    """Backfill summary rows with statement-only fields needed by the agent."""
    if not items:
        return

    periods = len(items)
    statements = _fins_cache_get(symbol, periods)
    if statements and not _statements_payload_supports_summary_enrichment(statements):
        statements = None
    if not statements:
        statements = _pkg._fetch_financial_statements(symbol, periods)

    income_by_date = {
        item.get('report_date'): item
        for item in statements.get('income_statement', [])
        if item.get('report_date')
    }
    cashflow_by_date = {
        item.get('report_date'): item
        for item in statements.get('cashflow', [])
        if item.get('report_date')
    }
    balance_by_date = {
        item.get('report_date'): item
        for item in statements.get('balance_sheet', [])
        if item.get('report_date')
    }

    for item in items:
        report_date = item.get('report_date')
        income = income_by_date.get(report_date, {})
        cashflow = cashflow_by_date.get(report_date, {})
        balance = balance_by_date.get(report_date, {})

        if item.get('parent_net_profit') is None:
            item['parent_net_profit'] = income.get('parent_net_profit')
        if item.get('parent_net_profit_yoy') is None:
            item['parent_net_profit_yoy'] = (
                income.get('parent_net_profit_yoy')
                if income.get('parent_net_profit_yoy') is not None
                else item.get('net_profit_yoy')
            )
        if item.get('deducted_net_profit') is None:
            item['deducted_net_profit'] = (
                income.get('deducted_net_profit')
                if income.get('deducted_net_profit') is not None
                else item.get('deducted_profit')
            )
        if item.get('deducted_net_profit_yoy') is None:
            item['deducted_net_profit_yoy'] = (
                income.get('deducted_net_profit_yoy')
                if income.get('deducted_net_profit_yoy') is not None
                else item.get('deducted_profit_yoy')
            )
        if item.get('operating_cash_flow') is None:
            item['operating_cash_flow'] = cashflow.get('operating_cf')
        if item.get('accounts_receivable') is None:
            item['accounts_receivable'] = balance.get('accounts_receivable')
        if item.get('inventory') is None:
            item['inventory'] = balance.get('inventory')
        if item.get('contract_liabilities') is None:
            item['contract_liabilities'] = balance.get('contract_liabilities')
        if item.get('asset_impairment_loss') is None:
            item['asset_impairment_loss'] = income.get('asset_impairment_loss')

    for idx, item in enumerate(items):
        if idx == 0:
            item.setdefault('revenue_qoq', None)
            continue
        item['revenue_qoq'] = _safe_growth_pct(
            item.get('revenue'),
            items[idx - 1].get('revenue'),
        )


# --- Endpoint ---

_fins_lock = None

