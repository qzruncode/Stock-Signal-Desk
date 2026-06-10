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
    return f"{CACHE_KEY}:{_normalize_symbol(symbol)}:{datetime.now().strftime('%Y%m%d')}"


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
    code = _normalize_symbol(symbol)
    result: dict = {
        'symbol': code,
        'periods': periods,
        'items': [],
        '_fetched_at': datetime.now().isoformat(),
        '_cached': False,
    }
    errors: list[str] = []

    # Source 1: 同花顺
    try:
        items = _fetch_from_ths(code, periods)
        if items:
            try:
                _enrich_financial_items_with_statements(code, items)
            except Exception as enrich_exc:
                errors.append(f"财报明细补充: {enrich_exc}")
                logger.warning(f"[Financials] statement enrichment failed for {symbol}: {enrich_exc}")
            result['items'] = items
            result['source'] = '同花顺'
            if errors:
                result['_errors'] = errors
            logger.info(f"[Financials] 同花顺 OK for {symbol}: {len(items)} periods, {_time.time() - t0:.1f}s")
            return result
        errors.append("同花顺返回空数据")
    except Exception as e:
        errors.append(f"同花顺: {e}")
        logger.warning(f"[Financials] 同花顺 failed for {symbol}: {e}")

    # Source 2: 新浪财经
    try:
        items = _fetch_from_sina(code, periods)
        if items:
            try:
                _enrich_financial_items_with_statements(code, items)
            except Exception as enrich_exc:
                errors.append(f"财报明细补充: {enrich_exc}")
                logger.warning(f"[Financials] statement enrichment failed for {symbol}: {enrich_exc}")
            result['items'] = items
            result['source'] = '新浪财经'
            if errors:
                result['_errors'] = errors
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
    symbol = _normalize_symbol(symbol)

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


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _build_price_overdraft_signal(payload: dict) -> dict:
    pe_ttm = _safe_float(payload.get("pe_ttm"))
    pe_dynamic = _safe_float(payload.get("pe_dynamic"))
    pb = _safe_float(payload.get("pb"))
    peg = _safe_float(payload.get("peg"))
    dividend_yield = _safe_float(payload.get("dividend_yield"))
    pe_percentiles = payload.get("pe_percentiles") or {}
    industry_average = payload.get("industry_average") or {}
    industry_pe = _safe_float(industry_average.get("pe"))
    industry_pb = _safe_float(industry_average.get("pb"))

    pe_1y = _safe_float(pe_percentiles.get("1y"))
    pe_3y = _safe_float(pe_percentiles.get("3y"))
    pe_5y = _safe_float(pe_percentiles.get("5y"))

    pe_premium_vs_industry = (
        round((pe_ttm - industry_pe) / industry_pe * 100, 2)
        if pe_ttm is not None and industry_pe not in (None, 0)
        else None
    )
    pb_premium_vs_industry = (
        round((pb - industry_pb) / industry_pb * 100, 2)
        if pb is not None and industry_pb not in (None, 0)
        else None
    )
    dynamic_pe_discount_vs_ttm = (
        round((pe_ttm - pe_dynamic) / pe_ttm * 100, 2)
        if pe_ttm not in (None, 0) and pe_dynamic is not None
        else None
    )

    expensive_score = 0.0
    expectation_support_score = 50.0
    signals: list[str] = []
    reasoning: list[str] = []
    limitations: list[str] = []

    percentile_anchor = max(v for v in (pe_1y, pe_3y, pe_5y) if v is not None) if any(
        v is not None for v in (pe_1y, pe_3y, pe_5y)
    ) else None
    if percentile_anchor is not None:
        expensive_score += _clamp((percentile_anchor - 50) * 0.7, 0, 35)
        if percentile_anchor >= 90:
            signals.append("pe_percentile_extremely_high")
            reasoning.append(f"PE 历史分位处于高位（最高分位 {percentile_anchor:.2f}%），说明当前定价接近历史偏贵区间。")
        elif percentile_anchor >= 75:
            signals.append("pe_percentile_high")
            reasoning.append(f"PE 历史分位偏高（最高分位 {percentile_anchor:.2f}%），估值安全边际正在收窄。")
    else:
        limitations.append("缺少足够的历史 PE 分位数据，无法完整评估当前估值所处区间。")

    if pe_premium_vs_industry is not None:
        expensive_score += _clamp(pe_premium_vs_industry * 0.25, 0, 25)
        if pe_premium_vs_industry >= 40:
            signals.append("pe_premium_vs_industry_high")
            reasoning.append(f"当前 PE 相对行业平均溢价 {pe_premium_vs_industry:.2f}%，市场已经计入更高成长预期。")
        elif pe_premium_vs_industry <= -15:
            signals.append("pe_discount_vs_industry")
            reasoning.append(f"当前 PE 低于行业平均 {abs(pe_premium_vs_industry):.2f}%，纯估值层面的透支压力有限。")
    else:
        limitations.append("缺少可比行业 PE，行业相对估值判断不完整。")

    if pb_premium_vs_industry is not None:
        expensive_score += _clamp(pb_premium_vs_industry * 0.12, 0, 10)
        if pb_premium_vs_industry >= 35:
            signals.append("pb_premium_vs_industry_high")
            reasoning.append(f"PB 相对行业也存在 {pb_premium_vs_industry:.2f}% 溢价，说明高定价不只体现在盈利倍数。")
    else:
        limitations.append("缺少可比行业 PB，资产端估值溢价无法充分验证。")

    if dynamic_pe_discount_vs_ttm is not None:
        if dynamic_pe_discount_vs_ttm >= 25:
            expectation_support_score += 20
            signals.append("forward_pe_improving")
            reasoning.append(f"动态 PE 较 TTM 下降 {dynamic_pe_discount_vs_ttm:.2f}%，说明市场预期未来盈利改善能够部分消化高估值。")
        elif dynamic_pe_discount_vs_ttm >= 10:
            expectation_support_score += 10
        elif dynamic_pe_discount_vs_ttm <= 0:
            expectation_support_score -= 15
            signals.append("forward_pe_not_improving")
            reasoning.append("动态 PE 没有明显低于 TTM PE，意味着盈利改善预期对当前高估值的消化能力有限。")
    else:
        limitations.append("缺少动态 PE 或 TTM PE，无法判断未来盈利预期是否显著改善。")

    if peg is not None:
        if peg <= 1:
            expectation_support_score += 20
            signals.append("peg_supportive")
            reasoning.append(f"PEG 为 {peg:.2f}，估值与增长匹配度较好。")
        elif peg <= 1.5:
            expectation_support_score += 5
        elif peg <= 2:
            expectation_support_score -= 10
            signals.append("peg_elevated")
            reasoning.append(f"PEG 为 {peg:.2f}，增长对估值的支撑开始偏弱。")
        else:
            expectation_support_score -= 25
            signals.append("peg_above_2")
            reasoning.append(f"PEG 为 {peg:.2f}，当前估值对增长兑现的要求较高。")
    else:
        limitations.append("缺少 PEG，无法直接衡量估值与增长预期是否匹配。")

    if dividend_yield is not None:
        if dividend_yield >= 3:
            expectation_support_score += 8
            signals.append("dividend_buffer_strong")
        elif dividend_yield < 1:
            expectation_support_score -= 8
            signals.append("dividend_buffer_weak")
    else:
        limitations.append("缺少股息率，无法评估现金回报对高估值的缓冲作用。")

    expensive_score = round(_clamp(expensive_score, 0, 100), 2)
    expectation_support_score = round(_clamp(expectation_support_score, 0, 100), 2)

    evidence_count = sum(
        metric is not None
        for metric in (
            percentile_anchor,
            pe_premium_vs_industry,
            pb_premium_vs_industry,
            dynamic_pe_discount_vs_ttm,
            peg,
            dividend_yield,
        )
    )
    confidence = round(_clamp(evidence_count / 6 * 100, 0, 100), 2)
    overdraft_score = round(_clamp(expensive_score * 0.65 + (100 - expectation_support_score) * 0.35, 0, 100), 2)

    if evidence_count < 2:
        status = "uncertain"
        reasoning.append("可用估值证据较少，当前更适合把结果视作提示信号而非明确结论。")
    elif overdraft_score >= 75:
        status = "high"
    elif overdraft_score >= 55:
        status = "medium"
    elif overdraft_score >= 35:
        status = "watch"
    else:
        status = "low"

    if not reasoning:
        reasoning.append("现有估值与预期信号没有出现明显背离，短期内未观察到强烈的透支特征。")

    return {
        "status": status,
        "score": overdraft_score,
        "confidence": confidence,
        "valuation_expensive_score": expensive_score,
        "expectation_support_score": expectation_support_score,
        "signals": signals,
        "metrics": {
            "pe_ttm": pe_ttm,
            "pe_dynamic": pe_dynamic,
            "pb": pb,
            "peg": peg,
            "dividend_yield": dividend_yield,
            "pe_percentile_1y": pe_1y,
            "pe_percentile_3y": pe_3y,
            "pe_percentile_5y": pe_5y,
            "industry_pe": industry_pe,
            "industry_pb": industry_pb,
            "pe_premium_vs_industry": pe_premium_vs_industry,
            "pb_premium_vs_industry": pb_premium_vs_industry,
            "dynamic_pe_discount_vs_ttm": dynamic_pe_discount_vs_ttm,
        },
        "reasoning": reasoning[:4],
        "limitations": limitations[:4],
    }


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
        "price_overdraft_signal": {},
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
    result["price_overdraft_signal"] = _build_price_overdraft_signal(result)
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
            if not cached.get("price_overdraft_signal"):
                cached["price_overdraft_signal"] = _build_price_overdraft_signal(cached)
            cached["_cached"] = True
            return cached
    data = _fetch_valuation_ratios(symbol, with_history=with_history)
    _daily_cache_put(VALUATION_CACHE_KEY, symbol, data, cache_part)
    return data


