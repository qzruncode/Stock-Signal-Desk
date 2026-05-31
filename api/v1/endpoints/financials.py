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
import re
from datetime import datetime, timedelta
from typing import Any, Optional

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
    if isinstance(val, str):
        val = val.strip().replace(",", "").replace("%", "")
        if not val or val.lower() in ('false', 'none', 'nan', '-'):
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


def _safe_str(val) -> str:
    if val is None:
        return ""
    text = str(val).strip()
    return "" if text in ("", "-", "nan", "None", "NaT") else text


def _normalize_symbol(symbol: str) -> str:
    code = symbol.strip().upper()
    if "." in code:
        code = code.split(".", 1)[0]
    for prefix in ("SH", "SZ", "BJ"):
        if code.startswith(prefix):
            return code[2:]
    return code


def _to_ts_code(symbol: str) -> str:
    code = _normalize_symbol(symbol)
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    if code.startswith(("8", "4")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def _to_top_holder_symbol(symbol: str) -> str:
    return _to_em_symbol(_normalize_symbol(symbol)).lower()


def _pick_col(columns, keywords: list[str], *, exclude: list[str] | None = None) -> Any:
    exclude = exclude or []
    for col in columns:
        col_s = str(col)
        if any(k.lower() in col_s.lower() for k in keywords) and not any(
            x.lower() in col_s.lower() for x in exclude
        ):
            return col
    return None


def _row_pick(row, keywords: list[str], *, exclude: list[str] | None = None) -> Any:
    col = _pick_col(row.index, keywords, exclude=exclude)
    if col is None:
        return None
    return row.get(col)


def _parse_date(val) -> Optional[datetime]:
    if val is None:
        return None
    try:
        import pandas as pd
        parsed = pd.to_datetime(val)
    except Exception:
        return None
    if parsed is None:
        return None
    try:
        if pd.isna(parsed):
            return None
        return parsed.to_pydatetime()
    except Exception:
        return None


def _latest_quarter_dates(limit: int = 8) -> list[str]:
    today = datetime.now().date()
    candidates: list[str] = []
    for year in range(today.year, today.year - 4, -1):
        for month, day in ((12, 31), (9, 30), (6, 30), (3, 31)):
            d = datetime(year, month, day).date()
            if d <= today:
                candidates.append(d.strftime("%Y%m%d"))
    return candidates[:limit]


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
# Valuation ratios + shareholder structure
# ============================================================================

VALUATION_CACHE_KEY = "stocks:valuation_ratios:v1"
SHAREHOLDER_CACHE_KEY = "stocks:shareholder_structure:v1"


def _daily_cache_key(prefix: str, symbol: str, *parts: Any) -> str:
    tail = ":".join(str(p) for p in parts if p is not None)
    suffix = f":{tail}" if tail else ""
    return f"{prefix}:{_normalize_symbol(symbol)}{suffix}:{datetime.now().strftime('%Y%m%d')}"


def _daily_cache_get(prefix: str, symbol: str, *parts: Any) -> dict | None:
    try:
        from src.storage import DatabaseManager
        key = _daily_cache_key(prefix, symbol, *parts)
        raw = DatabaseManager.get_instance().get_kline_snapshot(key)
        if raw:
            data = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(data, dict) and data.get("symbol"):
                logger.info(f"[{prefix}] cache HIT {key}")
                return data
    except Exception as e:
        logger.warning(f"[{prefix}] cache read error: {e}")
    return None


def _daily_cache_put(prefix: str, symbol: str, data: dict, *parts: Any) -> None:
    try:
        from src.storage import DatabaseManager
        key = _daily_cache_key(prefix, symbol, *parts)
        DatabaseManager.get_instance().save_kline_snapshot(key, json.dumps(data, ensure_ascii=False))
        logger.info(f"[{prefix}] cache SAVED {key}")
    except Exception as e:
        logger.warning(f"[{prefix}] cache write error: {e}")


def _fetch_lg_valuation(symbol: str):
    import akshare as ak
    import pandas as pd

    for func_name in ("stock_a_lg_indicator", "stock_a_indicator_lg"):
        fn = getattr(ak, func_name, None)
        if fn is None:
            continue
        try:
            df = fn(symbol=_normalize_symbol(symbol))
            if isinstance(df, pd.DataFrame) and not df.empty:
                return df, func_name
        except Exception as exc:
            logger.warning(f"[Valuation] {func_name} failed for {symbol}: {exc}")
    return None, None


def _fetch_em_valuation_history(symbol: str):
    import akshare as ak
    import pandas as pd

    try:
        df = ak.stock_value_em(symbol=_normalize_symbol(symbol))
        if isinstance(df, pd.DataFrame) and not df.empty:
            return df, "stock_value_em"
    except Exception as exc:
        logger.warning(f"[Valuation] stock_value_em failed for {symbol}: {exc}")
    return None, None


def _extract_em_valuation_latest(df) -> dict:
    if df is None or df.empty:
        return {}
    work_df = df.copy()
    date_col = "数据日期" if "数据日期" in work_df.columns else _pick_col(work_df.columns, ["日期", "date"])
    if date_col is not None:
        work_df["_parsed_date"] = work_df[date_col].map(_parse_date)
        work_df = work_df.sort_values("_parsed_date")
    row = work_df.iloc[-1]
    return {
        "trade_date": (
            row.get("_parsed_date").date().isoformat()
            if row.get("_parsed_date") is not None
            else _safe_str(row.get(date_col)) if date_col is not None else None
        ),
        "pe_static": _safe_float(row.get("PE(静)")),
        "pe_ttm": _safe_float(row.get("PE(TTM)")),
        "pb": _safe_float(row.get("市净率")),
        "ps": _safe_float(row.get("市销率")),
        "pcf": _safe_float(row.get("市现率")),
        "peg": _safe_float(row.get("PEG值")),
        "_latest_close": _safe_float(row.get("当日收盘价")),
    }


def _extract_valuation_latest(df) -> dict:
    if df is None or df.empty:
        return {}
    work_df = df.copy()
    date_col = _pick_col(work_df.columns, ["日期", "date", "trade"])
    if date_col is not None:
        work_df["_parsed_date"] = work_df[date_col].map(_parse_date)
        work_df = work_df.sort_values("_parsed_date")
    row = work_df.iloc[-1]

    pe_ttm = _safe_float(_row_pick(row, ["pe_ttm", "市盈率ttm", "滚动市盈率", "pe-ttm"]))
    pe_dynamic = _safe_float(_row_pick(row, ["市盈率-动态", "动态市盈率", "pe_dynamic", "动"]))
    pe_static = _safe_float(_row_pick(row, ["静态市盈率", "市盈率", "pe"], exclude=["ttm", "动态", "分位"]))
    return {
        "trade_date": (
            row.get("_parsed_date").date().isoformat()
            if row.get("_parsed_date") is not None
            else _safe_str(_row_pick(row, ["日期", "date", "trade"])) or None
        ),
        "pe_static": pe_static,
        "pe_dynamic": pe_dynamic,
        "pe_ttm": pe_ttm or pe_static or pe_dynamic,
        "pb": _safe_float(_row_pick(row, ["市净率", "pb"])),
        "ps": _safe_float(_row_pick(row, ["市销率", "ps"])),
        "pcf": _safe_float(_row_pick(row, ["市现率", "pcf", "现金流"])),
        "peg": _safe_float(_row_pick(row, ["peg"])),
        "dividend_yield": _safe_float(_row_pick(row, ["股息率", "dv_ratio", "dividend"])),
    }


def _fill_valuation_from_em(symbol: str, payload: dict) -> tuple[dict, Optional[str]]:
    import akshare as ak

    try:
        df = ak.stock_individual_info_em(symbol=_normalize_symbol(symbol), timeout=10)
        if df is None or df.empty:
            return payload, None
        info_map = {str(row.get("item", "")): row.get("value") for _, row in df.iterrows()}
        payload["industry"] = payload.get("industry") or _safe_str(info_map.get("行业")) or None
        payload["pe_dynamic"] = payload.get("pe_dynamic") or _safe_float(info_map.get("市盈率-动态"))
        payload["pe_static"] = payload.get("pe_static") or _safe_float(info_map.get("市盈率-静态"))
        payload["pb"] = payload.get("pb") or _safe_float(info_map.get("市净率"))
        return payload, "stock_individual_info_em"
    except Exception as exc:
        logger.warning(f"[Valuation] EM fallback failed for {symbol}: {exc}")
        return payload, None


def _calc_pe_percentiles(df, current_pe: Optional[float]) -> dict:
    if df is None or df.empty or current_pe is None:
        return {}
    import pandas as pd

    pe_col = _pick_col(df.columns, ["pe_ttm", "市盈率ttm", "滚动市盈率", "pe"], exclude=["分位"])
    if pe_col is None:
        return {}
    date_col = _pick_col(df.columns, ["日期", "date", "trade"])
    work_df = df.copy()
    work_df["_pe"] = pd.to_numeric(work_df[pe_col], errors="coerce")
    work_df = work_df.dropna(subset=["_pe"])
    if work_df.empty:
        return {}
    if date_col is not None:
        work_df["_date"] = pd.to_datetime(work_df[date_col], errors="coerce")
    else:
        work_df["_date"] = pd.NaT

    now = datetime.now()
    result = {}
    for years in (5, 3, 1):
        window = work_df
        if work_df["_date"].notna().any():
            window = work_df[work_df["_date"] >= now - timedelta(days=365 * years)]
        if window.empty:
            continue
        result[f"{years}y"] = round(float((window["_pe"] <= current_pe).sum()) / float(len(window)) * 100, 2)
    return result


def _calc_pe_percentiles_from_em(df, current_pe: Optional[float]) -> dict:
    if df is None or df.empty or current_pe is None or "PE(TTM)" not in df.columns:
        return {}
    import pandas as pd

    work_df = df.copy()
    work_df["_pe"] = pd.to_numeric(work_df["PE(TTM)"], errors="coerce")
    work_df = work_df.dropna(subset=["_pe"])
    if work_df.empty:
        return {}
    date_col = "数据日期" if "数据日期" in work_df.columns else _pick_col(work_df.columns, ["日期", "date"])
    if date_col is not None:
        work_df["_date"] = pd.to_datetime(work_df[date_col], errors="coerce")
    else:
        work_df["_date"] = pd.NaT

    now = datetime.now()
    result = {}
    for years in (5, 3, 1):
        window = work_df
        if work_df["_date"].notna().any():
            window = work_df[work_df["_date"] >= now - timedelta(days=365 * years)]
        if not window.empty:
            result[f"{years}y"] = round(float((window["_pe"] <= current_pe).sum()) / float(len(window)) * 100, 2)
    return result


def _fill_valuation_comparison(symbol: str, payload: dict) -> tuple[dict, Optional[str]]:
    try:
        import akshare as ak

        df = ak.stock_zh_valuation_comparison_em(symbol=_to_em_symbol(_normalize_symbol(symbol)))
        if df is None or df.empty or "代码" not in df.columns:
            return payload, None

        matched = df[df["代码"].astype(str).map(_normalize_symbol) == _normalize_symbol(symbol)]
        if not matched.empty:
            stock_row = matched.iloc[0]
            payload["pe_ttm"] = payload.get("pe_ttm") or _safe_float(stock_row.get("市盈率-TTM"))
            current_year_col = f"市盈率-{str(datetime.now().year)[-2:]}E"
            forecast_pe = _safe_float(stock_row.get(current_year_col))
            if forecast_pe is None:
                forecast_col = next((col for col in stock_row.index if str(col).startswith("市盈率-") and str(col).endswith("E")), None)
                forecast_pe = _safe_float(stock_row.get(forecast_col)) if forecast_col else None
            payload["pe_dynamic"] = payload.get("pe_dynamic") or forecast_pe
            payload["pb"] = payload.get("pb") or _safe_float(stock_row.get("市净率-MRQ"))
            payload["ps"] = payload.get("ps") or _safe_float(stock_row.get("市销率-TTM"))
            payload["pcf"] = payload.get("pcf") or _safe_float(stock_row.get("市现率1-TTM"))
            payload["peg"] = payload.get("peg") or _safe_float(stock_row.get("PEG"))

        avg_df = df[df["代码"].astype(str) == "行业平均"]
        if not avg_df.empty:
            avg_row = avg_df.iloc[0]
            payload["industry_average"] = {
                "industry": "行业平均",
                "pe": _safe_float(avg_row.get("市盈率-TTM")),
                "pb": _safe_float(avg_row.get("市净率-MRQ")),
                "sample_size": max(0, len(df) - 3),
            }
        return payload, "stock_zh_valuation_comparison_em"
    except Exception as exc:
        logger.warning(f"[Valuation] comparison failed for {symbol}: {exc}")
        return payload, None


def _calc_dividend_yield(symbol: str, latest_close: Optional[float]) -> tuple[Optional[float], Optional[str], Optional[str]]:
    if not latest_close or latest_close <= 0:
        return None, None, None
    try:
        import akshare as ak
        import pandas as pd

        df = ak.stock_fhps_detail_em(symbol=_normalize_symbol(symbol))
        if df is None or df.empty:
            return None, None, None
        date_col = "除权除息日" if "除权除息日" in df.columns else _pick_col(df.columns, ["除权", "日期"])
        cash_col = "现金分红-现金分红比例" if "现金分红-现金分红比例" in df.columns else _pick_col(df.columns, ["现金分红", "派息"])
        if cash_col is None:
            return None, None, None
        work_df = df.copy()
        if date_col is not None:
            work_df["_date"] = pd.to_datetime(work_df[date_col], errors="coerce")
        if "方案进度" in work_df.columns:
            work_df = work_df[work_df["方案进度"].astype(str).str.contains("实施", na=False)]
        # 取最近一次实施的分红
        if date_col is not None and not work_df.empty:
            work_df = work_df.sort_values("_date", ascending=False)
        cash_per_10 = pd.to_numeric(work_df[cash_col].iloc[0], errors="coerce") if not work_df.empty else None
        if cash_per_10 is None or pd.isna(cash_per_10):
            return None, None, None
        cash_per_share = float(cash_per_10) / 10.0
        latest_date = work_df["_date"].iloc[0] if date_col is not None else None
        dividend_date = latest_date.strftime("%Y-%m-%d") if latest_date is not None and not pd.isna(latest_date) else None
        return round(cash_per_share / latest_close * 100, 2), dividend_date, "stock_fhps_detail_em"
    except Exception as exc:
        logger.warning(f"[Valuation] dividend yield failed for {symbol}: {exc}")
        return None, None, None


def _fill_industry_average(payload: dict) -> tuple[dict, Optional[str]]:
    industry = payload.get("industry")
    if not industry:
        return payload, None
    try:
        import akshare as ak
        import pandas as pd

        df = ak.stock_board_industry_cons_em(symbol=industry)
        if df is None or df.empty:
            return payload, None
        pe_col = _pick_col(df.columns, ["市盈率-动态", "动态市盈率", "市盈率", "pe"])
        pb_col = _pick_col(df.columns, ["市净率", "pb"])
        pe_values = pd.to_numeric(df[pe_col], errors="coerce").dropna() if pe_col is not None else pd.Series(dtype=float)
        pb_values = pd.to_numeric(df[pb_col], errors="coerce").dropna() if pb_col is not None else pd.Series(dtype=float)
        payload["industry_average"] = {
            "industry": industry,
            "pe": round(float(pe_values.mean()), 2) if not pe_values.empty else None,
            "pb": round(float(pb_values.mean()), 2) if not pb_values.empty else None,
            "sample_size": int(max(len(pe_values), len(pb_values), len(df))),
        }
        return payload, "stock_board_industry_cons_em"
    except Exception as exc:
        logger.warning(f"[Valuation] industry average failed for {industry}: {exc}")
        return payload, None


def _fetch_valuation_ratios(symbol: str, with_history: bool = True) -> dict:
    code = _normalize_symbol(symbol)
    result: dict = {
        "symbol": code,
        "trade_date": None,
        "pe_static": None,
        "pe_dynamic": None,
        "pe_ttm": None,
        "pb": None,
        "ps": None,
        "pcf": None,
        "peg": None,
        "dividend_yield": None,
        "dividend_date": None,
        "pe_percentiles": {},
        "industry_average": {"industry": None, "pe": None, "pb": None, "sample_size": 0},
        "source_chain": [],
        "errors": [],
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
    }
    latest_close = None
    em_df, value_source = _fetch_em_valuation_history(code)
    if em_df is not None:
        value_payload = _extract_em_valuation_latest(em_df)
        latest_close = value_payload.pop("_latest_close", None)
        result.update({k: v for k, v in value_payload.items() if v is not None})
        result["source_chain"].append(value_source)
        if with_history:
            result["pe_percentiles"] = _calc_pe_percentiles_from_em(em_df, result.get("pe_ttm"))
    else:
        df, source = _fetch_lg_valuation(code)
        if df is not None:
            result.update({k: v for k, v in _extract_valuation_latest(df).items() if v is not None})
            result["source_chain"].append(source)
            if with_history:
                result["pe_percentiles"] = _calc_pe_percentiles(df, result.get("pe_ttm"))
        else:
            result["errors"].append("valuation_history_unavailable")

    result, em_source = _fill_valuation_from_em(code, result)
    if em_source:
        result["source_chain"].append(em_source)
    result, comparison_source = _fill_valuation_comparison(code, result)
    if comparison_source:
        result["source_chain"].append(comparison_source)
    if not result.get("industry_average", {}).get("pe"):
        result["industry_average"]["industry"] = result.get("industry")
        result, industry_source = _fill_industry_average(result)
        if industry_source:
            result["source_chain"].append(industry_source)
    dividend_yield, dividend_date, dividend_source = _calc_dividend_yield(code, latest_close)
    if dividend_yield is not None:
        result["dividend_yield"] = dividend_yield
        result["dividend_date"] = dividend_date
    if dividend_source:
        result["source_chain"].append(dividend_source)
    return result


def _fetch_holder_count_from_akshare(symbol: str) -> tuple[dict, Optional[str], list[str]]:
    import akshare as ak

    code = _normalize_symbol(symbol)
    errors: list[str] = []
    try:
        df = ak.stock_zh_a_gdhs_detail_em(symbol=code)
        if df is None or df.empty:
            return {}, None, ["stock_zh_a_gdhs_detail_em:empty"]
        date_col = "股东户数统计截止日" if "股东户数统计截止日" in df.columns else _pick_col(df.columns, ["日期", "截止", "报告期"])
        count_col = "股东户数-本次" if "股东户数-本次" in df.columns else _pick_col(df.columns, ["股东户数", "股东人数", "户数"], exclude=["日期", "截止", "统计"])
        prev_count_col = "股东户数-上次" if "股东户数-上次" in df.columns else None
        change_count_col = "股东户数-增减" if "股东户数-增减" in df.columns else None
        change_col = "股东户数-增减比例" if "股东户数-增减比例" in df.columns else _pick_col(df.columns, ["较上期变化", "环比", "增减比例", "变化比例"])
        work_df = df.copy()
        if date_col is not None:
            work_df["_date"] = work_df[date_col].map(_parse_date)
            work_df = work_df.sort_values("_date")
        latest = work_df.iloc[-1]
        prev = work_df.iloc[-2] if len(work_df) > 1 else None
        count = _safe_int_like(latest.get(count_col)) if count_col is not None else None
        prev_count = (
            _safe_int_like(latest.get(prev_count_col))
            if prev_count_col is not None
            else _safe_int_like(prev.get(count_col)) if prev is not None and count_col is not None else None
        )
        change_count = _safe_int_like(latest.get(change_count_col)) if change_count_col is not None else None
        change_pct = _safe_pct(latest.get(change_col)) if change_col is not None else None
        if change_pct is None and count is not None and prev_count:
            change_pct = round((count - prev_count) / prev_count * 100, 2)
        return {
            "holder_count": count,
            "holder_count_previous": prev_count,
            "holder_count_change": (
                change_count
                if change_count is not None
                else count - prev_count if count is not None and prev_count is not None else None
            ),
            "holder_count_change_pct": change_pct,
            "holder_report_date": (
                latest.get("_date").date().isoformat()
                if latest.get("_date") is not None
                else _safe_str(latest.get(date_col)) if date_col is not None else None
            ),
        }, "stock_zh_a_gdhs_detail_em", errors
    except Exception as exc:
        errors.append(f"stock_zh_a_gdhs_detail_em:{type(exc).__name__}")
        logger.warning(f"[Shareholder] gdhs detail failed for {code}: {exc}")
    return {}, None, errors


def _safe_int_like(val) -> Optional[int]:
    num = _safe_float(val)
    return int(num) if num is not None else None


def _parse_chinese_share_amount(value: Any) -> Optional[float]:
    text = _safe_str(value).replace(",", "")
    if not text:
        return None
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", text)
    if not match:
        return _safe_float(text)
    amount = float(match.group(1))
    if "亿" in text:
        amount *= 1e8
    elif "万" in text:
        amount *= 1e4
    return amount


def _fetch_holder_count_from_tushare(symbol: str) -> tuple[dict, Optional[str], list[str]]:
    import os

    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if not token:
        return {}, None, []
    try:
        import tushare as ts

        pro = ts.pro_api(token)
        df = pro.stk_holdernumber(ts_code=_to_ts_code(symbol))
        if df is None or df.empty:
            return {}, None, ["tushare.stk_holdernumber:empty"]
        date_col = _pick_col(df.columns, ["ann_date", "end_date", "日期", "截止"])
        count_col = _pick_col(df.columns, ["holder_num", "股东户数", "股东人数"])
        work_df = df.copy()
        if date_col is not None:
            work_df["_date"] = work_df[date_col].map(_parse_date)
            work_df = work_df.sort_values("_date")
        latest = work_df.iloc[-1]
        prev = work_df.iloc[-2] if len(work_df) > 1 else None
        count = _safe_int_like(latest.get(count_col)) if count_col is not None else None
        prev_count = _safe_int_like(prev.get(count_col)) if prev is not None and count_col is not None else None
        return {
            "holder_count": count,
            "holder_count_previous": prev_count,
            "holder_count_change": count - prev_count if count is not None and prev_count is not None else None,
            "holder_count_change_pct": (
                round((count - prev_count) / prev_count * 100, 2)
                if count is not None and prev_count else None
            ),
            "holder_report_date": (
                latest.get("_date").date().isoformat()
                if latest.get("_date") is not None
                else _safe_str(latest.get(date_col)) if date_col is not None else None
            ),
        }, "tushare.stk_holdernumber", []
    except Exception as exc:
        logger.warning(f"[Shareholder] tushare holdernumber failed for {symbol}: {exc}")
        return {}, None, [f"tushare.stk_holdernumber:{type(exc).__name__}"]


def _fetch_top10_holders(symbol: str) -> tuple[list[dict], Optional[float], Optional[str], list[str]]:
    import akshare as ak

    errors: list[str] = []
    for date in _latest_quarter_dates(limit=10):
        try:
            df = ak.stock_gdfx_top_10_em(symbol=_to_top_holder_symbol(symbol), date=date)
            if df is None or df.empty:
                continue
            name_col = _pick_col(df.columns, ["股东名称", "名称"])
            pct_col = _pick_col(df.columns, ["持股比例", "占总股本", "比例"])
            amount_col = _pick_col(df.columns, ["持股数量", "持股数", "数量"])
            nature_col = _pick_col(df.columns, ["股东性质", "性质", "类型"])
            change_col = _pick_col(df.columns, ["增减", "变动", "变化"])
            holders: list[dict] = []
            institution_pct = 0.0
            for _, row in df.head(10).iterrows():
                name = _safe_str(row.get(name_col)) if name_col is not None else ""
                nature = _safe_str(row.get(nature_col)) if nature_col is not None else ""
                pct = _safe_pct(row.get(pct_col)) if pct_col is not None else None
                institution_keywords = (
                    "公司", "基金", "银行", "保险", "社保", "QFII", "券商", "信托",
                    "法人", "国有", "机构", "结算", "汇金", "证券金融", "私募"
                )
                if pct is not None and any(k in f"{name}{nature}" for k in institution_keywords):
                    institution_pct += pct
                holders.append({
                    "name": name,
                    "holding_pct": pct,
                    "holding_amount": _safe_float(row.get(amount_col)) if amount_col is not None else None,
                    "holder_type": nature or None,
                    "change": _safe_str(row.get(change_col)) if change_col is not None else None,
                })
            return holders, round(institution_pct, 2), f"stock_gdfx_top_10_em:{date}", errors
        except Exception as exc:
            errors.append(f"stock_gdfx_top_10_em:{date}:{type(exc).__name__}")
            continue
    return [], None, None, errors


def _fetch_holder_changes(symbol: str) -> tuple[list[dict], Optional[str], list[str]]:
    import akshare as ak

    code = _normalize_symbol(symbol)
    errors: list[str] = []
    candidates = [
        ("stock_shareholder_change_ths", {"symbol": code}),
        ("stock_hold_management_detail_em", {}),
    ]
    for func_name, kwargs in candidates:
        fn = getattr(ak, func_name, None)
        if fn is None:
            continue
        try:
            df = fn(**kwargs)
            if df is None or df.empty:
                continue
            code_col = _pick_col(df.columns, ["代码", "证券代码", "股票代码"])
            if code_col is not None:
                df = df[df[code_col].astype(str).map(_normalize_symbol) == code]
            if df.empty:
                continue
            date_col = _pick_col(df.columns, ["变动日期", "公告日期", "日期"])
            holder_col = "变动股东" if "变动股东" in df.columns else _pick_col(df.columns, ["股东名称", "名称", "变动人"])
            direction_col = _pick_col(df.columns, ["变动方向", "增减", "类型", "方向"])
            shares_col = "变动数量" if "变动数量" in df.columns else _pick_col(df.columns, ["变动数量", "变动股数", "数量"])
            pct_col = _pick_col(df.columns, ["变动比例", "占总股本", "比例"])
            price_col = "交易均价" if "交易均价" in df.columns else _pick_col(df.columns, ["均价", "价格"])
            work_df = df.copy()
            if date_col is not None:
                work_df["_date"] = work_df[date_col].map(_parse_date)
                work_df = work_df.sort_values("_date", ascending=False)
            records = []
            for _, row in work_df.head(8).iterrows():
                raw_change = _safe_str(row.get(shares_col)) if shares_col is not None else ""
                direction = _safe_str(row.get(direction_col)) if direction_col is not None else None
                if not direction and raw_change:
                    if "增持" in raw_change:
                        direction = "增持"
                    elif "减持" in raw_change:
                        direction = "减持"
                records.append({
                    "date": (
                        row.get("_date").date().isoformat()
                        if row.get("_date") is not None
                        else _safe_str(row.get(date_col)) if date_col is not None else None
                    ),
                    "holder": _safe_str(row.get(holder_col)) if holder_col is not None else "",
                    "direction": direction,
                    "shares": _parse_chinese_share_amount(raw_change) if raw_change else None,
                    "pct": _safe_pct(row.get(pct_col)) if pct_col is not None else None,
                    "price": _safe_float(row.get(price_col)) if price_col is not None else None,
                })
            return records, func_name, errors
        except Exception as exc:
            errors.append(f"{func_name}:{type(exc).__name__}")
            logger.warning(f"[Shareholder] holder changes {func_name} failed for {code}: {exc}")
    return [], None, errors


def _fetch_actual_controller(symbol: str) -> tuple[Optional[str], Optional[str], list[str]]:
    import akshare as ak

    code = _normalize_symbol(symbol)
    errors: list[str] = []
    try:
        df = ak.stock_hold_control_cninfo(symbol="全部")
        if df is None or df.empty:
            return None, None, ["stock_hold_control_cninfo:empty"]
        code_col = _pick_col(df.columns, ["代码", "证券代码", "股票代码"])
        matched = df
        if code_col is not None:
            matched = df[df[code_col].astype(str).map(_normalize_symbol) == code]
        if matched.empty:
            return None, None, []
        row = matched.iloc[0]
        controller = _safe_str(_row_pick(row, ["实际控制人", "控制人", "控股股东"]))
        return controller or None, "stock_hold_control_cninfo", errors
    except Exception as exc:
        logger.warning(f"[Shareholder] actual controller failed for {code}: {exc}")
        return None, None, [f"stock_hold_control_cninfo:{type(exc).__name__}"]


def _fetch_shareholder_structure(symbol: str) -> dict:
    code = _normalize_symbol(symbol)
    result: dict = {
        "symbol": code,
        "holder_count": None,
        "holder_count_previous": None,
        "holder_count_change": None,
        "holder_count_change_pct": None,
        "holder_report_date": None,
        "top10_holders": [],
        "institution_holding_pct": None,
        "major_holder_changes": [],
        "actual_controller": None,
        "source_chain": [],
        "errors": [],
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
    }

    holder_payload, holder_source, holder_errors = _fetch_holder_count_from_akshare(code)
    if not holder_payload:
        holder_payload, holder_source, tushare_errors = _fetch_holder_count_from_tushare(code)
        holder_errors.extend(tushare_errors)
    result.update(holder_payload)
    result["errors"].extend(holder_errors)
    if holder_source:
        result["source_chain"].append(holder_source)

    holders, institution_pct, top_source, top_errors = _fetch_top10_holders(code)
    result["top10_holders"] = holders
    result["institution_holding_pct"] = institution_pct
    result["errors"].extend(top_errors)
    if top_source:
        result["source_chain"].append(top_source)

    changes, changes_source, changes_errors = _fetch_holder_changes(code)
    result["major_holder_changes"] = changes
    result["errors"].extend(changes_errors)
    if changes_source:
        result["source_chain"].append(changes_source)

    controller, controller_source, controller_errors = _fetch_actual_controller(code)
    result["actual_controller"] = controller
    result["errors"].extend(controller_errors)
    if controller_source:
        result["source_chain"].append(controller_source)
    return result


@router.get("/valuation-ratios", summary="获取估值指标")
def get_valuation_ratios(
    symbol: str = Query(..., description="股票代码，如 600519"),
    with_history: bool = Query(True, description="是否包含历史 PE 分位数"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """获取当前及历史估值指标。"""
    symbol = _normalize_symbol(symbol)
    cache_part = "hist" if with_history else "latest"
    if not force:
        cached = _daily_cache_get(VALUATION_CACHE_KEY, symbol, cache_part)
        if cached:
            cached["_cached"] = True
            return cached
    data = _fetch_valuation_ratios(symbol, with_history=with_history)
    _daily_cache_put(VALUATION_CACHE_KEY, symbol, data, cache_part)
    return data


@router.get("/shareholder-structure", summary="获取股东结构")
def get_shareholder_structure(
    symbol: str = Query(..., description="股票代码，如 600519"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """获取股东人数、前十大股东、机构持股、重要股东增减持与实际控制人。"""
    symbol = _normalize_symbol(symbol)
    if not force:
        cached = _daily_cache_get(SHAREHOLDER_CACHE_KEY, symbol)
        if cached:
            cached["_cached"] = True
            return cached
    data = _fetch_shareholder_structure(symbol)
    _daily_cache_put(SHAREHOLDER_CACHE_KEY, symbol, data)
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


# --- Source 4: 同花顺新版长表（字段补充） ---

_THS_NEW_BALANCE_METRICS = {
    'report_date': 'report_date',
    'total_assets': 'total_assets',
    'total_debt': 'total_liabilities',
    'equity_total': 'total_equity',
    'monetary_fund': 'monetary_funds',
    'accounts_receivable': 'accounts_receivable',
    'inventory': 'inventory',
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
            'noncurrent_liab_1year': None, 'lease_liab': None,
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
        ('同花顺新版资产负债', _fetch_from_ths_new_balance),
        ('同花顺(聚合)', _fetch_from_ths_abstract),
    ]

    used_sources: list[str] = []
    base_loaded = False

    for src_name, src_fn in sources:
        try:
            src_data = src_fn(symbol, periods)
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


# --- Endpoint ---

_fins_lock = None


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
