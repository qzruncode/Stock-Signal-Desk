# -*- coding: utf-8 -*-
"""Three financial statements: balance sheet, income statement, cash flow.

Data sources: 东方财富 → 同花顺 → 新浪财经 (fallback chain).
"""
from __future__ import annotations

import logging
import time as _time
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

from ._helpers import (
    _normalize_symbol, _to_em_symbol, _safe_float, _safe_str, _safe_amount,
    _safe_pct, _pick_col, _parse_date,
)
from ._cache import _fins_cache_get, _fins_cache_put, FINS_STATEMENTS_CACHE_KEY

def _pick_quarters(df, periods: int):
    """Pick the last N quarters of data from the DataFrame.

    Different sources use different date column names:
    - EM: REPORT_DATE
    - THS: 报告期
    - Sina: 报告日

    We auto-detect and take the last `periods` rows.
    """
    if df is None or df.empty:
        return df
    for col in ['REPORT_DATE', '报告期', '报告日']:
        if col in df.columns:
            df = df.sort_values(col)
            break
    return df.tail(periods)


_BS_COLUMNS = {
    'REPORT_DATE': 'report_date',
    'REPORT_DATE_NAME': 'report_date_name',
    'TOTAL_ASSETS': 'total_assets',
    'TOTAL_LIABILITIES': 'total_liabilities',
    'TOTAL_EQUITY': 'total_equity',
    'TOTAL_PARENT_EQUITY': 'parent_equity',
    'MONETARYFUNDS': 'monetary_funds',
    'ACCOUNTS_RECE': 'accounts_receivable',
    'INVENTORY': 'inventory',
    'CONTRACT_LIAB': 'contract_liabilities',
    'FIXED_ASSET': 'fixed_asset',
    'SHORT_LOAN': 'short_loan',
    'LONG_LOAN': 'long_loan',
    'ACCOUNTS_PAYABLE': 'accounts_payable',
    'NONCURRENT_LIAB_1YEAR': 'noncurrent_liab_1year',
    'LEASE_LIAB': 'lease_liab',
    'TOTAL_CURRENT_ASSETS': 'total_current_assets',
    'TOTAL_CURRENT_LIAB': 'total_current_liabilities',
}

_BS_STRING_FIELDS = {'report_date', 'report_date_name'}


def _normalize_balance_debt_fields(item: dict) -> None:
    """Treat absent borrowing line items as zero in balance-sheet displays."""
    if item.get('total_liabilities') is None:
        return
    for field in ('short_loan', 'long_loan'):
        if item.get(field) is None:
            item[field] = 0.0


def _fetch_balance_sheet(symbol: str, periods: int) -> list[dict]:
    """Fetch balance sheet from 东方财富."""
    import akshare as ak

    em_symbol = _to_em_symbol(symbol)
    df = ak.stock_balance_sheet_by_report_em(symbol=em_symbol)
    if df is None or df.empty:
        raise ValueError("东方财富资产负债表返回空数据")

    df = _pick_quarters(df, periods)
    items = []
    for _, row in df.iterrows():
        item = {}
        for src_col, dst_col in _BS_COLUMNS.items():
            if src_col not in row.index:
                continue
            val = row[src_col]
            if dst_col in _BS_STRING_FIELDS:
                item[dst_col] = str(val).strip() if val is not None and str(val) != 'nan' else None
            else:
                item[dst_col] = _safe_float(val)
        # Compute derived ratios
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


_IS_COLUMNS = {
    'REPORT_DATE': 'report_date',
    'REPORT_DATE_NAME': 'report_date_name',
    'TOTAL_OPERATE_INCOME': 'revenue',
    'TOTAL_OPERATE_INCOME_YOY': 'revenue_yoy',
    'TOTAL_OPERATE_COST': 'total_cost',
    'OPERATE_COST': 'operate_cost',
    'OPERATE_PROFIT': 'operate_profit',
    'TOTAL_PROFIT': 'total_profit',
    'NETPROFIT': 'net_profit',
    'NETPROFIT_YOY': 'net_profit_yoy',
    'PARENT_NETPROFIT': 'parent_net_profit',
    'PARENT_NETPROFIT_YOY': 'parent_net_profit_yoy',
    'DEDUCT_PARENT_NETPROFIT': 'deducted_net_profit',
    'DEDUCT_PARENT_NETPROFIT_YOY': 'deducted_net_profit_yoy',
    'BASIC_EPS': 'basic_eps',
    'DILUTED_EPS': 'diluted_eps',
    'SALE_EXPENSE': 'sale_expense',
    'MANAGE_EXPENSE': 'manage_expense',
    'RESEARCH_EXPENSE': 'research_expense',
    'FINANCE_EXPENSE': 'finance_expense',
    'INVEST_INCOME': 'invest_income',
    'OPERATE_TAX_ADD': 'operate_tax_add',
    'INCOME_TAX': 'income_tax',
    'ASSET_IMPAIRMENT_LOSS': 'asset_impairment_loss',
}