@router.get("/price-overdraft-signal", summary="获取股价透支判定信号")
def get_price_overdraft_signal(
    symbol: str = Query(..., description="股票代码，如 600519"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """获取预期校准后的股价透支判定信号。"""
    valuation = get_valuation_ratios(symbol=symbol, with_history=True, force=force)
    return {
        "symbol": valuation.get("symbol"),
        "trade_date": valuation.get("trade_date"),
        "price_overdraft_signal": valuation.get("price_overdraft_signal") or _build_price_overdraft_signal(valuation),
        "source_chain": valuation.get("source_chain", []),
        "errors": valuation.get("errors", []),
        "_fetched_at": valuation.get("_fetched_at"),
        "_cached": valuation.get("_cached", False),
    }


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
    return f"{FINS_STATEMENTS_CACHE_KEY}:{_normalize_symbol(symbol)}:p{periods}:{datetime.now().strftime('%Y%m%d')}"


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


# --- Income Statement ---

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


# --- Source 4: 同花顺新版长表（字段补充） ---

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
        statements = _fetch_financial_statements(symbol, periods)

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
    symbol = _normalize_symbol(symbol)

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


# ============================================================================
# Stock News Search
# ============================================================================

NEWS_CACHE_KEY = "stocks:news:v3:rss_structured"


def _rss_stock_keywords(code: str) -> list[str]:
    keywords = [code]
    try:
        from src.data.stock_index_loader import get_index_stock_name

        stock_name = get_index_stock_name(code)
        if stock_name:
            keywords.append(stock_name)
            normalized_name = stock_name.replace("Ａ", "A").replace("Ｂ", "B")
            if normalized_name != stock_name:
                keywords.append(normalized_name)
    except Exception as exc:
        logger.debug("[RSSHub] stock name lookup failed for %s: %s", code, exc)

    deduped = []
    seen_keywords = set()
    for keyword in keywords:
        value = _safe_str(keyword)
        if value and value not in seen_keywords:
            seen_keywords.add(value)
            deduped.append(value)
    return deduped


def _rss_stock_industry_keywords(code: str) -> list[str]:
    keywords: list[str] = []
    try:
        import akshare as ak

        df = ak.stock_individual_info_em(symbol=code, timeout=10)
        if df is not None and not df.empty:
            info_map = {str(row.get("item", "")): row.get("value") for _, row in df.iterrows()}
            industry = _safe_str(info_map.get("行业"))
            if industry:
                keywords.append(industry)
    except Exception as exc:
        logger.debug("[RSSHub] stock industry lookup failed for %s: %s", code, exc)

    stock_name = next((kw for kw in _rss_stock_keywords(code) if kw != code), "")
    fallback_map = {
        "中金岭南": ["有色金属", "铅锌", "锌", "铅"],
        "贵州茅台": ["白酒", "食品饮料"],
        "平安银行": ["银行"],
        "宁德时代": ["电池", "新能源"],
    }
    keywords.extend(fallback_map.get(stock_name, []))

    deduped: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        value = _safe_str(keyword)
        if value and value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def _normalize_rss_text(value: Any) -> str:
    text = re.sub(r"<[^>]+>", "", _safe_str(value))
    return text.replace("Ａ", "A").replace("Ｂ", "B").upper()


def _rss_entry_matches_keywords(entry: dict, keywords: list[str]) -> bool:
    text = " ".join(
        _normalize_rss_text(entry.get(field))
        for field in ("title", "summary", "author", "link")
    )
    return any(_normalize_rss_text(keyword) in text for keyword in keywords)


def _rss_entry_is_recent(entry: dict, cutoff: datetime) -> bool:
    value = entry.get("published")
    if not value:
        return True
    parsed = _parse_date(value)
    if parsed is None:
        return True
    if getattr(parsed, "tzinfo", None) is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed >= cutoff


def _rss_stock_feed_specs(keywords: list[str]) -> list[tuple[str, dict, str, bool]]:
    feed_specs: list[tuple[str, dict, str, bool]] = []
    for keyword in keywords:
        feed_specs.append((
            "eastmoney_search",
            {"keyword": keyword},
            f"东方财富搜索:{keyword}",
            True,
        ))
    feed_specs.extend([
        ("cls", {"category": "telegraph"}, "财联社电报", True),
        ("cls", {"category": "depth"}, "财联社深度", True),
        ("wallstreetcn_live", {}, "华尔街见闻实时快讯", True),
        ("wallstreetcn", {"category": "shares"}, "华尔街见闻股市", True),
        ("wallstreetcn_hot", {}, "华尔街见闻热门", True),
        ("sina_roll", {"category": "2517"}, "新浪股市滚动", True),
        ("sina_roll", {"category": "2516"}, "新浪财经滚动", True),
        ("sina_finance", {"category": "rollnews"}, "新浪财经频道", True),
        ("yicai", {"category": "brief"}, "第一财经快讯", True),
        ("yicai", {"category": "latest"}, "第一财经最新", True),
        ("yicai", {"category": "news"}, "第一财经新闻", True),
        ("36kr", {"category": "newsflashes"}, "36氪快讯", True),
        ("36kr", {"category": "information/web_news"}, "36氪网页新闻", True),
    ])
    return feed_specs


def _rsshub_is_slow_spec(spec: tuple[str, dict, str, bool]) -> bool:
    source_id, params, _, _ = spec
    category = _safe_str(params.get("category"))
    keyword = _safe_str(params.get("keyword"))

    if source_id == "36kr" and category == "information/web_news":
        return True
    if source_id == "yicai" and category in {"latest", "news"}:
        return True
    if source_id == "sina_finance" and category == "rollnews":
        return True
    if source_id == "cls" and category == "depth":
        return True
    if source_id == "wallstreetcn_hot":
        return True
    if source_id == "eastmoney_search" and keyword and len(keyword) > 6:
        return True
    return False


def _rsshub_enough_entries(entries: list[dict], target: int) -> bool:
    if len(entries) < target:
        return False
    return len(_dedupe_rss_entries(entries)) >= target


def _fetch_rsshub_entries(
    code: str,
    days: int,
    feed_specs: list[tuple[str, dict, str, bool]],
    *,
    keywords: Optional[list[str]] = None,
    limit: int = 50,
    max_workers: int = 8,
    timeout: float = 6.0,
    target_items: int = 8,
) -> tuple[list[dict], list[str], list[str]]:
    errors: list[str] = []
    entries: list[dict] = []
    cutoff = datetime.now() - timedelta(days=days)
    keywords = keywords or _rss_stock_keywords(code)

    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from api.v1.endpoints.rss import _build_feed_url, _fetch_rss_feed

        def _fetch_one(spec: tuple[str, dict, str, bool], phase_timeout: float) -> tuple[str, bool, dict]:
            source_id, params, label, require_match = spec
            feed_url = _build_feed_url(source_id, **params)
            return label, require_match, _fetch_rss_feed(feed_url, limit=limit, timeout=phase_timeout)

        def _collect(specs: list[tuple[str, dict, str, bool]], *, phase_timeout: float, phase_workers: int) -> None:
            if not specs:
                return
            with ThreadPoolExecutor(max_workers=max(1, min(phase_workers, len(specs) or 1))) as pool:
                futures = {pool.submit(_fetch_one, spec, phase_timeout): spec for spec in specs}
                for future in as_completed(futures):
                    _, _, label, _ = futures[future]
                    try:
                        fetched_label, require_match, rss_result = future.result()
                    except Exception as exc:
                        errors.append(f"RSSHub {label}: {exc}")
                        logger.warning("[RSSHub] feed failed for %s/%s: %s", code, label, exc)
                        continue

                    for error in rss_result.get("errors", []) or []:
                        errors.append(f"RSSHub {fetched_label}: {error}")

                    for entry in rss_result.get("items", []) or []:
                        if not _rss_entry_is_recent(entry, cutoff):
                            continue
                        if require_match and not _rss_entry_matches_keywords(entry, keywords):
                            continue
                        entry = dict(entry)
                        entry["_rss_source_id"] = futures[future][0]
                        entry["_rss_source_label"] = fetched_label
                        entries.append(entry)

        fast_specs = [spec for spec in feed_specs if not _rsshub_is_slow_spec(spec)]
        slow_specs = [spec for spec in feed_specs if _rsshub_is_slow_spec(spec)]

        _collect(
            fast_specs,
            phase_timeout=max(1.5, min(timeout, 4.0)),
            phase_workers=max(1, min(max_workers, 4)),
        )
        if slow_specs and not _rsshub_enough_entries(entries, target_items):
            _collect(
                slow_specs,
                phase_timeout=max(1.5, min(timeout, 2.5)),
                phase_workers=max(1, min(max_workers, 2)),
            )
    except Exception as exc:
        errors.append(f"RSSHub 聚合: {exc}")
        logger.warning("[RSSHub] aggregate failed for %s: %s", code, exc)

    return entries, keywords, errors


def _rss_entry_text(entry: dict) -> str:
    return re.sub(r"<[^>]+>", "", _safe_str(entry.get("title") or entry.get("summary")))


def _rss_entry_summary(entry: dict, limit: int = 180) -> str:
    text = re.sub(r"<[^>]+>", "", _safe_str(entry.get("summary")))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _rss_entry_date(entry: dict) -> Optional[datetime]:
    parsed = _parse_date(entry.get("published"))
    if parsed and getattr(parsed, "tzinfo", None) is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def _dedupe_rss_entries(entries: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique: list[dict] = []
    for entry in entries:
        key = re.sub(r"\s+", "", _rss_entry_text(entry)).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(entry)
    unique.sort(key=lambda item: _rss_entry_date(item) or datetime.min, reverse=True)
    return unique


def _fetch_direct_sentiment_sources(symbol: str, days: int) -> tuple[list[dict], list[str]]:
    """Fallback to direct AkShare news/report feeds when RSSHub coverage is empty or too thin."""
    import akshare as ak

    code = _normalize_symbol(symbol)
    cutoff = datetime.now() - timedelta(days=days)
    items: list[dict] = []
    errors: list[str] = []

    try:
        df = ak.stock_news_em(symbol=code)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                pub_time = _parse_date(row.get("发布时间"))
                if pub_time is not None and pub_time < cutoff:
                    continue
                items.append({
                    "title": _safe_str(row.get("新闻标题")),
                    "content": _safe_str(row.get("新闻内容")),
                    "date_str": pub_time.date().isoformat() if pub_time else None,
                    "source": _safe_str(row.get("文章来源")) or "东方财富新闻",
                })
    except Exception as exc:
        errors.append(f"东方财富新闻直连: {exc}")

    try:
        df = ak.stock_research_report_em(symbol=code)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                pub_time = _parse_date(row.get("日期"))
                if pub_time is not None and pub_time < cutoff:
                    continue
                institution = _safe_str(row.get("机构"))
                rating = _safe_str(row.get("东财评级"))
                summary_parts = [part for part in (institution, rating) if part]
                items.append({
                    "title": _safe_str(row.get("报告名称")),
                    "content": "；".join(summary_parts),
                    "date_str": pub_time.date().isoformat() if pub_time else None,
                    "source": institution or "东方财富研报",
                })
    except Exception as exc:
        errors.append(f"东方财富研报直连: {exc}")

    deduped: list[dict] = []
    seen: set[str] = set()
    for item in items:
        key = re.sub(r"\s+", "", f"{item.get('title', '')}|{item.get('date_str', '')}").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped, errors


def _fetch_direct_news_sources(symbol: str, days: int) -> tuple[list[dict], list[str], list[str]]:
    """Direct AkShare fallback for stock news and research reports."""
    import akshare as ak

    code = _normalize_symbol(symbol)
    cutoff = datetime.now() - timedelta(days=days)
    items: list[dict] = []
    errors: list[str] = []
    used_sources: list[str] = []

    try:
        df = ak.stock_news_em(symbol=code)
        if df is not None and not df.empty:
            count = 0
            for _, row in df.iterrows():
                pub_time = _parse_date(row.get("发布时间"))
                if pub_time is not None and pub_time < cutoff:
                    continue
                title = _safe_str(row.get("新闻标题"))
                summary = _safe_str(row.get("新闻内容"))
                structured = _classify_financial_text(f"{title} {summary}")
                items.append({
                    "title": title,
                    "summary": summary,
                    "publish_time": pub_time.isoformat() if pub_time else None,
                    "source": _safe_str(row.get("文章来源")) or "东方财富新闻",
                    "url": _safe_str(row.get("新闻链接")),
                    "category": "新闻",
                    **structured,
                })
                count += 1
            if count > 0:
                used_sources.append(f"东方财富新闻直连({count}条)")
    except Exception as exc:
        errors.append(f"东方财富新闻直连: {exc}")

    try:
        df = ak.stock_research_report_em(symbol=code)
        if df is not None and not df.empty:
            count = 0
            for _, row in df.iterrows():
                pub_time = _parse_date(row.get("日期"))
                if pub_time is not None and pub_time < cutoff:
                    continue
                title = _safe_str(row.get("报告名称"))
                org = _safe_str(row.get("机构"))
                rating = _safe_str(row.get("东财评级"))
                summary_parts = [p for p in [f"机构: {org}", f"评级: {rating}"] if p.strip() not in ("机构: ", "评级: ")]
                structured = _classify_financial_text(f"{title} {'；'.join(summary_parts)}")
                items.append({
                    "title": title,
                    "summary": "；".join(summary_parts) if summary_parts else "",
                    "publish_time": pub_time.isoformat() if pub_time else None,
                    "source": org or "券商研报",
                    "url": _safe_str(row.get("报告PDF链接")),
                    "category": "研报",
                    **structured,
                })
                count += 1
            if count > 0:
                used_sources.append(f"券商研报直连({count}条)")
    except Exception as exc:
        errors.append(f"券商研报直连: {exc}")

    return items, used_sources, errors


def _fetch_rss_research_reports(symbol: str, days: int) -> tuple[list[dict], list[str], list[str]]:
    code = _normalize_symbol(symbol)
    keywords = _rss_stock_keywords(code)
    feed_specs = [
        ("eastmoney_report", {"category": "stock"}, "东方财富个股研报", True),
        ("ulapia", {"category": "stock_research"}, "ulapia个股研报", True),
        ("ulapia", {"category": "brokerage_news"}, "ulapia券商晨报", True),
    ]
    entries, _, errors = _fetch_rsshub_entries(
        code,
        days,
        feed_specs,
        keywords=keywords,
        limit=80,
        max_workers=6,
    )

    items: list[dict] = []
    source_counts: dict[str, int] = {}
    for entry in _dedupe_rss_entries(entries):
        title = _rss_entry_text(entry)
        summary = _rss_entry_summary(entry)
        pub_time = _rss_entry_date(entry)
        label = _safe_str(entry.get("_rss_source_label") or entry.get("author") or "RSSHub研报")
        structured = _classify_financial_text(f"{title} {summary}")
        items.append({
            "title": title,
            "org": _safe_str(entry.get("author")) or label,
            "rating": None,
            "industry": None,
            "publish_date": pub_time.date().isoformat() if pub_time else None,
            "url": _safe_str(entry.get("link")),
            "profit_forecasts": [],
            "monthly_report_count": None,
            **structured,
        })
        source_counts[label] = source_counts.get(label, 0) + 1

    used_sources = [f"RSSHub{label}({count}条)" for label, count in source_counts.items() if count > 0]
    return items, used_sources, errors


def _fetch_rss_announcements(symbol: str, days: int, ann_type: str) -> tuple[list[dict], list[str], list[str]]:
    code = _normalize_symbol(symbol)
    em_symbol = _to_em_symbol(code)
    feed_specs: list[tuple[str, dict, str, bool]] = []
    if em_symbol.startswith("SZ"):
        feed_specs.append(("szse_disclosure", {"stock_code": code}, "深交所公告", False))

    entries, _, errors = _fetch_rsshub_entries(
        code,
        days,
        feed_specs,
        keywords=[],
        limit=100,
        max_workers=2,
        timeout=10.0,
    )

    type_keywords = {
        "业绩": ["业绩", "年报", "半年报", "季报", "报告", "预告", "快报", "修正"],
        "分红": ["分红", "派息", "送转", "权益分派", "利润分配"],
        "增持": ["增持", "回购"],
        "减持": ["减持"],
        "高管变动": ["高管", "董事", "监事", "独立董事", "任职", "辞职", "变更", "聘任"],
    }

    items: list[dict] = []
    source_counts: dict[str, int] = {}
    for entry in _dedupe_rss_entries(entries):
        title = _rss_entry_text(entry)
        if ann_type != "all":
            keywords = type_keywords.get(ann_type, [])
            if keywords and not any(keyword in title for keyword in keywords):
                continue
        pub_time = _rss_entry_date(entry)
        label = _safe_str(entry.get("_rss_source_label") or entry.get("author") or "RSSHub公告")
        items.append({
            "title": title,
            "notice_type": ann_type if ann_type != "all" else "公告",
            "publish_date": pub_time.date().isoformat() if pub_time else None,
            "url": _safe_str(entry.get("link")),
            "source": label,
        })
        source_counts[label] = source_counts.get(label, 0) + 1

    used_sources = [f"RSSHub{label}({count}条)" for label, count in source_counts.items() if count > 0]
    return items, used_sources, errors


def _classify_financial_text(text: str) -> dict:
    normalized = _safe_str(text)
    event_rules = [
        ("earnings", "业绩", ("业绩", "净利润", "营收", "年报", "半年报", "季报", "预告", "快报", "盈利", "亏损")),
        ("capital_action", "资本动作", ("增发", "定增", "发行", "并购", "收购", "重组", "资产", "投资", "募资")),
        ("shareholder", "股东变化", ("股东", "增持", "减持", "回购", "质押", "解押")),
        ("governance", "治理变动", ("董事", "监事", "高管", "总经理", "财务总监", "辞职", "聘任", "变更")),
        ("risk", "风险监管", ("问询", "监管", "处罚", "诉讼", "仲裁", "违规", "退市", "立案", "风险")),
        ("market", "市场交易", ("涨停", "跌停", "大宗交易", "龙虎榜", "主力资金", "净流入", "净流出")),
        ("research", "研究评级", ("研报", "评级", "买入", "增持", "中性", "减持", "卖出", "盈利预测", "目标价")),
        ("industry", "行业主题", ("行业", "板块", "景气", "周期", "需求", "供给", "价格")),
    ]
    event_type = "general"
    event_label = "一般资讯"
    tags: list[str] = []
    for key, label, words in event_rules:
        matched = [word for word in words if word in normalized]
        if matched:
            if event_type == "general":
                event_type = key
                event_label = label
            tags.extend(matched[:3])

    score = _classify_sentiment(normalized) if normalized else 0.0
    polarity = "positive" if score > 0.01 else ("negative" if score < -0.01 else "neutral")
    high_words = ("重大", "终止", "停牌", "复牌", "退市", "处罚", "立案", "亏损", "预增", "预减", "收购", "重组", "分红", "回购")
    medium_words = ("公告", "业绩", "评级", "增持", "减持", "资金", "问询", "诉讼", "投资")
    importance = "high" if any(word in normalized for word in high_words) else (
        "medium" if any(word in normalized for word in medium_words) else "low"
    )
    return {
        "event_type": event_type,
        "event_label": event_label,
        "polarity": polarity,
        "sentiment_score": round(score, 3),
        "importance": importance,
        "tags": list(dict.fromkeys(tags))[:8],
    }


def _build_structured_analysis(items: list[dict], *, days: int, dimension: str, source_key: str = "source") -> dict:
    from collections import Counter, defaultdict

    source_counter = Counter(_safe_str(item.get(source_key)) or "未知" for item in items)
    notice_type_counter = Counter(_safe_str(item.get("notice_type")) or "未分类" for item in items if _safe_str(item.get("notice_type")))
    event_counter = Counter(_safe_str(item.get("event_type")) or _safe_str(item.get("notice_type")) or _safe_str(item.get("category")) or "general" for item in items)
    polarity_counter = Counter(_safe_str(item.get("polarity") or item.get("label")) or "neutral" for item in items)
    importance_counter = Counter(_safe_str(item.get("importance")) or "low" for item in items)
    daily_counter: dict[str, int] = defaultdict(int)
    for item in items:
        date_value = (
            _safe_str(item.get("publish_date"))
            or _safe_str(item.get("publish_time"))[:10]
            or _safe_str(item.get("date_str"))
        )
        if date_value:
            daily_counter[date_value[:10]] += 1

    key_events = [
        {
            "title": item.get("title", ""),
            "date": item.get("publish_date") or _safe_str(item.get("publish_time"))[:10] or item.get("date_str"),
            "source": item.get(source_key) or item.get("org") or "未知",
            "event_type": item.get("event_type") or item.get("notice_type") or item.get("category") or "general",
            "polarity": item.get("polarity") or item.get("label") or "neutral",
            "importance": item.get("importance") or "low",
            "tags": item.get("tags", []),
        }
        for item in items
        if item.get("importance") in ("high", "medium")
    ][:12]

    coverage_level = "none"
    if len(items) >= 20:
        coverage_level = "good"
    elif len(items) >= 8:
        coverage_level = "fair"
    elif items:
        coverage_level = "thin"

    return {
        "dimension": dimension,
        "data_quality": {
            "item_count": len(items),
            "source_count": len(source_counter),
            "days": days,
            "coverage_level": coverage_level,
            "proxy_item_count": sum(1 for item in items if item.get("is_proxy")),
        },
        "source_distribution": dict(source_counter.most_common()),
        "notice_type_distribution": dict(notice_type_counter.most_common()),
        "event_distribution": dict(event_counter.most_common()),
        "polarity_distribution": dict(polarity_counter.most_common()),
        "importance_distribution": dict(importance_counter.most_common()),
        "daily_distribution": dict(sorted(daily_counter.items())),
        "key_events": key_events,
        "ai_summary_hints": [
            f"{dimension}覆盖度: {coverage_level}, 共{len(items)}条, 来源{len(source_counter)}个",
            f"主要事件类型: {', '.join([k for k, _ in event_counter.most_common(3)]) or '无'}",
            f"情绪分布: {dict(polarity_counter.most_common())}",
        ],
    }


def _classify_risk_event(text: str, *, source_kind: str) -> Optional[dict]:
    normalized = _safe_str(text)
    if not normalized:
        return None

    risk_rules = [
        (
            "regulatory",
            "监管处罚",
            {
                "high": ("立案", "处罚", "罚款", "通报批评", "调查", "违规"),
                "medium": ("监管函", "问询", "问询函", "警示函"),
            },
        ),
        (
            "litigation",
            "诉讼仲裁",
            {
                "high": ("冻结", "查封", "执行"),
                "medium": ("诉讼", "仲裁", "被告", "纠纷"),
            },
        ),
        (
            "delisting",
            "退市警示",
            {
                "high": ("退市", "*ST", "终止上市", "暂停上市"),
                "medium": ("风险警示", "ST"),
            },
        ),
        (
            "profit_warning",
            "业绩预警",
            {
                "high": ("预亏", "首亏", "亏损", "商誉减值", "大幅下滑"),
                "medium": ("下修", "减值", "资产减值"),
            },
        ),
        (
            "debt_cashflow",
            "债务现金流",
            {
                "high": ("违约", "无法偿还", "债务逾期", "现金流紧张"),
                "medium": ("债务", "流动性", "票据", "担保", "大额应收"),
            },
        ),
        (
            "pledge_reduction",
            "质押减持",
            {
                "high": ("平仓", "爆仓", "清仓", "被动减持"),
                "medium": ("质押", "减持"),
                "low": ("解除质押",),
            },
        ),
        (
            "governance",
            "治理异动",
            {
                "medium": ("失联", "无法保证", "非标", "保留意见", "否定意见"),
                "low": ("辞职", "更正", "内控", "内部控制"),
            },
        ),
        (
            "operation",
            "经营波动",
            {
                "high": ("事故", "失火", "安全生产"),
                "medium": ("停产", "停工", "召回", "环保"),
            },
        ),
    ]

    severity_rank = {"low": 1, "medium": 2, "high": 3}
    matched: Optional[dict[str, Any]] = None
    for category, label, severity_map in risk_rules:
        rule_hits: list[str] = []
        rule_severity: Optional[str] = None
        for severity in ("high", "medium", "low"):
            words = severity_map.get(severity, ())
            hits = [word for word in words if word in normalized]
            if hits:
                rule_hits.extend(hits)
                if rule_severity is None:
                    rule_severity = severity
        if rule_severity is None:
            continue
        candidate = {
            "risk_category": category,
            "risk_label": label,
            "severity": rule_severity,
            "tags": list(dict.fromkeys(rule_hits))[:5],
        }
        if matched is None or severity_rank[candidate["severity"]] > severity_rank[matched["severity"]]:
            matched = candidate

    if matched is None:
        return None

    return matched




def _fetch_rss_stock_news(code: str, days: int, limit: int = 80) -> tuple[list[dict], list[str], list[str]]:
    """Fetch and aggregate stock news through the project-local RSSHub instance."""
    errors: list[str] = []
    used_sources: list[str] = []
    items: list[dict] = []
    keywords = _rss_stock_keywords(code)

    entries, keywords, fetch_errors = _fetch_rsshub_entries(
        code,
        days,
        _rss_stock_feed_specs(keywords),
        keywords=keywords,
        limit=limit,
    )
    errors.extend(fetch_errors)
    for entry in entries:
        label = _safe_str(entry.get("_rss_source_label", "RSSHub"))
        title = re.sub(r"<[^>]+>", "", _safe_str(entry.get("title")))
        text = f"{title} {_safe_str(entry.get('summary'))}"
        structured = _classify_financial_text(text)
        items.append({
            "title": title,
            "summary": _safe_str(entry.get("summary")),
            "publish_time": entry.get("published"),
            "source": _safe_str(entry.get("author")) or label,
            "url": _safe_str(entry.get("link")),
            "category": "新闻",
            **structured,
            "_rss_source_label": label,
        })

    unique_rss_items = []
    seen_rss = set()
    source_counts: dict[str, int] = {}
    for item in items:
        normalized_title = _normalize_rss_text(item.get("title")).strip()
        key = normalized_title or _safe_str(item.get("url"))
        if key and key in seen_rss:
            continue
        seen_rss.add(key)
        label = _safe_str(item.pop("_rss_source_label", "RSSHub"))
        source_counts[label] = source_counts.get(label, 0) + 1
        unique_rss_items.append(item)
    items = unique_rss_items

    used_sources.extend(
        f"RSSHub{label}({count}条)"
        for label, count in source_counts.items()
        if count > 0
    )

    if items:
        logger.info(
            "[News] RSSHub aggregate OK for %s: %s items, keywords=%s",
            code,
            len(items),
            keywords,
        )

    return items, used_sources, errors


def _fetch_news(symbol: str, days: int, source: str) -> dict:
    """搜索指定股票的相关新闻。

    数据源:
      1. RSSHub 东方财富搜索新闻 (/eastmoney/search/:keyword)
      2. 东方财富个股研报 (stock_research_report_em)
    """
    import time as _time
    import akshare as ak

    t0 = _time.time()
    code = _normalize_symbol(symbol)
    errors: list[str] = []
    news_items: list[dict] = []
    used_sources: list[str] = []

    cutoff = datetime.now() - timedelta(days=days)

    def _is_recent(d) -> bool:
        if d is None:
            return True
        return d >= cutoff

    # -------------------------------------------------------------------
    # Source 1: RSSHub 东方财富搜索新闻
    # -------------------------------------------------------------------
    if source in ("all", "eastmoney", "news"):
        rss_items, rss_sources, rss_errors = _fetch_rss_stock_news(code, days)
        news_items.extend(rss_items)
        used_sources.extend(rss_sources)
        errors.extend(rss_errors)
        if len(rss_items) < 5:
            direct_items, direct_sources, direct_errors = _fetch_direct_news_sources(code, days)
            news_items.extend([item for item in direct_items if item.get("category") == "新闻"])
            used_sources.extend([item for item in direct_sources if "新闻" in item])
            errors.extend(direct_errors)

    # -------------------------------------------------------------------
    # Source 2: 东方财富个股研报
    # -------------------------------------------------------------------
    if source in ("all", "eastmoney", "research"):
        try:
            df = ak.stock_research_report_em(symbol=code)
            if df is not None and not df.empty:
                title_col = _pick_col(df.columns, ["报告名称", "title"])
                org_col = _pick_col(df.columns, ["机构", "org"])
                date_col = _pick_col(df.columns, ["日期", "date"])
                url_col = _pick_col(df.columns, ["报告PDF链接", "url"])
                rating_col = _pick_col(df.columns, ["东财评级", "评级", "rating"])

                research_count = 0
                for _, row in df.iterrows():
                    pub_time = _parse_date(row.get(date_col)) if date_col is not None else None
                    if not _is_recent(pub_time):
                        continue
                    title = _safe_str(row.get(title_col)) if title_col is not None else ""
                    org = _safe_str(row.get(org_col)) if org_col is not None else ""
                    rating = _safe_str(row.get(rating_col)) if rating_col is not None else ""
                    summary_parts = [p for p in [f"机构: {org}", f"评级: {rating}"] if p.strip() not in ("机构: ", "评级: ")]
                    structured = _classify_financial_text(f"{title} {'；'.join(summary_parts)}")
                    news_items.append({
                        "title": title,
                        "summary": "；".join(summary_parts) if summary_parts else "",
                        "publish_time": pub_time.isoformat() if pub_time else None,
                        "source": org or "券商研报",
                        "url": _safe_str(row.get(url_col)) if url_col is not None else "",
                        "category": "研报",
                        **structured,
                    })
                    research_count += 1

                if research_count > 0:
                    used_sources.append(f"券商研报({research_count}条)")
                    logger.info(f"[News] research reports OK for {code}: {research_count} items")
                if research_count < 3:
                    rss_research_items, rss_research_sources, rss_research_errors = _fetch_rss_research_reports(code, days)
                    news_items.extend([
                        {
                            "title": item.get("title", ""),
                            "summary": _safe_str(item.get("event_label") or ""),
                            "publish_time": item.get("publish_date"),
                            "source": item.get("org") or "RSSHub研报",
                            "url": item.get("url") or "",
                            "category": "研报",
                            "event_type": item.get("event_type"),
                            "event_label": item.get("event_label"),
                            "polarity": item.get("polarity"),
                            "importance": item.get("importance"),
                            "tags": item.get("tags", []),
                        }
                        for item in rss_research_items
                    ])
                    used_sources.extend(rss_research_sources)
                    errors.extend(rss_research_errors)
        except Exception as exc:
            errors.append(f"券商研报: {exc}")
            logger.warning(f"[News] research reports failed for {code}: {exc}")
            rss_research_items, rss_research_sources, rss_research_errors = _fetch_rss_research_reports(code, days)
            news_items.extend([
                {
                    "title": item.get("title", ""),
                    "summary": _safe_str(item.get("event_label") or ""),
                    "publish_time": item.get("publish_date"),
                    "source": item.get("org") or "RSSHub研报",
                    "url": item.get("url") or "",
                    "category": "研报",
                    "event_type": item.get("event_type"),
                    "event_label": item.get("event_label"),
                    "polarity": item.get("polarity"),
                    "importance": item.get("importance"),
                    "tags": item.get("tags", []),
                }
                for item in rss_research_items
            ])
            used_sources.extend(rss_research_sources)
            errors.extend(rss_research_errors)

    # 去重 + 按时间倒序
    seen = set()
    unique_items = []
    for item in news_items:
        normalized_title = re.sub(r"<[^>]+>", "", item.get("title") or "")
        normalized_title = normalized_title.replace("Ａ", "A").replace("Ｂ", "B").strip().upper()
        key = normalized_title or (item.get("url") or "")
        if key and key not in seen:
            seen.add(key)
            unique_items.append(item)
    unique_items.sort(
        key=lambda x: x.get("publish_time") or "",
        reverse=True,
    )

    logger.info(f"[News] total {_time.time() - t0:.1f}s for {code}: "
                f"{len(unique_items)} unique items from {used_sources}")
    latest_time = _latest_content_time(unique_items, ["publish_time", "publish_date", "date_str"])

    return {
        "symbol": code,
        "days": days,
        "source": source,
        "items": unique_items,
        "analysis": _build_structured_analysis(unique_items, days=days, dimension="相关新闻"),
        "source_chain": used_sources,
        "errors": errors,
        "data_time": latest_time,
        "is_stale": _content_is_stale(unique_items, days, ["publish_time", "publish_date", "date_str"]),
        "fallback_used": bool(errors),
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
    }


@router.get("/news", summary="搜索相关新闻")
def search_news(
    symbol: str = Query(..., description="股票代码 或 关键词"),
    days: int = Query(90, ge=1, le=90, description="查询最近N天的新闻"),
    source: str = Query("all", description="来源: all | eastmoney | news | research"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """搜索指定股票的相关新闻。

    返回新闻标题、摘要、发布时间、来源、链接、分类。

    数据源:
      - 东方财富个股新闻 (翻页获取，最多50条)
      - 东方财富个股研报 (stock_research_report_em)

    按天缓存。
    """
    symbol = _normalize_symbol(symbol)
    cache_part = f"d{days}:{source}"
    if not force:
        cached = _daily_cache_get(NEWS_CACHE_KEY, symbol, cache_part)
        if cached:
            cached["_cached"] = True
            return cached
    data = _fetch_news(symbol, days, source)
    _daily_cache_put(NEWS_CACHE_KEY, symbol, data, cache_part)
    return data


# ============================================================================
# Company Announcements
# ============================================================================

ANNOUNCEMENTS_CACHE_KEY = "stocks:announcements:v4"


def _fetch_announcements(symbol: str, days: int, ann_type: str) -> dict:
    """获取上市公司公告。

    数据源: akshare.stock_individual_notice_report()
    """
    import time as _time
    import akshare as ak

    t0 = _time.time()
    code = _normalize_symbol(symbol)
    errors: list[str] = []
    items: list[dict] = []

    try:
        end_date = datetime.now().strftime("%Y-%m-%d")
        begin_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        df = ak.stock_individual_notice_report(
            security=code,
            symbol="全部",
            begin_date=begin_date,
            end_date=end_date,
        )
        if df is not None and not df.empty:
            title_col = _pick_col(df.columns, ["公告标题", "title"])
            type_col = _pick_col(df.columns, ["公告类型", "type"])
            date_col = _pick_col(df.columns, ["公告日期", "date"])
            url_col = _pick_col(df.columns, ["网址", "url"])

            # 类型关键词映射
            TYPE_KEYWORDS = {
                "业绩": ["业绩", "年报", "半年报", "季报", "报告", "预告", "快报", "修正"],
                "分红": ["分红", "派息", "送转", "权益分派", "利润分配"],
                "增持": ["增持", "回购"],
                "减持": ["减持"],
                "高管变动": ["高管", "董事", "监事", "独立董事", "任职", "辞职", "变更", "聘任"],
            }

            for _, row in df.iterrows():
                pub_time = _parse_date(row.get(date_col)) if date_col is not None else None
                title = _safe_str(row.get(title_col)) if title_col is not None else ""
                notice_type = _safe_str(row.get(type_col)) if type_col is not None else ""

                # 类型过滤
                if ann_type != "all":
                    keywords = TYPE_KEYWORDS.get(ann_type, [])
                    if not any(kw in title or kw in notice_type for kw in keywords):
                        continue

                items.append({
                    "title": title,
                    "notice_type": notice_type,
                    "publish_date": pub_time.date().isoformat() if pub_time else None,
                    "url": _safe_str(row.get(url_col)) if url_col is not None else "",
                    **_classify_financial_text(f"{notice_type} {title}"),
                })

            logger.info(f"[Announcements] OK for {code}: {len(items)} items (type={ann_type})")
    except Exception as exc:
        errors.append(f"公司公告: {exc}")
        logger.warning(f"[Announcements] failed for {code}: {exc}")

    if len(items) < 3:
        rss_items, rss_sources, rss_errors = _fetch_rss_announcements(code, days, ann_type)
        items.extend(rss_items)
        errors.extend(rss_errors)
        if rss_sources:
            source_chain = rss_sources
        else:
            source_chain = []
    else:
        source_chain = []

    deduped: list[dict] = []
    seen: set[str] = set()
    for item in items:
        key = re.sub(r"\s+", "", f"{item.get('title', '')}|{item.get('publish_date', '')}").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    deduped.sort(key=lambda item: item.get("publish_date") or "", reverse=True)

    return {
        "symbol": code,
        "days": days,
        "type": ann_type,
        "items": deduped,
        "analysis": _build_structured_analysis(deduped, days=days, dimension="公司公告"),
        "source_chain": source_chain,
        "errors": errors,
        "data_time": _latest_content_time(deduped, ["publish_date"]),
        "is_stale": _content_is_stale(deduped, days, ["publish_date"]),
        "fallback_used": bool(errors or source_chain),
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
    }


@router.get("/announcements", summary="获取公司公告")
def get_announcements(
    symbol: str = Query(..., description="股票代码"),
    days: int = Query(90, ge=1, le=365, description="查询最近N天"),
    type: str = Query("all", description="公告类型: all | 业绩 | 分红 | 增持 | 减持 | 高管变动"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """获取上市公司正式公告。

    返回公告标题、公告日期、公告类型、链接。

    数据源: RSSHub 交易所披露路由。

    按天缓存。
    """
    symbol = _normalize_symbol(symbol)
    cache_part = f"d{days}:{type}"
    if not force:
        cached = _daily_cache_get(ANNOUNCEMENTS_CACHE_KEY, symbol, cache_part)
        if cached:
            cached["_cached"] = True
            return cached
    data = _fetch_announcements(symbol, days, type)
    _daily_cache_put(ANNOUNCEMENTS_CACHE_KEY, symbol, data, cache_part)
    return data


# ============================================================================
# Risk Events
# ============================================================================

RISK_EVENTS_CACHE_KEY = "stocks:risk_events:v4"

RISK_KEYWORDS: tuple[str, ...] = (
    "减持",
    "质押",
    "冻结",
    "诉讼",
    "仲裁",
    "问询函",
    "监管函",
    "立案",
    "处罚",
    "资产减值",
    "商誉减值",
    "业绩预告下修",
    "募投延期",
    "关联交易",
    "大额应收",
    "债务逾期",
    "担保",
    "退市风险",
)

RISK_CATEGORY_LABELS: dict[str, str] = {
    "regulatory": "监管处罚",
    "litigation": "诉讼仲裁",
    "delisting": "退市警示",
    "profit_warning": "业绩预警",
    "debt_cashflow": "债务现金流",
    "pledge_reduction": "质押减持",
    "governance": "治理异动",
    "operation": "经营波动",
}

RISK_LABEL_TO_CATEGORY: dict[str, str] = {
    label: category for category, label in RISK_CATEGORY_LABELS.items()
}


def _match_risk_keywords(text: str) -> list[str]:
    normalized = _safe_str(text)
    return [kw for kw in RISK_KEYWORDS if kw in normalized]


def _extract_risk_summary(content: str, keywords: list[str]) -> str:
    normalized = _safe_str(content)
    if not normalized or not keywords:
        return ""
    sentences = re.split(r"[。！？；\n\r]+", normalized)
    matched = []
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if any(keyword in sentence for keyword in keywords):
            matched.append(sentence)
        if len(matched) >= 3:
            break
    return "；".join(matched)[:240]


def _fetch_risk_body_if_needed(url: str, title: str, matched_keywords: list[str]) -> tuple[str, str]:
    if not url or not matched_keywords:
        return "", ""
    try:
        from src.search_service import fetch_url_content

        content = fetch_url_content(url, timeout=8)
        summary = _extract_risk_summary(content, matched_keywords)
        return content[:1500], summary
    except Exception as exc:
        logger.warning("[RiskEvents] fetch body failed for %s: %s", title, exc)
        return "", ""


def _build_risk_events(symbol: str, days: int) -> dict:
    code = _normalize_symbol(symbol)
    news = _fetch_news(code, days, "all")
    announcements = _fetch_announcements(code, days, "all")

    items: list[dict] = []
    risk_counter: dict[str, int] = {}
    severity_counter = {"high": 0, "medium": 0, "low": 0}

    for item in news.get("items", []):
        title = _safe_str(item.get("title"))
        title_keywords = _match_risk_keywords(title)
        text = " ".join(
            filter(
                None,
                [
                    title,
                    _safe_str(item.get("summary")),
                    _safe_str(item.get("event_label")),
                    " ".join(item.get("tags", []) or []),
                ],
            )
        )
        risk = _classify_risk_event(text, source_kind="news")
        if not title_keywords and not risk:
            continue
        body_content, risk_summary = _fetch_risk_body_if_needed(item.get("url") or "", title, title_keywords)
        body_keywords = _match_risk_keywords(body_content)
        merged_keywords = list(dict.fromkeys(title_keywords + body_keywords + (risk["tags"] if risk else [])))
        risk = risk or _classify_risk_event(body_content, source_kind="news")
        if not risk:
            continue
        severity_counter[risk["severity"]] = severity_counter.get(risk["severity"], 0) + 1
        risk_counter[risk["risk_label"]] = risk_counter.get(risk["risk_label"], 0) + 1
        items.append({
            "title": title,
            "summary": item.get("summary"),
            "risk_summary": risk_summary or _extract_risk_summary(_safe_str(item.get("summary")), merged_keywords),
            "date": _safe_str(item.get("publish_time"))[:10] or None,
            "source": item.get("source") or "新闻",
            "source_type": "news",
            "url": item.get("url") or "",
            "severity": risk["severity"],
            "risk_category": risk["risk_category"],
            "risk_label": risk["risk_label"],
            "event_type": item.get("event_type") or "general",
            "tags": merged_keywords[:8],
        })

    for item in announcements.get("items", []):
        title = _safe_str(item.get("title"))
        title_keywords = _match_risk_keywords(title)
        text = " ".join(
            filter(
                None,
                [
                    _safe_str(item.get("notice_type")),
                    title,
                    _safe_str(item.get("event_label")),
                    " ".join(item.get("tags", []) or []),
                ],
            )
        )
        risk = _classify_risk_event(text, source_kind="announcement")
        if not title_keywords and not risk:
            continue
        body_content, risk_summary = _fetch_risk_body_if_needed(item.get("url") or "", title, title_keywords)
        body_keywords = _match_risk_keywords(body_content)
        merged_keywords = list(dict.fromkeys(title_keywords + body_keywords + (risk["tags"] if risk else [])))
        risk = risk or _classify_risk_event(body_content, source_kind="announcement")
        if not risk:
            continue
        severity_counter[risk["severity"]] = severity_counter.get(risk["severity"], 0) + 1
        risk_counter[risk["risk_label"]] = risk_counter.get(risk["risk_label"], 0) + 1
        items.append({
            "title": title,
            "summary": item.get("notice_type") or "",
            "risk_summary": risk_summary or _extract_risk_summary(_safe_str(item.get("notice_type")), merged_keywords),
            "date": item.get("publish_date"),
            "source": item.get("source") or "公司公告",
            "source_type": "announcement",
            "url": item.get("url") or "",
            "severity": risk["severity"],
            "risk_category": risk["risk_category"],
            "risk_label": risk["risk_label"],
            "event_type": item.get("event_type") or "announcement",
            "tags": merged_keywords[:8],
        })

    deduped: list[dict] = []
    seen: set[str] = set()
    for item in sorted(items, key=lambda row: row.get("date") or "", reverse=True):
        key = re.sub(r"\s+", "", f"{item.get('title', '')}|{item.get('date', '')}|{item.get('risk_category', '')}").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    top_risk_labels = [label for label, _ in sorted(risk_counter.items(), key=lambda pair: pair[1], reverse=True)[:5]]
    source_type_counter = {"news": 0, "announcement": 0}
    for item in deduped:
        source_type = _safe_str(item.get("source_type")).lower()
        if source_type in source_type_counter:
            source_type_counter[source_type] += 1

    analysis = {
        "total_events": len(deduped),
        "severity_distribution": severity_counter,
        "source_distribution": source_type_counter,
        "top_risk_labels": top_risk_labels,
        "high_severity_titles": [item["title"] for item in deduped if item.get("severity") == "high"][:8],
        "ai_summary_hints": [
            f"近{days}天共发现 {len(deduped)} 条风险线索",
            f"高风险事件 {severity_counter['high']} 条, 中风险事件 {severity_counter['medium']} 条",
            f"主要风险主题: {', '.join(top_risk_labels) or '暂无明显风险主题'}",
        ],
    }

    return {
        "symbol": code,
        "days": days,
        "items": deduped[:50],
        "analysis": analysis,
        "source_chain": list(dict.fromkeys((news.get("source_chain") or []) + (announcements.get("source_chain") or []))),
        "errors": list(dict.fromkeys((news.get("errors") or []) + (announcements.get("errors") or []))),
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
    }


@router.get("/risk-events", summary="获取风险事件")
def get_risk_events(
    symbol: str = Query(..., description="股票代码"),
    days: int = Query(90, ge=1, le=365, description="查询最近N天"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """聚合相关新闻和公司公告中的风险事件。"""
    symbol = _normalize_symbol(symbol)
    cache_part = f"d{days}"
    if not force:
        cached = _daily_cache_get(RISK_EVENTS_CACHE_KEY, symbol, cache_part)
        if cached:
            cached["_cached"] = True
            return cached
    data = _build_risk_events(symbol, days)
    _daily_cache_put(RISK_EVENTS_CACHE_KEY, symbol, data, cache_part)
    return data


# ============================================================================
# Sentiment Analysis
# ============================================================================

SENTIMENT_CACHE_KEY = "stocks:sentiment:v3:rss_structured"

# Chinese financial sentiment keywords
_SENTIMENT_POSITIVE = {
    '增长', '上升', '突破', '新高', '利好', '看好', '受益', '回暖',
    '复苏', '改善', '提升', '盈利', '超额', '领先', '强劲', '大涨', '涨停',
    '增持', '回购', '分红', '超预期', '创新高', '景气', '放量', '优化',
    '成功', '签约', '合作', '扩张', '布局', '亮眼', '丰厚', '稳健',
    '持续增长', '净流入', '上涨', '反弹', '拉升', '领涨', '跑赢',
    '提振', '兑现', '创纪录', '高景气', '高增长', '强势',
}

_SENTIMENT_NEGATIVE = {
    '下跌', '暴跌', '亏损', '下滑', '减持', '套牢', '风险',
    '警告', '违规', '处罚', '退市', '爆雷', '缩水', '承压',
    '低迷', '恶化', '诉讼', '违约', '暴雷', '跌停', '腰斩', '清仓',
    '解禁', '停牌', '摘牌', '造假', '欺诈', '质疑', '压力', '下行',
    '净流出', '流出', '撤离', '利空', '拖累', '受挫', '遇阻', '受阻',
    '负增长', '大幅下滑', '大幅下跌', '不及预期', '低于预期',
}

_SENTIMENT_AMPLIFIER = {
    '大幅', '暴涨', '暴跌', '严重', '超预期', '远超', '显著', '急剧',
    '远低于', '远高于', '超预期', '不及预期', '低于预期', '远低于预期',
}


def _classify_sentiment(text: str) -> float:
    """Classify sentiment of a single text. Returns score in [-1, 1]."""
    try:
        import jieba
    except ImportError:
        jieba = None

    if jieba is not None:
        words = list(jieba.cut(text))
    else:
        words = [text[i:i+2] for i in range(len(text)-1)]

    pos = sum(1 for w in words if w in _SENTIMENT_POSITIVE)
    neg = sum(1 for w in words if w in _SENTIMENT_NEGATIVE)
    amp = sum(1 for w in words if w in _SENTIMENT_AMPLIFIER)

    total = pos + neg
    if total == 0:
        return 0.0  # neutral

    raw = (pos - neg) / total
    if amp > 0:
        raw = max(-1.0, min(1.0, raw * 1.3))
    return raw


def _fetch_sentiment(symbol: str, days: int) -> dict:
    """分析市场对某股票的情绪倾向。

    数据源: RSSHub 聚合财经资讯 + 个股研报
    方法: 中文分词 + 金融情绪词典匹配
    """
    import time as _time

    t0 = _time.time()
    code = _normalize_symbol(symbol)
    errors: list[str] = []
    keywords = _rss_stock_keywords(code)
    feed_specs = _rss_stock_feed_specs(keywords) + [
        ("eastmoney_report", {"category": "stock"}, "东方财富个股研报", True),
        ("ulapia", {"category": "stock_research"}, "ulapia个股研报", True),
        ("ulapia", {"category": "brokerage_news"}, "ulapia券商晨报", True),
    ]
    entries, _, fetch_errors = _fetch_rsshub_entries(
        code,
        days,
        feed_specs,
        keywords=keywords,
        limit=100,
        max_workers=10,
    )
    errors.extend(fetch_errors)

    unique_items = []
    for entry in _dedupe_rss_entries(entries):
        pub_time = _rss_entry_date(entry)
        unique_items.append({
            "title": _rss_entry_text(entry),
            "content": _rss_entry_summary(entry),
            "date_str": pub_time.date().isoformat() if pub_time else None,
            "source": _safe_str(entry.get("_rss_source_label") or entry.get("author") or "RSSHub"),
        })

    if len(unique_items) < 5:
        direct_items, direct_errors = _fetch_direct_sentiment_sources(code, days)
        errors.extend(direct_errors)
        existing_keys = {
            re.sub(r"\s+", "", f"{item.get('title', '')}|{item.get('date_str', '')}").lower()
            for item in unique_items
        }
        for item in direct_items:
            key = re.sub(r"\s+", "", f"{item.get('title', '')}|{item.get('date_str', '')}").lower()
            if key and key not in existing_keys:
                existing_keys.add(key)
                unique_items.append(item)

    # 情感分析
    positive_count = 0
    negative_count = 0
    neutral_count = 0
    daily_counts: dict[str, dict] = {}
    sentiment_items: list[dict] = []

    for item in unique_items:
        combined = (item.get("title") or "") + " " + (item.get("content") or "")
        score = _classify_sentiment(combined)

        if score > 0.01:
            label = "positive"
            positive_count += 1
        elif score < -0.01:
            label = "negative"
            negative_count += 1
        else:
            label = "neutral"
            neutral_count += 1

        # Date for trend
        date_str = item.get("date_str")  # use pre-extracted date
        if not date_str:
            for part in [item.get("title") or "", item.get("content") or ""]:
                m = re.search(r'(\d{4}-\d{2}-\d{2})', part)
                if m:
                    date_str = m.group(1)
                    break

        if date_str:
            daily = daily_counts.setdefault(date_str, {"total": 0, "positive": 0, "negative": 0, "neutral": 0})
            daily["total"] += 1
            daily[label] += 1

        sentiment_items.append({
            "title": item.get("title", ""),
            "sentiment_score": round(score, 3),
            "label": label,
            "source": item.get("source", ""),
            **_classify_financial_text(combined),
        })

    # Overall sentiment score (-100 to +100)
    total_classified = positive_count + negative_count + neutral_count
    if total_classified > 0:
        overall = ((positive_count - negative_count) / total_classified) * 100
    else:
        overall = 0.0

    daily_trend = [
        {"date": k, "total": v["total"], "positive": v["positive"],
         "negative": v["negative"], "neutral": v["neutral"]}
        for k, v in sorted(daily_counts.items())
    ]

    # Top keywords
    try:
        import jieba
        from collections import Counter
        all_text = " ".join(i.get("title") or "" for i in unique_items)
        stop_words = {
            '的', '了', '是', '在', '和', '与', '或', '但', '而', '对', '于',
            '中', '上', '下', '到', '将', '以', '被', '由', '从', '向', '个',
            '年', '月', '日', '一', '这', '那', '他', '她', '它', '们',
            '等', '并', '各', '已', '仍', '再', '又', '也', '还',
        }
        words = [w for w in jieba.cut(all_text) if len(w) >= 2 and w not in stop_words]
        top_keywords = [w for w, _ in Counter(words).most_common(20)]
    except Exception:
        top_keywords = []

    logger.info(f"[Sentiment] total {_time.time() - t0:.1f}s for {code}: "
                f"{len(unique_items)} items, score={overall:.1f}")
    latest_time = _latest_content_time(unique_items, ["date_str"])

    return {
        "symbol": code,
        "days": days,
        "sentiment_score": round(overall, 1),
        "positive_count": positive_count,
        "negative_count": negative_count,
        "neutral_count": neutral_count,
        "daily_trend": daily_trend,
        "top_keywords": top_keywords,
        "items": sentiment_items[:50],
        "analysis": _build_structured_analysis(sentiment_items, days=days, dimension="舆情情绪"),
        "errors": errors,
        "data_time": latest_time,
        "is_stale": _content_is_stale(unique_items, days, ["date_str"]),
        "fallback_used": bool(errors),
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
    }


@router.get("/sentiment", summary="获取舆情情绪")
def get_sentiment(
    symbol: str = Query(..., description="股票代码"),
    days: int = Query(90, ge=1, le=90, description="分析最近N天"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """分析市场对某股票的情绪倾向。

    返回舆情分数（-100到+100）、正/负/中性新闻条数、
    讨论热度趋势、关键词、逐条情绪标注。

    数据源: RSSHub 聚合财经资讯 + 个股研报
    方法: 中文分词 + 金融情绪词典匹配

    按天缓存。
    """
    symbol = _normalize_symbol(symbol)
    cache_part = f"d{days}"
    if not force:
        cached = _daily_cache_get(SENTIMENT_CACHE_KEY, symbol, cache_part)
        if cached:
            cached["_cached"] = True
            return cached
    data = _fetch_sentiment(symbol, days)
    _daily_cache_put(SENTIMENT_CACHE_KEY, symbol, data, cache_part)
    return data


# ============================================================================
# Research Reports (券商研报)
# ============================================================================

RESEARCH_CACHE_KEY = "stocks:research_report:v5"


def _fetch_research_reports(symbol: str, days: int) -> dict:
    """获取券商对公司的最新研究报告摘要。

    数据源: akshare.stock_research_report_em()
    """
    import time as _time
    import akshare as ak

    t0 = _time.time()
    code = _normalize_symbol(symbol)
    errors: list[str] = []
    cutoff = datetime.now() - timedelta(days=days)
    items: list[dict] = []

    try:
        df = ak.stock_research_report_em(symbol=code)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                pub_time = _parse_date(row.get("日期"))
                if pub_time and pub_time < cutoff:
                    continue

                # Extract analyst info from title (some reports include analyst names)
                title = _safe_str(row.get("报告名称"))
                org = _safe_str(row.get("机构"))
                rating = _safe_str(row.get("东财评级"))
                url = _safe_str(row.get("报告PDF链接"))
                industry = _safe_str(row.get("行业"))

                # Build profit forecast summary
                forecasts = []
                for year_key, label in [
                    ("2026-盈利预测-收益", "2026"),
                    ("2027-盈利预测-收益", "2027"),
                    ("2028-盈利预测-收益", "2028"),
                ]:
                    eps = _safe_float(row.get(year_key))
                    pe = _safe_float(row.get(year_key.replace("-收益", "-市盈率")))
                    if eps is not None:
                        forecasts.append({
                            "year": label,
                            "eps": eps,
                            "pe": pe,
                        })

                items.append({
                    "title": title,
                    "org": org,
                    "rating": rating,
                    "industry": industry,
                    "publish_date": pub_time.date().isoformat() if pub_time else None,
                    "url": url,
                    "profit_forecasts": forecasts,
                    "monthly_report_count": _safe_int_like(row.get("近一月个股研报数")),
                })

            logger.info(f"[Research] OK for {code}: {len(items)} items (days={days})")
    except Exception as exc:
        errors.append(f"券商研报: {exc}")
        logger.warning(f"[Research] failed for {code}: {exc}")

    used_sources: list[str] = [f"东方财富研报直连({len(items)}条)"] if items else []
    if len(items) < 3:
        rss_items, rss_sources, rss_errors = _fetch_rss_research_reports(code, days)
        errors.extend(rss_errors)
        existing_keys = {
            re.sub(r"\s+", "", f"{item.get('title', '')}|{item.get('publish_date', '')}").lower()
            for item in items
        }
        for item in rss_items:
            key = re.sub(r"\s+", "", f"{item.get('title', '')}|{item.get('publish_date', '')}").lower()
            if key and key not in existing_keys:
                existing_keys.add(key)
                items.append(item)
        used_sources.extend(rss_sources)

    return {
        "symbol": code,
        "days": days,
        "items": items,
        "analysis": _build_structured_analysis(items, days=days, dimension="券商研报", source_key="org"),
        "source_chain": used_sources,
        "errors": errors,
        "data_time": _latest_content_time(items, ["publish_date"]),
        "is_stale": _content_is_stale(items, days, ["publish_date"]),
        "fallback_used": bool(errors or len(used_sources) > 1 or (used_sources and not used_sources[0].startswith("东方财富"))),
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
    }


@router.get("/research-report", summary="获取券商研报")
def get_research_report(
    symbol: str = Query(..., description="股票代码"),
    days: int = Query(1095, ge=1, le=1095, description="查询最近N天"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """获取券商对公司的最新研究报告摘要。

    返回券商名称、评级、目标价、盈利预测、研报标题。

    数据源: RSSHub 东方财富研报 + ulapia 研报。

    按天缓存。
    """
    symbol = _normalize_symbol(symbol)
    cache_part = f"d{days}"
    if not force:
        cached = _daily_cache_get(RESEARCH_CACHE_KEY, symbol, cache_part)
        if cached:
            cached["_cached"] = True
            return cached
    data = _fetch_research_reports(symbol, days)
    _daily_cache_put(RESEARCH_CACHE_KEY, symbol, data, cache_part)
    return data


# ============================================================================
# Social Media Sentiment (社交媒体情绪)
# ============================================================================

SOCIAL_SENTIMENT_CACHE_KEY = "stocks:social_sentiment:v4"


def _latest_content_time(items: list[dict], keys: list[str]) -> str | None:
    latest: datetime | None = None
    for item in items:
        for key in keys:
            raw = item.get(key)
            if not raw:
                continue
            parsed = _parse_date(raw)
            if parsed is not None and (latest is None or parsed > latest):
                latest = parsed
                break
    return latest.isoformat() if latest is not None else None


def _content_is_stale(items: list[dict], max_age_days: int, keys: list[str]) -> bool:
    latest = _latest_content_time(items, keys)
    if not latest:
        return True
    parsed = _parse_date(latest)
    if parsed is None:
        return True
    return parsed < (datetime.now() - timedelta(days=max_age_days))


def _fetch_social_sentiment(symbol: str, days: int) -> dict:
    """获取社交媒体讨论热度和情绪。

    数据源:
      1. 东方财富股吧 — 帖子列表 (标题+阅读+评论)
      2. 东方财富千股千评 — 每日评分趋势
      3. jieba 分词 + 金融情绪词典 — NLP 情绪分析
    """
    import time as _time
    import re as _re
    import requests as _requests
    import akshare as ak

    t0 = _time.time()
    code = _normalize_symbol(symbol)
    errors: list[str] = []
    cutoff = datetime.now() - timedelta(days=days)

    def _is_recent(d) -> bool:
        if d is None:
            return True
        return d >= cutoff

    # ----------------------------------------------------------------
    # 1. 东方财富股吧帖子
    # ----------------------------------------------------------------
    guba_items: list[dict] = []
    try:
        url = f"https://guba.eastmoney.com/list,{code},1,f.html"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        }
        r = _requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            # 提取帖子: 阅读数、评论数、post_id、标题、更新时间
            pattern = (
                r'<div class="read">(\d+)</div>.*?'
                r'<div class="reply">(\d+)</div>.*?'
                r'<a data-postid="(\d+)" href="/news,\w+,(\d+)\.html">(.*?)</a>.*?'
                r'<div class="update">(.*?)</div>'
            )
            posts = _re.findall(pattern, r.text, _re.DOTALL)
            for p in posts:
                read_count, reply_count, post_id, _, title, update_time = p
                title = _safe_str(title)

                # 解析日期 (格式: "06-01 08:00" 或 "2025-12-31")
                date_match = _re.match(r'(\d{2})-(\d{2})\s+(\d{2}):(\d{2})', update_time)
                if date_match:
                    month, day, hour, minute = date_match.groups()
                    year = datetime.now().year
                    pub_dt = datetime(year, int(month), int(day), int(hour), int(minute))
                    if pub_dt > datetime.now():
                        pub_dt = pub_dt.replace(year=year - 1)
                else:
                    pub_dt = _parse_date(update_time)

                # 股吧帖子本来就少，不过滤时间，全部返回
                # if pub_dt and pub_dt < cutoff:
                #     continue

                # NLP 情绪分析
                score = _classify_sentiment(title)
                label = "positive" if score > 0.01 else ("negative" if score < -0.01 else "neutral")

                guba_items.append({
                    "title": title,
                    "content": "",
                    "source": "股吧",
                    "url": f"https://guba.eastmoney.com/news,{code},{post_id}.html",
                    "read_count": int(read_count),
                    "reply_count": int(reply_count),
                    "sentiment_score": round(score, 3),
                    "label": label,
                    "publish_time": pub_dt.isoformat() if pub_dt else None,
                })
            logger.info(f"[SocialSentiment] Guba OK for {code}: {len(guba_items)} posts")
    except Exception as exc:
        errors.append(f"股吧: {exc}")
        logger.warning(f"[SocialSentiment] Guba failed for {code}: {exc}")

    # ----------------------------------------------------------------
    # 2. 千股千评历史评分
    # ----------------------------------------------------------------
    score_trend: list[dict] = []
    current_score: float | None = None
    try:
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "filter": f'(SECURITY_CODE="{code}")',
            "columns": "ALL",
            "source": "WEB",
            "client": "WEB",
            "reportName": "RPT_STOCK_HISTORYMARK",
            "sortColumns": "DIAGNOSE_DATE",
            "sortTypes": "1",
        }
        r = _requests.get(url, params=params, timeout=15)
        data = r.json()
        if data.get("result") and data["result"].get("data"):
            rows = data["result"]["data"]
            for row in rows:
                raw_date = row.get("DIAGNOSE_DATE", "")
                date_obj = _parse_date(raw_date)
                if not date_obj or date_obj < cutoff:
                    continue
                score = _safe_float(row.get("TOTAL_SCORE"))
                close = _safe_float(row.get("CLOSE"))
                score_trend.append({
                    "date": date_obj.date().isoformat(),
                    "score": round(score, 1) if score is not None else None,
                    "close": close,
                })
            if rows:
                current_score = _safe_float(rows[-1].get("TOTAL_SCORE"))
    except Exception as exc:
        errors.append(f"千股千评: {exc}")

    # ----------------------------------------------------------------
    # 3. 聚合统计
    # ----------------------------------------------------------------
    all_items = guba_items

    positive_count = sum(1 for i in all_items if i["label"] == "positive")
    negative_count = sum(1 for i in all_items if i["label"] == "negative")
    neutral_count = sum(1 for i in all_items if i["label"] == "neutral")

    # 热度趋势 (按日统计)
    daily_counts: dict[str, dict] = {}
    for item in all_items:
        date_str = (item.get("publish_time") or "")[:10]
        if date_str:
            daily = daily_counts.setdefault(date_str, {
                "total": 0, "positive": 0, "negative": 0, "neutral": 0,
                "read_total": 0, "reply_total": 0,
            })
            daily["total"] += 1
            daily[item["label"]] += 1
            daily["read_total"] += item.get("read_count", 0)
            daily["reply_total"] += item.get("reply_count", 0)

    # 加入千股千评评分
    for st in score_trend:
        if st["date"] in daily_counts:
            daily_counts[st["date"]]["score"] = st["score"]
        else:
            daily_counts[st["date"]] = {
                "total": 0, "positive": 0, "negative": 0, "neutral": 0,
                "read_total": 0, "reply_total": 0, "score": st["score"],
            }

    # 总体情绪
    total = positive_count + negative_count + neutral_count
    if total > 0:
        overall_score = round(((positive_count - negative_count) / total) * 100, 1)
    else:
        overall_score = 0

    total_read = sum(i.get("read_count", 0) for i in all_items)
    total_reply = sum(i.get("reply_count", 0) for i in all_items)

    logger.info(f"[SocialSentiment] total {_time.time() - t0:.1f}s for {code}: "
                f"{len(all_items)} guba posts, score={overall_score}")
    latest_time = _latest_content_time(all_items, ["publish_time"])

    return {
        "symbol": code,
        "days": days,
        "overall_score": overall_score,
        "total_discussion": total,
        "total_read": total_read,
        "total_reply": total_reply,
        "positive_count": positive_count,
        "negative_count": negative_count,
        "neutral_count": neutral_count,
        "diagnose_score": round(current_score, 1) if current_score is not None else None,
        "score_trend": score_trend,
        "daily_trend": [
            {"date": k, **v}
            for k, v in sorted(daily_counts.items())
        ],
        "items": all_items[:50],
        "errors": errors,
        "data_time": latest_time,
        "is_stale": _content_is_stale(all_items, days, ["publish_time"]),
        "fallback_used": bool(errors),
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
    }


@router.get("/social-sentiment", summary="获取社交媒体情绪")
def get_social_sentiment(
    symbol: str = Query(..., description="股票代码"),
    days: int = Query(90, ge=1, le=90, description="查询最近N天"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """获取社交媒体讨论热度和情绪。

    返回:
      - 舆情分数 (-100~+100)
      - 讨论帖子数量/日趋势
      - 千股千评评分趋势
      - 正/负/中性比例
      - 逐条帖子

    数据源: RSSHub 东方财富关键词搜索。

    按天缓存。
    """
    symbol = _normalize_symbol(symbol)
    cache_part = f"d{days}"
    if not force:
        cached = _daily_cache_get(SOCIAL_SENTIMENT_CACHE_KEY, symbol, cache_part)
        if cached:
            cached["_cached"] = True
            return cached
    data = _fetch_social_sentiment(symbol, days)
    _daily_cache_put(SOCIAL_SENTIMENT_CACHE_KEY, symbol, data, cache_part)
    return data
