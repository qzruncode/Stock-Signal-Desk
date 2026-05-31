# -*- coding: utf-8 -*-
"""Core financial indicators + three financial statements endpoints.

Data sources (in fallback order):
  1. akshare.stock_financial_abstract_ths() — 同花顺 (primary)
  2. akshare.stock_financial_abstract()      — 新浪财经 (fallback)

Three financial statements (balance sheet, income, cashflow) come from
东方财富 (stock_*_by_report_em), which requires a market-prefixed symbol
(e.g. SH600519).

Returns single-quarter financial data: profitability, growth, solvency,
and per-share metrics. Per-day cache since financial data changes quarterly.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)
router = APIRouter()

CACHE_KEY = "financials:v2"

# ---------------------------------------------------------------------------
# Market prefix helper
# ---------------------------------------------------------------------------

def _to_em_symbol(symbol: str) -> str:
    """Convert plain symbol to East Money format with market prefix.

    SH: 600xxx, 601xxx, 603xxx, 605xxx, 688xxx
    SZ: 000xxx, 001xxx, 002xxx, 003xxx, 300xxx, 301xxx
    BJ: 8xxxxx, 9xxxxx
    """
    code = symbol.strip()
    if code.startswith(('SH', 'SZ', 'BJ')):
        return code
    if code[0] in ('6', '9'):
        return f"SH{code}"
    return f"SZ{code}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        v = float(val)
    except (ValueError, TypeError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _safe_amount(val) -> Optional[float]:
    """Parse amount string like '747.34亿', '1.47亿', '628.00万' to float (in 元)."""
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() in ('false', 'none', 'nan', '-'):
        return None
    multiplier = 1.0
    if '亿' in s:
        s = s.replace('亿', '')
        multiplier = 1e8
    elif '万' in s:
        s = s.replace('万', '')
        multiplier = 1e4
    try:
        return float(s) * multiplier
    except (ValueError, TypeError):
        return None


def _safe_pct(val) -> Optional[float]:
    """Parse percentage string like '23.38%' to float 23.38."""
    if val is None:
        return None
    s = str(val).strip().replace("%", "")
    if not s or s.lower() in ('false', 'none', 'nan', '-'):
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _cache_key(symbol: str) -> str:
    return f"{CACHE_KEY}:{symbol}:{datetime.now().strftime('%Y%m%d')}"


def _cache_get(symbol: str) -> dict | None:
    try:
        from src.storage import DatabaseManager
        raw = DatabaseManager.get_instance().get_kline_snapshot(_cache_key(symbol))
        if raw:
            data = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(data, dict) and 'symbol' in data:
                logger.info(f"[Financials] cache HIT {_cache_key(symbol)}")
                return data
    except Exception as e:
        logger.warning(f"[Financials] cache read error: {e}")
    return None


def _cache_put(symbol: str, data: dict) -> None:
    try:
        from src.storage import DatabaseManager
        DatabaseManager.get_instance().save_kline_snapshot(
            _cache_key(symbol), json.dumps(data, ensure_ascii=False))
        logger.info(f"[Financials] cache SAVED {_cache_key(symbol)}")
    except Exception as e:
        logger.warning(f"[Financials] cache write error: {e}")


# ---------------------------------------------------------------------------
# Source 1: 同花顺 (stock_financial_abstract_ths)
# Cumulative data, needs diff to get single-quarter.
# ---------------------------------------------------------------------------

_THS_COLUMN_MAP = {
    '报告期': 'report_date',
    '净利润': 'net_profit',
    '净利润同比增长率': 'net_profit_yoy',
    '扣非净利润': 'deducted_profit',
    '扣非净利润同比增长率': 'deducted_profit_yoy',
    '营业总收入': 'revenue',
    '营业总收入同比增长率': 'revenue_yoy',
    '基本每股收益': 'eps',
    '每股净资产': 'bps',
    '销售净利率': 'net_margin',
    '销售毛利率': 'gross_margin',
    '净资产收益率': 'roe',
    '净资产收益率-摊薄': 'roe_diluted',
    '流动比率': 'current_ratio',
    '速动比率': 'quick_ratio',
    '资产负债率': 'debt_ratio',
}

_THS_PCT_FIELDS = {
    'net_profit_yoy', 'deducted_profit_yoy', 'revenue_yoy',
    'net_margin', 'gross_margin', 'roe', 'roe_diluted', 'debt_ratio',
}

_THS_AMOUNT_FIELDS = {'net_profit', 'deducted_profit', 'revenue'}

# Fields whose values are cumulative (Q2=H1, Q3=3Q, Q4=annual).
_THS_CUMULATIVE_FIELDS = {'net_profit', 'deducted_profit', 'revenue'}


def _fetch_from_ths(symbol: str, periods: int) -> list[dict]:
    """Fetch from 同花顺. Returns cumulative data converted to single-quarter."""
    import akshare as ak

    df = ak.stock_financial_abstract_ths(symbol=symbol, indicator='按报告期')
    if df is None or df.empty:
        raise ValueError("同花顺返回空数据")

    all_rows = []
    for _, row in df.iterrows():
        item = {}
        for src_col, dst_col in _THS_COLUMN_MAP.items():
            if src_col not in row.index:
                continue
            val = row[src_col]
            if dst_col in _THS_PCT_FIELDS:
                item[dst_col] = _safe_pct(val)
            elif dst_col in _THS_AMOUNT_FIELDS:
                item[dst_col] = _safe_amount(val)
            else:
                item[dst_col] = _safe_float(val)

        raw_date = row.get('报告期', '')
        if raw_date is not None and str(raw_date).strip() not in ('', 'nan', 'None'):
            item['report_date'] = str(raw_date).strip()
        all_rows.append(item)

    all_rows.sort(key=lambda x: x.get('report_date', ''))

    # Diff cumulative → single-quarter
    items = []
    for i, cur in enumerate(all_rows):
        is_q1 = (cur.get('report_date') or '').endswith('03-31')
        if i == 0 or is_q1:
            items.append(cur)
            continue
        prev = all_rows[i - 1]
        single = dict(cur)
        for field in _THS_CUMULATIVE_FIELDS:
            cv = cur.get(field)
            pv = prev.get(field)
            if cv is not None and pv is not None:
                single[field] = cv - pv
        items.append(single)

    # Take last N, but need periods+1 for proper Q1 diff
    if len(items) > periods:
        # Include one extra before the window for Q1 diff reference
        start_idx = len(items) - periods - 1
        if start_idx >= 0:
            items = items[start_idx:]

    return items[-periods:]


# ---------------------------------------------------------------------------
# Source 2: 新浪财经 (stock_financial_abstract)
# Already single-quarter data. Rows=indicators, Columns=report dates.
# ---------------------------------------------------------------------------

# Mapping: (选项, 指标) → response field
_SINA_INDICATOR_MAP = {
    ('常用指标', '营业总收入'): 'revenue',
    ('常用指标', '净利润'): 'net_profit',
    ('常用指标', '扣非净利润'): 'deducted_profit',
    ('常用指标', '基本每股收益'): 'eps',
    ('常用指标', '每股净资产'): 'bps',
    ('常用指标', '净资产收益率(ROE)'): 'roe',
    ('常用指标', '毛利率'): 'gross_margin',
    ('常用指标', '销售净利率'): 'net_margin',
    ('常用指标', '资产负债率'): 'debt_ratio',
    ('盈利能力', '净资产收益率(ROE)'): 'roe',
    ('盈利能力', '摊薄净资产收益率'): 'roe_diluted',
    ('成长能力', '营业总收入增长率'): 'revenue_yoy',
    ('成长能力', '归属母公司净利润增长率'): 'net_profit_yoy',
    ('财务风险', '流动比率'): 'current_ratio',
    ('财务风险', '速动比率'): 'quick_ratio',
    ('财务风险', '资产负债率'): 'debt_ratio',
    ('每股指标', '基本每股收益'): 'eps',
    ('每股指标', '每股净资产_最新股数'): 'bps',
}


def _fetch_from_sina(symbol: str, periods: int) -> list[dict]:
    """Fetch from 新浪财经. Data is already single-quarter."""
    import akshare as ak
    import pandas as pd

    df = ak.stock_financial_abstract(symbol=symbol)
    if df is None or df.empty:
        raise ValueError("新浪返回空数据")

    # Build lookup: (选项, 指标) → {date_col: value}
    lookup: dict[tuple, dict[str, any]] = {}
    for _, row in df.iterrows():
        key = (str(row['选项']), str(row['指标']))
        lookup[key] = {str(k): v for k, v in row.items()
                       if k not in ('选项', '指标') and pd.notna(v)}

    # Collect all date columns, sort, take last `periods+1` (extra for diff)
    all_dates = sorted(
        {k for v in lookup.values() for k in v},
        reverse=True,
    )[:periods + 1]
    all_dates = sorted(all_dates)  # chronological

    # Parse all cumulative items, then diff
    all_items = []
    for date_col in all_dates:
        d = date_col.strip()
        if len(d) == 8:
            d = f"{d[:4]}-{d[4:6]}-{d[6:8]}"

        item: dict = {'report_date': d}
        for (opt, ind), dst in _SINA_INDICATOR_MAP.items():
            if dst in item:
                continue
            row_data = lookup.get((opt, ind), {})
            val = row_data.get(date_col)
            if val is not None:
                s = str(val).strip()
                if s in ('', '--', 'nan', 'None'):
                    continue
                if dst in ('net_margin', 'gross_margin', 'roe', 'roe_diluted',
                           'debt_ratio', 'net_profit_yoy', 'revenue_yoy'):
                    item[dst] = _safe_pct(s)
                elif dst in ('revenue', 'net_profit', 'deducted_profit'):
                    item[dst] = _safe_amount(s)
                else:
                    item[dst] = _safe_float(s)

        has_data = any(v is not None for k, v in item.items() if k != 'report_date')
        if has_data:
            all_items.append(item)

    # Diff cumulative amounts → single-quarter
    items = []
    for i, cur in enumerate(all_items):
        is_q1 = (cur.get('report_date') or '').endswith('03-31')
        if i == 0 or is_q1:
            items.append(cur)
            continue
        prev = all_items[i - 1]
        single = dict(cur)
        for field in ('revenue', 'net_profit', 'deducted_profit'):
            cv = cur.get(field)
            pv = prev.get(field)
            if cv is not None and pv is not None:
                single[field] = cv - pv
        items.append(single)

    return items[-periods:]


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def _fetch_financials(symbol: str, periods: int = 12) -> dict:
    """Fetch core financial indicators with multi-source fallback."""
    import time as _time

    t0 = _time.time()
    result: dict = {
        'symbol': symbol,
        'periods': periods,
        'items': [],
        '_fetched_at': datetime.now().isoformat(),
        '_cached': False,
    }
    errors: list[str] = []

    # Source 1: 同花顺
    try:
        items = _fetch_from_ths(symbol, periods)
        if items:
            result['items'] = items
            result['source'] = '同花顺'
            logger.info(f"[Financials] 同花顺 OK for {symbol}: {len(items)} periods, {_time.time() - t0:.1f}s")
            return result
        errors.append("同花顺返回空数据")
    except Exception as e:
        errors.append(f"同花顺: {e}")
        logger.warning(f"[Financials] 同花顺 failed for {symbol}: {e}")

    # Source 2: 新浪财经
    try:
        items = _fetch_from_sina(symbol, periods)
        if items:
            result['items'] = items
            result['source'] = '新浪财经'
            logger.info(f"[Financials] 新浪 OK for {symbol}: {len(items)} periods, {_time.time() - t0:.1f}s")
            return result
        errors.append("新浪返回空数据")
    except Exception as e:
        errors.append(f"新浪: {e}")
        logger.warning(f"[Financials] 新浪 failed for {symbol}: {e}")

    logger.warning(f"[Financials] all sources failed for {symbol}: {'; '.join(errors)}")
    return result


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

import threading
_lock = None


@router.get("/financials", summary="获取核心财务指标")
def get_financials(
    symbol: str = Query(..., description="股票代码，如 600519"),
    periods: int = Query(12, ge=1, le=40, description="返回最近 N 个报告期数据（默认12=3年）"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """获取单只股票的核心财务指标（单季度数据）。

    包含盈利能力（ROE、毛利率、净利率）、成长性（营收/利润增长率）、
    偿债能力（资产负债率、流动/速动比率）、每股指标（EPS、BPS）。

    数据源（按优先级）：
    1. 同花顺 (stock_financial_abstract_ths)
    2. 新浪财经 (stock_financial_abstract)

    按天缓存。
    """
    symbol = symbol.strip()

    if not force:
        cached = _cache_get(symbol)
        if cached:
            cached['_cached'] = True

            global _lock
            if _lock is None:
                _lock = threading.Lock()
            if _lock.acquire(blocking=False):
                def _bg_refresh():
                    try:
                        _cache_put(symbol, _fetch_financials(symbol, periods))
                    finally:
                        _lock.release()
                threading.Thread(target=_bg_refresh, daemon=True).start()

            return cached

    data = _fetch_financials(symbol, periods)
    _cache_put(symbol, data)
    return data


# ============================================================================
# Three Financial Statements
#
# Data sources (in fallback order):
#   1. 东方财富 stock_*_by_report_em() — 三张完整报表，单季度粒度
#   2. 同花顺 stock_financial_{debt,benefit,cash}_ths() — 三张完整报表，单季度粒度
#   3. 新浪财经 stock_financial_report_sina() — 三张完整报表，单季度粒度
#   4. 同花顺 stock_financial_abstract_ths() — 聚合指标，仅利润表有数据
#
# Cache: per-symbol per-periods per-day, stored via KlineSnapshot table.
# ============================================================================

FINS_STATEMENTS_CACHE_KEY = "financials:statements:v2"


def _fins_cache_key(symbol: str, periods: int) -> str:
    return f"{FINS_STATEMENTS_CACHE_KEY}:{symbol}:p{periods}:{datetime.now().strftime('%Y%m%d')}"


def _fins_cache_get(symbol: str, periods: int) -> dict | None:
    try:
        from src.storage import DatabaseManager
        key = _fins_cache_key(symbol, periods)
        raw = DatabaseManager.get_instance().get_kline_snapshot(key)
        if raw:
            data = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(data, dict) and 'symbol' in data:
                # Validate periods match (prevent stale cache with wrong periods)
                if data.get('periods') == periods:
                    logger.info(f"[FinancialStatements] cache HIT {key}")
                    return data
                logger.info(f"[FinancialStatements] cache STALE (periods mismatch) {key}")
    except Exception as e:
        logger.warning(f"[FinancialStatements] cache read error: {e}")
    return None


def _fins_cache_put(symbol: str, periods: int, data: dict) -> None:
    try:
        from src.storage import DatabaseManager
        key = _fins_cache_key(symbol, periods)
        DatabaseManager.get_instance().save_kline_snapshot(
            key, json.dumps(data, ensure_ascii=False))
        logger.info(f"[FinancialStatements] cache SAVED {key}")
    except Exception as e:
        logger.warning(f"[FinancialStatements] cache write error: {e}")


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


# --- Balance Sheet ---

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
    'FIXED_ASSET': 'fixed_asset',
    'SHORT_LOAN': 'short_loan',
    'LONG_LOAN': 'long_loan',
    'ACCOUNTS_PAYABLE': 'accounts_payable',
    'TOTAL_CURRENT_ASSETS': 'total_current_assets',
    'TOTAL_CURRENT_LIAB': 'total_current_liabilities',
}

_BS_STRING_FIELDS = {'report_date', 'report_date_name'}


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


# --- Income Statement ---

_IS_COLUMNS = {
    'REPORT_DATE': 'report_date',
    'REPORT_DATE_NAME': 'report_date_name',
    'TOTAL_OPERATE_INCOME': 'revenue',
    'TOTAL_OPERATE_COST': 'total_cost',
    'OPERATE_COST': 'operate_cost',
    'OPERATE_PROFIT': 'operate_profit',
    'TOTAL_PROFIT': 'total_profit',
    'NETPROFIT': 'net_profit',
    'PARENT_NETPROFIT': 'parent_net_profit',
    'DEDUCT_PARENT_NETPROFIT': 'deducted_net_profit',
    'BASIC_EPS': 'basic_eps',
    'DILUTED_EPS': 'diluted_eps',
    'SALE_EXPENSE': 'sale_expense',
    'MANAGE_EXPENSE': 'manage_expense',
    'RESEARCH_EXPENSE': 'research_expense',
    'FINANCE_EXPENSE': 'finance_expense',
    'INVEST_INCOME': 'invest_income',
    'OPERATE_TAX_ADD': 'operate_tax_add',
    'INCOME_TAX': 'income_tax',
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


# --- Cash Flow Statement ---

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


# --- Source 1: 东方财富 (primary) ---

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


# --- Source 2: 同花顺 (三张独立表) ---

_THS_DEBT_COLUMNS = {
    '报告期': 'report_date',
    '*资产合计': 'total_assets',
    '*负债合计': 'total_liabilities',
    '*所有者权益（或股东权益）合计': 'total_equity',
    '*归属于母公司所有者权益合计': 'parent_equity',
    '货币资金': 'monetary_funds',
    '应收账款': 'accounts_receivable',
    '存货': 'inventory',
    '固定资产合计': 'fixed_asset',
    '短期借款': 'short_loan',
    '长期借款': 'long_loan',
    '应付账款': 'accounts_payable',
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
    'total_current_assets', 'total_current_liabilities',
    'revenue', 'total_cost', 'operate_cost', 'net_profit',
    'parent_net_profit', 'deducted_net_profit',
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


# --- Source 3: 新浪财经 ---

_SINA_BS_COLUMNS = {
    '报告日': 'report_date',
    '资产总计': 'total_assets',
    '负债合计': 'total_liabilities',
    '所有者权益(或股东权益)合计': 'total_equity',
    '归属于母公司股东权益合计': 'parent_equity',
    '货币资金': 'monetary_funds',
    '应收账款': 'accounts_receivable',
    '存货': 'inventory',
    '固定资产及清理合计': 'fixed_asset',
    '短期借款': 'short_loan',
    '长期借款': 'long_loan',
    '应付账款': 'accounts_payable',
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


# --- Source 4: 同花顺 聚合指标 (last resort) ---

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

    items = _fetch_from_ths(symbol, periods)
    if not items:
        raise ValueError("同花顺聚合指标返回空数据")

    for item in items:
        rd = item.get('report_date', '')
        rdn = (rd[:4] + 'Q' + str((int(rd[5:7]) + 2) // 3)) if len(rd) >= 10 else ''

        result['balance_sheet'].append({
            'report_date': rd, 'report_date_name': rdn,
            'total_assets': None, 'total_liabilities': None, 'total_equity': None,
            'parent_equity': None, 'monetary_funds': None, 'accounts_receivable': None,
            'inventory': None, 'fixed_asset': None, 'short_loan': None,
            'long_loan': None, 'accounts_payable': None,
            'total_current_assets': None, 'total_current_liabilities': None,
            'debt_ratio': item.get('debt_ratio'), 'equity_multiplier': None,
        })
        result['income_statement'].append({
            'report_date': rd, 'report_date_name': rdn,
            'revenue': item.get('revenue'), 'total_cost': None,
            'operate_cost': None, 'operate_profit': None, 'total_profit': None,
            'net_profit': item.get('net_profit'), 'parent_net_profit': None,
            'deducted_net_profit': item.get('deducted_profit'),
            'basic_eps': item.get('eps'), 'diluted_eps': None,
            'sale_expense': None, 'manage_expense': None, 'research_expense': None,
            'finance_expense': None, 'invest_income': None,
            'operate_tax_add': None, 'income_tax': None,
            'gross_profit': None, 'gross_margin': item.get('gross_margin'),
            'net_margin': item.get('net_margin'),
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


# --- Orchestrator ---

def _fetch_financial_statements(symbol: str, periods: int = 12) -> dict:
    """Fetch all three financial statements with multi-source fallback.

    Fallback chain:
      1. 东方财富 stock_*_by_report_em() — 三张完整报表，单季度
      2. 同花顺 stock_financial_{debt,benefit,cash}_ths() — 三张完整报表，单季度
      3. 新浪财经 stock_financial_report_sina() — 三张完整报表，单季度
      4. 同花顺 stock_financial_abstract_ths() — 聚合指标，仅利润表有数据
    """
    import time as _time

    t0 = _time.time()
    result: dict = {
        'symbol': symbol,
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
        ('同花顺(聚合)', _fetch_from_ths_abstract),
    ]

    for src_name, src_fn in sources:
        try:
            src_data = src_fn(symbol, periods)
            result.update(src_data)
            if src_name != '东方财富':
                result['_fallback'] = True
            logger.info(f"[FinancialStatements] total {_time.time() - t0:.1f}s for {symbol} (source: {src_name})")
            return result
        except Exception as e:
            errors.append(f"{src_name}: {e}")
            logger.warning(f"[FinancialStatements] {src_name} failed for {symbol}: {e}")

    result['_errors'] = errors
    logger.warning(f"[FinancialStatements] all sources failed for {symbol}: {'; '.join(errors)}")
    return result


# --- Endpoint ---


@router.get("/financials/statements", summary="获取三大财务报表")
def get_financial_statements(
    symbol: str = Query(..., description="股票代码，如 600519"),
    periods: int = Query(12, ge=4, le=40, description="返回最近 N 个报告期数据（默认12=3年单季度）"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """获取单只股票的三大财务报表（单季度数据）。

    包含：
    - 资产负债表：总资产、总负债、股东权益、货币资金、应收账款、存货、固定资产、
      短期借款、长期借款、应付账款、资产负债率、权益乘数
    - 利润表：营业总收入、营业总成本、营业利润、利润总额、净利润、扣非净利润、
      基本每股收益、稀释每股收益、毛利率、净利率
    - 现金流量表：经营活动现金流净额、投资活动现金流净额、筹资活动现金流净额、
      自由现金流、经营现金流/净利润（利润含金量）

    数据源：东方财富 (stock_*_by_report_em)，按天缓存。
    """
    symbol = symbol.strip()

    if not force:
        cached = _fins_cache_get(symbol, periods)
        if cached:
            cached['_cached'] = True

            # Background refresh — keep cache warm, same pattern as stock_info and financials
            global _fins_lock
            if _fins_lock is None:
                _fins_lock = threading.Lock()
            if _fins_lock.acquire(blocking=False):
                def _bg_refresh():
                    try:
                        _fins_cache_put(symbol, periods, _fetch_financial_statements(symbol, periods))
                    finally:
                        _fins_lock.release()
                threading.Thread(target=_bg_refresh, daemon=True).start()

            return cached

    data = _fetch_financial_statements(symbol, periods)
    _fins_cache_put(symbol, periods, data)
    return data