_IS_STRING_FIELDS = {'report_date', 'report_date_name'}


def _fetch_income_statement(symbol: str, periods: int) -> list[dict]:
    """Fetch income statement from 东方财富."""
    import akshare as ak

    em_symbol = _to_em_symbol(symbol)
    df = ak.stock_profit_sheet_by_report_em(symbol=em_symbol)
    if df is None or df.empty:
        raise ValueError("东方财富利润表返回空数据")

    df = _pick_quarters(df, periods)
    items = []
    for _, row in df.iterrows():
        item = {}
        for src_col, dst_col in _IS_COLUMNS.items():
            if src_col not in row.index:
                continue
            val = row[src_col]
            if dst_col in _IS_STRING_FIELDS:
                item[dst_col] = str(val).strip() if val is not None and str(val) != 'nan' else None
            else:
                item[dst_col] = _safe_float(val)
        # Compute gross profit and margin
        rev = item.get('revenue')
        oc = item.get('operate_cost')
        if rev and oc and rev != 0:
            item['gross_profit'] = rev - oc
            item['gross_margin'] = round((rev - oc) / rev * 100, 2)
        # Net margin
        np_val = item.get('net_profit')
        if rev and np_val and rev != 0:
            item['net_margin'] = round(np_val / rev * 100, 2)
        if item.get('report_date'):
            item['report_date'] = str(item['report_date'])[:10]
        items.append(item)
    return items


_CF_COLUMNS = {
    'REPORT_DATE': 'report_date',
    'REPORT_DATE_NAME': 'report_date_name',
    'NETCASH_OPERATE': 'operating_cf',
    'NETCASH_INVEST': 'investing_cf',
    'NETCASH_FINANCE': 'financing_cf',
    'CONSTRUCT_LONG_ASSET': 'capex',
    'NETPROFIT': 'net_profit',
}

_CF_STRING_FIELDS = {'report_date', 'report_date_name'}


def _fetch_cashflow(symbol: str, periods: int) -> list[dict]:
    """Fetch cash flow statement from 东方财富."""
    import akshare as ak

    em_symbol = _to_em_symbol(symbol)
    df = ak.stock_cash_flow_sheet_by_report_em(symbol=em_symbol)
    if df is None or df.empty:
        raise ValueError("东方财富现金流量表返回空数据")

    df = _pick_quarters(df, periods)
    items = []
    for _, row in df.iterrows():
        item = {}
        for src_col, dst_col in _CF_COLUMNS.items():
            if src_col not in row.index:
                continue
            val = row[src_col]
            if dst_col in _CF_STRING_FIELDS:
                item[dst_col] = str(val).strip() if val is not None and str(val) != 'nan' else None
            else:
                item[dst_col] = _safe_float(val)
        # Free cash flow = operating CF - capex
        ocf = item.get('operating_cf')
        capex = item.get('capex')
        if ocf is not None and capex is not None:
            item['free_cashflow'] = ocf - abs(capex)
        # Cash flow quality = operating CF / net profit
        np_val = item.get('net_profit')
        if ocf and np_val and np_val != 0:
            item['cf_quality'] = round(ocf / np_val, 2)
        if item.get('report_date'):
            item['report_date'] = str(item['report_date'])[:10]
        items.append(item)
    return items


def _fetch_from_em(symbol: str, periods: int) -> dict:
    """Fetch all three financial statements from 东方财富.

    Returns dict with balance_sheet, income_statement, cashflow lists.
    Raises on total failure (all three empty).
    """
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


