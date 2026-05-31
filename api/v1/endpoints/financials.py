# -*- coding: utf-8 -*-
"""Core financial indicators endpoint.

Data sources (in fallback order):
  1. akshare.stock_financial_abstract_ths() — 同花顺 (primary)
  2. akshare.stock_financial_abstract()      — 新浪财经 (fallback)

Returns single-quarter financial data: profitability, growth, solvency,
and per-share metrics. Per-day cache since financial data changes quarterly.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)
router = APIRouter()

CACHE_KEY = "financials:v2"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


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
