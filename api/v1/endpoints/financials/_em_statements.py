# -*- coding: utf-8 -*-
"""东方财富 financial statements — balance sheet, income statement, cash flow."""

from __future__ import annotations

import logging
import time as _time

from api.v1.endpoints.financials._normalize import (
    BS_COLUMNS, BS_STRING_FIELDS,
    IS_COLUMNS, IS_STRING_FIELDS,
    CF_COLUMNS, CF_STRING_FIELDS,
    _normalize_balance_debt_fields,
)
from api.v1.endpoints.financials._symbol import _to_em_symbol, _safe_float
from api.v1.endpoints.financials._fetch_statements import _pick_quarters

logger = logging.getLogger(__name__)


def _fetch_balance_sheet(symbol: str, periods: int) -> list[dict]:
    import akshare as ak

    em_symbol = _to_em_symbol(symbol)
    df = ak.stock_balance_sheet_by_report_em(symbol=em_symbol)
    if df is None or df.empty:
        raise ValueError("东方财富资产负债表返回空数据")

    df = _pick_quarters(df, periods)
    items = []
    for _, row in df.iterrows():
        item = {}
        for src_col, dst_col in BS_COLUMNS.items():
            if src_col not in row.index:
                continue
            val = row[src_col]
            if dst_col in BS_STRING_FIELDS:
                item[dst_col] = str(val).strip() if val is not None and str(val) != 'nan' else None
            else:
                item[dst_col] = _safe_float(val)
        ta = item.get('total_assets')
        tl = item.get('total_liabilities')
        te = item.get('total_equity')
        if ta and ta != 0:
            if tl is not None:
                item['debt_ratio'] = round(tl / ta * 100, 2)
            if te is not None and te != 0:
                item['equity_multiplier'] = round(ta / te, 2)
        if item.get('report_date'):
            item['report_date'] = str(item['report_date'])[:10]
        items.append(item)
    return items


def _fetch_income_statement(symbol: str, periods: int) -> list[dict]:
    import akshare as ak

    em_symbol = _to_em_symbol(symbol)
    df = ak.stock_profit_sheet_by_report_em(symbol=em_symbol)
    if df is None or df.empty:
        raise ValueError("东方财富利润表返回空数据")

    df = _pick_quarters(df, periods)
    items = []
    for _, row in df.iterrows():
        item = {}
        for src_col, dst_col in IS_COLUMNS.items():
            if src_col not in row.index:
                continue
            val = row[src_col]
            if dst_col in IS_STRING_FIELDS:
                item[dst_col] = str(val).strip() if val is not None and str(val) != 'nan' else None
            else:
                item[dst_col] = _safe_float(val)
        rev = item.get('revenue')
        oc = item.get('operate_cost')
        if rev and oc and rev != 0:
            item['gross_profit'] = rev - oc
            item['gross_margin'] = round((rev - oc) / rev * 100, 2)
        np_val = item.get('net_profit')
        if rev and np_val and rev != 0:
            item['net_margin'] = round(np_val / rev * 100, 2)
        if item.get('report_date'):
            item['report_date'] = str(item['report_date'])[:10]
        items.append(item)
    return items


def _fetch_cashflow(symbol: str, periods: int) -> list[dict]:
    import akshare as ak

    em_symbol = _to_em_symbol(symbol)
    df = ak.stock_cash_flow_sheet_by_report_em(symbol=em_symbol)
    if df is None or df.empty:
        raise ValueError("东方财富现金流量表返回空数据")

    df = _pick_quarters(df, periods)
    items = []
    for _, row in df.iterrows():
        item = {}
        for src_col, dst_col in CF_COLUMNS.items():
            if src_col not in row.index:
                continue
            val = row[src_col]
            if dst_col in CF_STRING_FIELDS:
                item[dst_col] = str(val).strip() if val is not None and str(val) != 'nan' else None
            else:
                item[dst_col] = _safe_float(val)
        ocf = item.get('operating_cf')
        capex = item.get('capex')
        if ocf is not None and capex is not None:
            item['free_cashflow'] = ocf - abs(capex)
        np_val = item.get('net_profit')
        if ocf and np_val and np_val != 0:
            item['cf_quality'] = round(ocf / np_val, 2)
        if item.get('report_date'):
            item['report_date'] = str(item['report_date'])[:10]
        items.append(item)
    return items


def _fetch_from_em(symbol: str, periods: int) -> dict:
    import time as _time

    t0 = _time.time()
    result: dict = {
        'balance_sheet': [],
        'income_statement': [],
        'cashflow': [],
    }
    errors: list[str] = []

    for name, fn in [
        ('balance_sheet', _fetch_balance_sheet),
        ('income_statement', _fetch_income_statement),
        ('cashflow', _fetch_cashflow),
    ]:
        try:
            result[name] = fn(symbol, periods)
            logger.info(f"[FinancialStatements] EM {name} OK for {symbol}: "
                        f"{len(result[name])} periods")
        except Exception as e:
            errors.append(f"{name}: {e}")
            logger.warning(f"[FinancialStatements] EM {name} failed for {symbol}: {e}")

    total = sum(len(result[k]) for k in result)
    if total == 0:
        raise ValueError(f"东方财富所有报表均返回空: {'; '.join(errors)}")

    result['source'] = '东方财富'
    logger.info(f"[FinancialStatements] EM total {_time.time() - t0:.1f}s for {symbol}")
    return result