_THS_DEBT_COLUMNS = {
    '报告期': 'report_date',
    '*资产合计': 'total_assets',
    '*负债合计': 'total_liabilities',
    '*所有者权益（或股东权益）合计': 'total_equity',
    '*归属于母公司所有者权益合计': 'parent_equity',
    '货币资金': 'monetary_funds',
    '应收账款': 'accounts_receivable',
    '存货': 'inventory',
    '合同负债': 'contract_liabilities',
    '固定资产合计': 'fixed_asset',
    '短期借款': 'short_loan',
    '长期借款': 'long_loan',
    '应付账款': 'accounts_payable',
    '一年内到期的非流动负债': 'noncurrent_liab_1year',
    '流动资产合计': 'total_current_assets',
    '流动负债合计': 'total_current_liabilities',
}

_THS_BENEFIT_COLUMNS = {
    '报告期': 'report_date',
    '*营业总收入': 'revenue',
    '*营业总成本': 'total_cost',
    '其中：营业成本': 'operate_cost',
    '*净利润': 'net_profit',
    '*归属于母公司所有者的净利润': 'parent_net_profit',
    '*扣除非经常性损益后的净利润': 'deducted_net_profit',
    '销售费用': 'sale_expense',
    '管理费用': 'manage_expense',
    '研发费用': 'research_expense',
    '财务费用': 'finance_expense',
    '营业税金及附加': 'operate_tax_add',
    '资产减值损失': 'asset_impairment_loss',
}

_THS_CASH_COLUMNS = {
    '报告期': 'report_date',
    '*经营活动产生的现金流量净额': 'operating_cf',
    '*投资活动产生的现金流量净额': 'investing_cf',
    '*筹资活动产生的现金流量净额': 'financing_cf',
}

_THS_STRING_FIELDS = {'report_date'}

# THS columns whose values are amount strings like '2922.58亿', '3796.12万'
_THS_AMOUNT_FIELDS = {
    'total_assets', 'total_liabilities', 'total_equity', 'parent_equity',
    'monetary_funds', 'accounts_receivable', 'inventory', 'fixed_asset',
    'short_loan', 'long_loan', 'accounts_payable',
    'noncurrent_liab_1year', 'lease_liab',
    'total_current_assets', 'total_current_liabilities',
    'revenue', 'total_cost', 'operate_cost', 'net_profit',
    'parent_net_profit', 'deducted_net_profit', 'deducted_profit',
    'sale_expense', 'manage_expense', 'research_expense', 'finance_expense',
    'operate_tax_add', 'operating_cf', 'investing_cf', 'financing_cf',
}


def _fetch_from_ths_triple(symbol: str, periods: int) -> dict:
    """Fetch three financial statements from 同花顺 independent tables.

    Uses stock_financial_debt_ths (balance sheet),
    stock_financial_benefit_ths (income statement),
    stock_financial_cash_ths (cash flow).
    """
    import time as _time
    import akshare as ak

    t0 = _time.time()
    result: dict = {
        'balance_sheet': [],
        'income_statement': [],
        'cashflow': [],
    }
    errors: list[str] = []

    def _parse_val(dst: str, val):
        """Parse THS value: string amounts like '2922.58亿', bool False→0, or raw float."""
        if dst in _THS_STRING_FIELDS:
            return str(val).strip() if val is not None and str(val) != 'nan' else None
        if dst in _THS_AMOUNT_FIELDS:
            if val is False or (isinstance(val, str) and val.strip() in ('False', '')):
                return None
            return _safe_amount(val)
        return _safe_float(val)

    # --- Balance Sheet ---
    try:
        df = ak.stock_financial_debt_ths(symbol=symbol, indicator='按报告期')
        if df is not None and not df.empty:
            df = _pick_quarters(df, periods)
            for _, row in df.iterrows():
                item = {}
                for src, dst in _THS_DEBT_COLUMNS.items():
                    if src not in row.index:
                        continue
                    item[dst] = _parse_val(dst, row[src])
                if item.get('report_date'):
                    item['report_date'] = str(item['report_date'])[:10]
                # Compute derived ratios
                ta = item.get('total_assets')
                tl = item.get('total_liabilities')
                te = item.get('total_equity')
                if ta and ta != 0:
                    if tl is not None:
                        item['debt_ratio'] = round(tl / ta * 100, 2)
                    if te is not None and te != 0:
                        item['equity_multiplier'] = round(ta / te, 2)
                result['balance_sheet'].append(item)
    except Exception as e:
        errors.append(f"balance: {e}")
        logger.warning(f"[FinancialStatements] THS triple balance failed for {symbol}: {e}")

    # --- Income Statement ---
    try:
        df = ak.stock_financial_benefit_ths(symbol=symbol, indicator='按报告期')
        if df is not None and not df.empty:
            df = _pick_quarters(df, periods)
            for _, row in df.iterrows():
                item = {}
                for src, dst in _THS_BENEFIT_COLUMNS.items():
                    if src not in row.index:
                        continue
                    item[dst] = _parse_val(dst, row[src])
                if item.get('report_date'):
                    item['report_date'] = str(item['report_date'])[:10]
                # Compute derived
                rev = item.get('revenue')
                oc = item.get('operate_cost')
                if rev and oc and rev != 0:
                    item['gross_profit'] = rev - oc
                    item['gross_margin'] = round((rev - oc) / rev * 100, 2)
                np_val = item.get('net_profit')
                if rev and np_val and rev != 0:
                    item['net_margin'] = round(np_val / rev * 100, 2)
                result['income_statement'].append(item)
    except Exception as e:
        errors.append(f"income: {e}")
        logger.warning(f"[FinancialStatements] THS triple income failed for {symbol}: {e}")

    # --- Cash Flow ---
    try:
        df = ak.stock_financial_cash_ths(symbol=symbol, indicator='按报告期')
        if df is not None and not df.empty:
            df = _pick_quarters(df, periods)
            for _, row in df.iterrows():
                item = {}
                for src, dst in _THS_CASH_COLUMNS.items():
                    if src not in row.index:
                        continue
                    item[dst] = _parse_val(dst, row[src])
                if item.get('report_date'):
                    item['report_date'] = str(item['report_date'])[:10]
                # Derived
                ocf = item.get('operating_cf')
                if ocf is not None:
                    item['free_cashflow'] = ocf  # THS doesn't have capex separately
                result['cashflow'].append(item)
    except Exception as e:
        errors.append(f"cashflow: {e}")
        logger.warning(f"[FinancialStatements] THS triple cashflow failed for {symbol}: {e}")

    total = sum(len(result[k]) for k in result)
    if total == 0:
        raise ValueError(f"同花顺三表均返回空: {'; '.join(errors)}")

    result['source'] = '同花顺'
    logger.info(f"[FinancialStatements] THS triple {_time.time() - t0:.1f}s for {symbol}: "
                f"BS={len(result['balance_sheet'])} IS={len(result['income_statement'])} CF={len(result['cashflow'])}")
    return result


_SINA_BS_COLUMNS = {
    '报告日': 'report_date',
    '资产总计': 'total_assets',
    '负债合计': 'total_liabilities',
    '所有者权益(或股东权益)合计': 'total_equity',
    '归属于母公司股东权益合计': 'parent_equity',
    '货币资金': 'monetary_funds',
    '应收账款': 'accounts_receivable',
    '存货': 'inventory',
    '合同负债': 'contract_liabilities',
    '固定资产及清理合计': 'fixed_asset',
    '短期借款': 'short_loan',
    '长期借款': 'long_loan',
    '应付账款': 'accounts_payable',
    '一年内到期的非流动负债': 'noncurrent_liab_1year',
    '租赁负债': 'lease_liab',
    '流动资产合计': 'total_current_assets',
    '流动负债合计': 'total_current_liabilities',
}

_SINA_IS_COLUMNS = {
    '报告日': 'report_date',
    '一、营业总收入': 'revenue',
    '二、营业总成本': 'total_cost',
    '其中：营业成本': 'operate_cost',
    '五、净利润': 'net_profit',
    '归属于母公司所有者的净利润': 'parent_net_profit',
    '扣除非经常性损益后的净利润': 'deducted_net_profit',
    '销售费用': 'sale_expense',
    '管理费用': 'manage_expense',
    '研发费用': 'research_expense',
    '财务费用': 'finance_expense',
    '营业税金及附加': 'operate_tax_add',
    '资产减值损失': 'asset_impairment_loss',
}

_SINA_CF_COLUMNS = {
    '报告日': 'report_date',
    '经营活动产生的现金流量净额': 'operating_cf',
    '投资活动产生的现金流量净额': 'investing_cf',
    '筹资活动产生的现金流量净额': 'financing_cf',
}

_SINA_STRING_FIELDS = {'report_date'}


def _fetch_from_sina_full(symbol: str, periods: int) -> dict:
    """Fetch three financial statements from 新浪财经.

    Uses stock_financial_report_sina() with symbol='资产负债表'/'利润表'/'现金流量表'.
    """
    import time as _time
    import akshare as ak

    t0 = _time.time()
    result: dict = {
        'balance_sheet': [],
        'income_statement': [],
        'cashflow': [],
    }
    errors: list[str] = []

    em_symbol = _to_em_symbol(symbol)
    sina_stock = em_symbol.lower()  # e.g. 'sh600519'

    # --- Balance Sheet ---
    try:
        df = ak.stock_financial_report_sina(stock=sina_stock, symbol='资产负债表')
        if df is not None and not df.empty:
            df = _pick_quarters(df, periods)
            for _, row in df.iterrows():
                item = {}
                for src, dst in _SINA_BS_COLUMNS.items():
                    if src not in row.index:
                        continue
                    val = row[src]
                    if dst in _SINA_STRING_FIELDS:
                        item[dst] = str(val).strip() if val is not None and str(val) != 'nan' else None
                    else:
                        item[dst] = _safe_float(val)
                if item.get('report_date'):
                    item['report_date'] = str(item['report_date'])[:4] + '-' + \
                                          str(item['report_date'])[4:6] + '-' + \
                                          str(item['report_date'])[6:8]
                ta = item.get('total_assets')
                tl = item.get('total_liabilities')
                te = item.get('total_equity')
                if ta and ta != 0:
                    if tl is not None:
                        item['debt_ratio'] = round(tl / ta * 100, 2)
                    if te is not None and te != 0:
                        item['equity_multiplier'] = round(ta / te, 2)
                result['balance_sheet'].append(item)
    except Exception as e:
        errors.append(f"balance: {e}")
        logger.warning(f"[FinancialStatements] Sina balance failed for {symbol}: {e}")

    # --- Income Statement ---
    try:
        df = ak.stock_financial_report_sina(stock=sina_stock, symbol='利润表')
        if df is not None and not df.empty:
            df = _pick_quarters(df, periods)
            for _, row in df.iterrows():
                item = {}
                for src, dst in _SINA_IS_COLUMNS.items():
                    if src not in row.index:
                        continue
                    val = row[src]
                    if dst in _SINA_STRING_FIELDS:
                        item[dst] = str(val).strip() if val is not None and str(val) != 'nan' else None
                    else:
                        item[dst] = _safe_float(val)
                if item.get('report_date'):
                    item['report_date'] = str(item['report_date'])[:4] + '-' + \
                                          str(item['report_date'])[4:6] + '-' + \
                                          str(item['report_date'])[6:8]
                rev = item.get('revenue')
                oc = item.get('operate_cost')
                if rev and oc and rev != 0:
                    item['gross_profit'] = rev - oc
                    item['gross_margin'] = round((rev - oc) / rev * 100, 2)
                np_val = item.get('net_profit')
                if rev and np_val and rev != 0:
                    item['net_margin'] = round(np_val / rev * 100, 2)
                result['income_statement'].append(item)
    except Exception as e:
        errors.append(f"income: {e}")
        logger.warning(f"[FinancialStatements] Sina income failed for {symbol}: {e}")

    # --- Cash Flow ---
    try:
        df = ak.stock_financial_report_sina(stock=sina_stock, symbol='现金流量表')
        if df is not None and not df.empty:
            df = _pick_quarters(df, periods)
            for _, row in df.iterrows():
                item = {}
                for src, dst in _SINA_CF_COLUMNS.items():
                    if src not in row.index:
                        continue
                    val = row[src]
                    if dst in _SINA_STRING_FIELDS:
                        item[dst] = str(val).strip() if val is not None and str(val) != 'nan' else None
                    else:
                        item[dst] = _safe_float(val)
                if item.get('report_date'):
                    item['report_date'] = str(item['report_date'])[:4] + '-' + \
                                          str(item['report_date'])[4:6] + '-' + \
                                          str(item['report_date'])[6:8]
                ocf = item.get('operating_cf')
                if ocf is not None:
                    item['free_cashflow'] = ocf
                result['cashflow'].append(item)
    except Exception as e:
        errors.append(f"cashflow: {e}")
        logger.warning(f"[FinancialStatements] Sina cashflow failed for {symbol}: {e}")

    total = sum(len(result[k]) for k in result)
    if total == 0:
        raise ValueError(f"新浪财经三表均返回空: {'; '.join(errors)}")

    result['source'] = '新浪财经'
    logger.info(f"[FinancialStatements] Sina {_time.time() - t0:.1f}s for {symbol}: "
                f"BS={len(result['balance_sheet'])} IS={len(result['income_statement'])} CF={len(result['cashflow'])}")
    return result
