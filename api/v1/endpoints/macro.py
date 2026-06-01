# -*- coding: utf-8 -*-
"""Macro data endpoints — index/macro economic data.

Index data sources (in fallback order):
  1. ak.stock_zh_index_daily()     — 新浪财经（日线）
  2. ak.stock_zh_index_spot_sina() — 新浪财经（快照，补成交额/涨跌幅）

Bond yield source:
  ak.bond_zh_us_rate() — 中美国债收益率（东方财富）

Macro indicator source:
  ak.macro_china_pmi/cpi/ppi/gdp/money_supply/shrzgm/lpr()

数据入库，接口从数据库读取，后台异步刷新。
"""

from __future__ import annotations

import json
import logging
import math
import threading
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Index map
# ---------------------------------------------------------------------------

INDEX_MAP = {
    "000001": "上证指数",
    "399001": "深证成指",
    "399006": "创业板指",
    "000688": "科创50",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, str):
        val = val.strip().replace(",", "").replace("%", "")
        if not val or val.lower() in ("false", "none", "nan", "-"):
            return None
    try:
        v = float(val)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except (ValueError, TypeError):
        return None


def _yiyuan_to_yuan(val) -> Optional[float]:
    """将亿元单位的值转换为元。"""
    v = _safe_float(val)
    if v is None:
        return None
    return v * 1e8


def _get_db():
    from src.storage import DatabaseManager
    return DatabaseManager.get_instance()


def _is_fresh_today(records: list[dict]) -> bool:
    """判断数据库记录是否包含今天的数据（已刷新）。"""
    if not records:
        return False
    today = datetime.now().strftime("%Y-%m-%d")
    return records[0].get("date") == today


# ---------------------------------------------------------------------------
# Data source: 新浪日线（primary — OHLCV）
# ---------------------------------------------------------------------------

def _fetch_index_sina_daily(index_code: str, days: int):
    """Fetch index daily OHLCV from Sina."""
    import akshare as ak
    prefix_map = {
        "000001": "sh",
        "399001": "sz",
        "399006": "sz",
        "000688": "sh",
    }
    prefix = prefix_map.get(index_code, "sh")
    sina_symbol = f"{prefix}{index_code}"
    try:
        df = ak.stock_zh_index_daily(symbol=sina_symbol)
        if df is not None and not df.empty:
            df = df.tail(days).reset_index(drop=True)
            return df
        logger.warning(f"[Macro-新浪日线] 空数据 for {sina_symbol}")
    except Exception as e:
        logger.warning(f"[Macro-新浪日线] 失败: {e}")
    return None


# ---------------------------------------------------------------------------
# Data source: 新浪快照（补最新涨跌幅 + 成交额）
# ---------------------------------------------------------------------------

def _fetch_index_spot():
    """Fetch index spot data from Sina (all indices at once)."""
    import akshare as ak
    try:
        df = ak.stock_zh_index_spot_sina()
        if df is not None and not df.empty:
            return df
    except Exception as e:
        logger.warning(f"[Macro-新浪快照] 失败: {e}")
    return None


# ---------------------------------------------------------------------------
# Fetch with fallback → normalize → return records
# ---------------------------------------------------------------------------

def _fetch_index_with_fallback(index_code: str, days: int) -> list[dict]:
    """Fetch index data: daily OHLCV + spot pct_chg/amount."""
    daily_df = _fetch_index_sina_daily(index_code, days)
    if daily_df is None or daily_df.empty:
        return []

    prefix_map = {"000001": "sh", "399001": "sz", "399006": "sz", "000688": "sh"}
    spot_code = f"{prefix_map.get(index_code, 'sh')}{index_code}"
    spot_df = _fetch_index_spot()
    spot_row = None
    if spot_df is not None and not spot_df.empty:
        mask = spot_df["代码"] == spot_code
        matched = spot_df[mask]
        if not matched.empty:
            spot_row = matched.iloc[0]

    records = []
    for i, (_, row) in enumerate(daily_df.iterrows()):
        rec = {}
        rec["date"] = str(row.get("date", "")).strip()
        rec["open"] = _safe_float(row.get("open"))
        rec["high"] = _safe_float(row.get("high"))
        rec["low"] = _safe_float(row.get("low"))
        rec["close"] = _safe_float(row.get("close"))
        rec["volume"] = _safe_float(row.get("volume"))

        if i > 0:
            prev_close = _safe_float(daily_df.iloc[i - 1].get("close"))
            curr_close = rec["close"]
            if prev_close and prev_close > 0 and curr_close:
                rec["pct_chg"] = round((curr_close - prev_close) / prev_close * 100, 2)
            else:
                rec["pct_chg"] = None
        else:
            rec["pct_chg"] = None

        records.append(rec)

    if spot_row is not None:
        latest = records[-1] if records else {}
        pct = _safe_float(spot_row.get("涨跌幅"))
        if pct is not None:
            latest["pct_chg"] = pct
        amount = _safe_float(spot_row.get("成交额"))
        if amount is not None:
            latest["amount"] = amount
        change_amt = _safe_float(spot_row.get("涨跌额"))
        if change_amt is not None:
            latest["change_amount"] = change_amt

    return records


# ---------------------------------------------------------------------------
# Response builder
# ---------------------------------------------------------------------------

def _build_index_response(index_code: str, records: list[dict], cached: bool, source: str) -> dict:
    index_name = INDEX_MAP.get(index_code, index_code)
    latest = records[-1] if records else {}

    return {
        "index_code": index_code,
        "index_name": index_name,
        "latest": latest,
        "history": records,
        "pe_ttm": None,
        "pb": None,
        "_fetched_at": datetime.now().isoformat(),
        "_cached": cached,
        "source": source,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# Background refresh lock
# ---------------------------------------------------------------------------

_lock = None

def _get_lock():
    global _lock
    if _lock is None:
        _lock = threading.Lock()
    return _lock


# ---------------------------------------------------------------------------
# Index endpoint
# ---------------------------------------------------------------------------

@router.get("/index", summary="获取大盘指数数据")
def get_index_data(
    index_code: str = Query("000001", description="指数代码（000001 | 399001 | 399006 | 000688）"),
    days: int = Query(20, ge=1, le=500, description="返回最近N天数据"),
):
    """获取指定指数的行情数据。

    优先从数据库读取，后台异步刷新数据。
    """
    index_code = index_code.strip()
    if index_code not in INDEX_MAP:
        raise HTTPException(status_code=400, detail={
            "error": "invalid_index_code",
            "message": f"不支持的指数代码: {index_code}，支持的代码: {list(INDEX_MAP.keys())}",
        })

    # 1. 从数据库读取
    db = _get_db()
    records = db.get_macro_index_daily(index_code, limit=days)

    if records and _is_fresh_today(records):
        # 今天已刷新，直接返回
        return _build_index_response(index_code, records, cached=True, source="新浪")

    if records:
        # 有历史数据但非今日，返回并后台刷新
        lock = _get_lock()
        if lock.acquire(blocking=False):
            def _bg_refresh():
                try:
                    new_records = _fetch_index_with_fallback(index_code, days)
                    if new_records:
                        db.save_macro_index_daily(index_code, new_records, data_source="新浪")
                except Exception as e:
                    logger.warning(f"[Macro] 后台刷新失败: {e}")
                finally:
                    lock.release()
            threading.Thread(target=_bg_refresh, daemon=True).start()
        return _build_index_response(index_code, records, cached=True, source="新浪")

    # 2. 数据库为空，同步拉取 + 入库
    new_records = _fetch_index_with_fallback(index_code, days)
    if not new_records:
        raise HTTPException(status_code=502, detail={
            "error": "empty_data",
            "message": f"无法获取 {index_code} 的指数数据",
        })
    db.save_macro_index_daily(index_code, new_records, data_source="新浪")
    return _build_index_response(index_code, new_records, cached=False, source="新浪")


# ============================================================================
# Bond yield endpoint
# ============================================================================

BOND_COUNTRY_MAP = {
    "cn": "中国",
    "us": "美国",
}

# bond_zh_us_rate 列名映射: term → DataFrame 列名
BOND_TERM_MAP = {
    "2y": "2年",
    "5y": "5年",
    "10y": "10年",
    "30y": "30年",
}

# DataFrame 实际列名 (bond_zh_us_rate)
_BOND_CN_COL_MAP = {
    "2y": "中国国债收益率2年",
    "5y": "中国国债收益率5年",
    "10y": "中国国债收益率10年",
    "30y": "中国国债收益率30年",
}

_BOND_US_COL_MAP = {
    "2y": "美国国债收益率2年",
    "5y": "美国国债收益率5年",
    "10y": "美国国债收益率10年",
    "30y": "美国国债收益率30年",
}

_BOND_SPREAD_COL = {
    "cn": "中国国债收益率10年-2年",
    "us": "美国国债收益率10年-2年",
}


def _fetch_bond_yield(country: str = "cn"):
    """Fetch government bond yield curve from 东方财富 (ak.bond_zh_us_rate).

    Returns the full DataFrame for both countries; caller filters by country.
    """
    import akshare as ak
    if country not in BOND_COUNTRY_MAP:
        raise HTTPException(status_code=400, detail={
            "error": "unsupported_country",
            "message": f"不支持的国家: {country}，支持: {list(BOND_COUNTRY_MAP.keys())}",
        })
    try:
        df = ak.bond_zh_us_rate()
        if df is None or df.empty:
            return None
        return df
    except Exception as e:
        logger.warning(f"[Macro-债券] 失败: {e}")
        return None


def _build_bond_response(
    latest_yield: float | None,
    history: list[dict],
    spread: float | None,
    country: str,
    term: str,
    cached: bool,
) -> dict:
    return {
        "country": country,
        "term": term,
        "latest_yield": latest_yield,
        "history": history,
        "spread": spread,
        "_fetched_at": datetime.now().isoformat(),
        "_cached": cached,
        "source": "东方财富",
        "errors": [],
    }


@router.get("/bond-yield", summary="获取国债收益率")
def get_bond_yield(
    country: str = Query("cn", description="国家: cn(中国) | us(美国)"),
    term: str = Query("10y", description="期限: 2y | 5y | 10y | 30y"),
):
    """获取国债收益率。

    返回最新收益率、近一个月走势、期限利差（10y-2y）。
    数据源: 东方财富 bond_zh_us_rate。
    优先从数据库读取，后台异步刷新。
    """
    country = country.strip().lower()
    term = term.strip().lower()

    if term not in BOND_TERM_MAP:
        raise HTTPException(status_code=400, detail={
            "error": "invalid_term",
            "message": f"不支持的期限: {term}，支持的期限: {list(BOND_TERM_MAP.keys())}",
        })

    if country not in BOND_COUNTRY_MAP:
        raise HTTPException(status_code=400, detail={
            "error": "invalid_country",
            "message": f"不支持的国家: {country}，支持的国家: {list(BOND_COUNTRY_MAP.keys())}",
        })

    # 1. 从数据库读取
    db = _get_db()
    history = db.get_bond_yield_daily(country, term, limit=30)

    if history and _is_fresh_today(history):
        return _build_bond_response(
            latest_yield=history[0].get("value"),
            history=history,
            spread=_calc_bond_spread(db, country),
            country=country,
            term=term,
            cached=True,
        )

    if history:
        lock = _get_lock()
        if lock.acquire(blocking=False):
            def _bg_refresh():
                try:
                    df = _fetch_bond_yield(country)
                    if df is not None:
                        # 一次拉取，所有 term 都入库
                        for t in BOND_TERM_MAP:
                            _save_bond_from_df(db, df, country, t)
                except Exception as e:
                    logger.warning(f"[Macro-债券] 后台刷新失败: {e}")
                finally:
                    lock.release()
            threading.Thread(target=_bg_refresh, daemon=True).start()
        return _build_bond_response(
            latest_yield=history[0].get("value"),
            history=history,
            spread=_calc_bond_spread(db, country),
            country=country,
            term=term,
            cached=True,
        )

    # 2. 数据库为空，同步拉取 + 入库
    df = _fetch_bond_yield(country)
    if df is None:
        raise HTTPException(status_code=502, detail={
            "error": "no_data",
            "message": f"无法获取 {country} 国债收益率数据",
        })

    # 一次拉取，所有 term 都入库
    for t in BOND_TERM_MAP:
        _save_bond_from_df(db, df, country, t)

    # 重新从数据库读取请求的 term
    history = db.get_bond_yield_daily(country, term, limit=30)
    spread = _calc_bond_spread(db, country)
    return _build_bond_response(
        latest_yield=history[0].get("value") if history else None,
        history=history,
        spread=spread,
        country=country,
        term=term,
        cached=False,
    )


def _save_bond_from_df(db, df, country: str, term: str) -> tuple[list[dict], float | None]:
    """从 bond_zh_us_rate DataFrame 提取收益率，入库，返回 history + spread。"""
    col_map = _BOND_CN_COL_MAP if country == "cn" else _BOND_US_COL_MAP
    term_col = col_map.get(term)
    if term_col is None:
        return [], None

    df_sorted = df.sort_values("日期").reset_index(drop=True)
    valid = df_sorted[df_sorted[term_col].notna()].copy()

    last_22 = valid.tail(22)
    history = []
    for _, row in last_22.iterrows():
        date_str = str(row.get("日期", "")).strip()
        val = _safe_float(row[term_col])
        if val is not None:
            history.append({"date": date_str, "value": val})

    if history:
        db.save_bond_yield_daily(country, term, history)

    # 计算利差 (10y - 2y), 直接从 DataFrame 的利差列取
    spread = None
    spread_col = _BOND_SPREAD_COL.get(country)
    if spread_col and spread_col in valid.columns and not valid.empty:
        latest = valid.iloc[-1]
        s = _safe_float(latest.get(spread_col))
        if s is not None:
            spread = round(s, 4)

    return history, spread


def _calc_bond_spread(db, country: str) -> float | None:
    """从数据库计算利差（10y - 2y）。"""
    try:
        y10 = db.get_bond_yield_daily(country, "10y", limit=1)
        y2 = db.get_bond_yield_daily(country, "2y", limit=1)
        if y10 and y2:
            v10 = y10[0].get("value")
            v2 = y2[0].get("value")
            if v10 is not None and v2 is not None:
                return round(v10 - v2, 4)
    except Exception:
        pass
    return None


# ============================================================================
# Macro indicator endpoint
# ============================================================================

INDICATOR_MAP = {
    "PMI": "制造业采购经理指数",
    "CPI": "居民消费价格指数",
    "PPI": "工业生产者出厂价格指数",
    "GDP": "国内生产总值",
    "M2": "货币和准货币(M2)",
    "社融": "社会融资规模",
    "LPR": "贷款市场报价利率",
}


def _fetch_indicator_pmi():
    import akshare as ak
    df = ak.macro_china_pmi()
    if df is None or df.empty:
        return None
    # Newest first → reverse so oldest first for consistent slicing
    df = df.iloc[::-1].reset_index(drop=True)
    records = []
    for _, row in df.iterrows():
        records.append({
            "period": str(row.get("月份", "")).strip(),
            "value": _safe_float(row.get("制造业-指数")),
            "yoy": _safe_float(row.get("制造业-同比增长")),
            "extra": {
                "非制造业-指数": _safe_float(row.get("非制造业-指数")),
                "非制造业-同比增长": _safe_float(row.get("非制造业-同比增长")),
            },
        })
    return records


def _fetch_indicator_cpi():
    import akshare as ak
    df = ak.macro_china_cpi()
    if df is None or df.empty:
        return None
    df = df.iloc[::-1].reset_index(drop=True)
    records = []
    for _, row in df.iterrows():
        records.append({
            "period": str(row.get("月份", "")).strip(),
            "value": _safe_float(row.get("全国-当月")),
            "yoy": _safe_float(row.get("全国-同比增长")),
            "mom": _safe_float(row.get("全国-环比增长")),
        })
    return records


def _fetch_indicator_ppi():
    import akshare as ak
    df = ak.macro_china_ppi()
    if df is None or df.empty:
        return None
    df = df.iloc[::-1].reset_index(drop=True)
    records = []
    for _, row in df.iterrows():
        records.append({
            "period": str(row.get("月份", "")).strip(),
            "value": _safe_float(row.get("当月")),
            "yoy": _safe_float(row.get("当月同比增长")),
        })
    return records


def _fetch_indicator_gdp():
    import akshare as ak
    df = ak.macro_china_gdp()
    if df is None or df.empty:
        return None
    df = df.iloc[::-1].reset_index(drop=True)
    records = []
    for _, row in df.iterrows():
        records.append({
            "period": str(row.get("季度", "")).strip(),
            "value": _safe_float(row.get("国内生产总值-绝对值")),
            "yoy": _safe_float(row.get("国内生产总值-同比增长")),
        })
    return records


def _fetch_indicator_m2():
    import akshare as ak
    df = ak.macro_china_money_supply()
    if df is None or df.empty:
        return None
    df = df.iloc[::-1].reset_index(drop=True)
    records = []
    for _, row in df.iterrows():
        records.append({
            "period": str(row.get("月份", "")).strip(),
            "value": _safe_float(row.get("货币和准货币(M2)-数量(亿元)")),
            "yoy": _safe_float(row.get("货币和准货币(M2)-同比增长")),
            "mom": _safe_float(row.get("货币和准货币(M2)-环比增长")),
        })
    return records


def _fetch_indicator_social_finance():
    import akshare as ak
    df = ak.macro_china_shrzgm()
    if df is None or df.empty:
        return None
    # 社融 is already oldest-first, no reversal needed
    records = []
    for _, row in df.iterrows():
        records.append({
            "period": str(row.get("月份", "")).strip(),
            "value": _safe_float(row.get("社会融资规模增量")),
        })
    return records


def _fetch_indicator_lpr():
    import akshare as ak
    df = ak.macro_china_lpr()
    if df is None or df.empty:
        return None
    # LPR is oldest first already, skip None values
    records = []
    for _, row in df.iterrows():
        value = _safe_float(row.get("LPR1Y"))
        if value is None:
            continue
        records.append({
            "period": str(row.get("TRADE_DATE", "")).strip(),
            "value": value,
            "extra": {
                "LPR5Y": _safe_float(row.get("LPR5Y")),
            },
        })
    return records


INDICATOR_FETCHERS = {
    "PMI": _fetch_indicator_pmi,
    "CPI": _fetch_indicator_cpi,
    "PPI": _fetch_indicator_ppi,
    "GDP": _fetch_indicator_gdp,
    "M2": _fetch_indicator_m2,
    "社融": _fetch_indicator_social_finance,
    "LPR": _fetch_indicator_lpr,
}


def _trend_label(records: list[dict]) -> str:
    """Determine trend from last 3 records.

    Tries yoy first, then falls back to value.
    Skips None values.
    """
    for k in ("yoy", "value"):
        vals = [r.get(k) for r in records[-5:] if r.get(k) is not None]
        if len(vals) >= 2:
            if vals[-1] > vals[0] + 0.05:
                return "上升"
            if vals[-1] < vals[0] - 0.05:
                return "下降"
            return "持平"
    return "持平"


def _build_indicator_response(
    indicator: str,
    records: list[dict],
    trend: str,
    cached: bool,
) -> dict:
    return {
        "indicator": indicator,
        "indicator_name": INDICATOR_MAP.get(indicator, indicator),
        "latest": records[-1] if records else {},
        "history": records,
        "trend": trend,
        "_fetched_at": datetime.now().isoformat(),
        "_cached": cached,
        "source": "东方财富",
        "errors": [],
    }


@router.get("/indicator", summary="获取宏观经济指标")
def get_macro_indicator(
    indicator: str = Query(..., description="指标名称: PMI | CPI | PPI | GDP | M2 | 社融 | LPR"),
    months: int = Query(12, ge=1, le=120, description="返回最近N个月数据"),
):
    """获取关键宏观经济数据。

    优先从数据库读取，后台异步刷新。
    """
    if indicator.strip() not in ("社融",):
        indicator = indicator.strip().upper()
    else:
        indicator = "社融"

    if indicator not in INDICATOR_FETCHERS:
        raise HTTPException(status_code=400, detail={
            "error": "invalid_indicator",
            "message": f"不支持的指标: {indicator}，支持的指标: {list(INDICATOR_FETCHERS.keys())}",
        })

    # 1. 从数据库读取
    db = _get_db()
    records = db.get_macro_indicator(indicator, limit=months)
    if records is not None:
        records = records[-months:] if len(records) > months else records

    if records:
        trend = _trend_label(records)
        lock = _get_lock()
        if lock.acquire(blocking=False):
            def _bg_refresh():
                try:
                    new_records = INDICATOR_FETCHERS[indicator]()
                    if new_records:
                        db.save_macro_indicator(indicator, new_records)
                except Exception as e:
                    logger.warning(f"[Macro-指标] 后台刷新失败: {e}")
                finally:
                    lock.release()
            threading.Thread(target=_bg_refresh, daemon=True).start()
        return _build_indicator_response(indicator, records, trend, cached=True)

    # 2. 数据库为空，同步拉取 + 入库
    fetcher = INDICATOR_FETCHERS[indicator]
    new_records = fetcher()
    if new_records is None:
        raise HTTPException(status_code=502, detail={
            "error": "no_data",
            "message": f"无法获取 {indicator} 数据",
        })

    new_records = new_records[-months:] if len(new_records) > months else new_records
    db.save_macro_indicator(indicator, new_records)
    trend = _trend_label(new_records)
    return _build_indicator_response(indicator, new_records, trend, cached=False)


# ============================================================================
# Sector fund flow endpoint
# ============================================================================

def _fetch_sector_flow_industry() -> list[dict]:
    """获取行业板块资金流向 — 返回全量排序数据。

    1. 优先: 同花顺 stock_board_industry_summary_ths — 有净流入、上涨下跌家数
    2. 降级: 新浪 stock_sector_spot(indicator='行业') — 有涨跌幅、总成交额、领涨股
    """
    import akshare as ak

    # 优先: 同花顺
    try:
        df = ak.stock_board_industry_summary_ths()
        if df is not None and not df.empty:
            df = df.sort_values("净流入", ascending=False).reset_index(drop=True)
            records = []
            for _, row in df.iterrows():
                rec = {
                    "name": str(row.get("板块", "")).strip(),
                    "pct_chg": _safe_float(row.get("涨跌幅")),
                    "main_net_inflow": _yiyuan_to_yuan(row.get("净流入")),  # 亿→元
                    "super_large_net_inflow": None,
                    "large_net_inflow": None,
                    "total_amount": _yiyuan_to_yuan(row.get("总成交额")),  # 亿→元
                    "up_count": _safe_float(row.get("上涨家数")),
                    "down_count": _safe_float(row.get("下跌家数")),
                    "leading_stock": str(row.get("领涨股", "")).strip(),
                }
                records.append(rec)
            return records
    except Exception as e:
        logger.warning(f"[Macro-板块资金-行业THS] 降级: {e}")

    # 降级: 新浪行业板块 (成交额已经是元)
    try:
        df = ak.stock_sector_spot(indicator="行业")
        if df is not None and not df.empty:
            df = df.sort_values("涨跌幅", ascending=False).reset_index(drop=True)
            records = []
            for _, row in df.iterrows():
                rec = {
                    "name": str(row.get("板块", "")).strip(),
                    "pct_chg": _safe_float(row.get("涨跌幅")),
                    "main_net_inflow": None,
                    "super_large_net_inflow": None,
                    "large_net_inflow": None,
                    "total_amount": _safe_float(row.get("总成交额")),
                    "up_count": None,
                    "down_count": None,
                    "leading_stock": str(row.get("股票名称", "")).strip(),
                }
                records.append(rec)
            return records
    except Exception as e:
        logger.warning(f"[Macro-板块资金-行业新浪] 失败: {e}")

    return []


def _fetch_sector_flow_concept() -> list[dict]:
    """获取概念板块资金流向 — 返回全量排序数据。

    1. 优先: 东方财富 stock_sector_fund_flow_rank(indicator='今日', sector_type='概念资金流')
    2. 降级: 新浪 stock_sector_spot(indicator='概念') — 有涨跌幅、总成交额、领涨股
    """
    import akshare as ak

    # 尝试东方财富
    try:
        df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="概念资金流")
        if df is not None and not df.empty:
            # akshare indicator="今日" 时列名带"今日"前缀，需 fallback 匹配
            sort_col = next(
                (c for c in df.columns if "主力净流入" in c and "净额" in c),
                next((c for c in df.columns if "主力净流入" in c), None),
            )
            if sort_col:
                df = df.sort_values(sort_col, ascending=False).reset_index(drop=True)
            main_col = next((c for c in df.columns if "主力净流入" in c and "净额" in c), None)
            super_col = next((c for c in df.columns if "超大单净流入" in c and "净额" in c), None)
            large_col = next((c for c in df.columns if "大单净流入" in c and "净额" in c and "超大" not in c), None)
            pct_col = next((c for c in df.columns if "涨跌幅" in c), "涨跌幅")
            records = []
            for _, row in df.iterrows():
                rec = {
                    "name": str(row.get("名称", "")).strip(),
                    "pct_chg": _safe_float(row.get(pct_col)),
                    "main_net_inflow": _safe_float(row.get(main_col) if main_col else None),
                    "super_large_net_inflow": _safe_float(row.get(super_col) if super_col else None),
                    "large_net_inflow": _safe_float(row.get(large_col) if large_col else None),
                    "total_amount": None,
                    "up_count": None,
                    "down_count": None,
                    "leading_stock": None,
                }
                records.append(rec)
            return records
    except Exception as e:
        logger.warning(f"[Macro-板块资金-概念EM] 降级: {e}")

    # 降级: 新浪概念板块
    try:
        df = ak.stock_sector_spot(indicator="概念")
        if df is not None and not df.empty:
            df = df.sort_values("涨跌幅", ascending=False).reset_index(drop=True)
            records = []
            for _, row in df.iterrows():
                rec = {
                    "name": str(row.get("板块", "")).strip(),
                    "pct_chg": _safe_float(row.get("涨跌幅")),
                    "main_net_inflow": None,
                    "super_large_net_inflow": None,
                    "large_net_inflow": None,
                    "total_amount": _safe_float(row.get("总成交额")),
                    "up_count": None,
                    "down_count": None,
                    "leading_stock": str(row.get("股票名称", "")).strip(),
                }
                records.append(rec)
            return records
    except Exception as e:
        logger.warning(f"[Macro-板块资金-概念新浪] 失败: {e}")

    return []


# ============================================================================
# Sector-flow & market-breadth cache helpers
# ============================================================================

def _macro_cache_key(prefix: str) -> str:
    """按天粒度的缓存 key。"""
    return f"macro:{prefix}:{datetime.now().strftime('%Y%m%d')}"


def _macro_cache_get(prefix: str) -> dict | None:
    try:
        from src.storage import DatabaseManager
        raw = DatabaseManager.get_instance().get_kline_snapshot(_macro_cache_key(prefix))
        if raw:
            return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        pass
    return None


def _macro_cache_put(prefix: str, data: dict) -> None:
    try:
        from src.storage import DatabaseManager
        DatabaseManager.get_instance().save_kline_snapshot(
            _macro_cache_key(prefix), json.dumps(data, ensure_ascii=False))
    except Exception:
        pass



@router.get("/sector-flow", summary="获取板块资金流向")
def get_sector_flow(
    type: str = Query("industry", description="板块类型: industry(行业) | concept(概念)"),
    top_n: int = Query(10, ge=1, le=50, description="返回前N个板块"),
):
    """获取行业/概念板块的主力资金净流入/流出情况。

    行业板块数据源: 同花顺 (stock_board_industry_summary_ths)
    概念板块数据源: 东方财富 (stock_sector_fund_flow_rank)，降级到新浪
    按天缓存。
    """
    type = type.strip().lower()
    if type not in ("industry", "concept"):
        raise HTTPException(status_code=400, detail={
            "error": "invalid_type",
            "message": f"不支持的板块类型: {type}，支持: industry, concept",
        })

    # 尝试读缓存
    cache_prefix = f"sector-flow-{type}"
    cached = _macro_cache_get(cache_prefix)
    if cached:
        # 缓存里存的是全量数据，按 top_n 截取
        inflow = (cached.get("inflow_top") or [])[:top_n]
        outflow = (cached.get("outflow_top") or [])[:top_n]
        records = (cached.get("records") or [])[:top_n]
        return {
            **cached,
            "inflow_top": inflow,
            "outflow_top": outflow,
            "records": records,
            "top_n": top_n,
            "_cached": True,
        }

    errors: list[str] = []

    try:
        if type == "industry":
            all_records = _fetch_sector_flow_industry()
            if all_records and all_records[0].get("main_net_inflow") is not None:
                source = "同花顺"
            else:
                source = "新浪"
        else:
            all_records = _fetch_sector_flow_concept()
            if all_records and all_records[0].get("main_net_inflow") is not None:
                source = "东方财富"
            else:
                source = "新浪"
    except Exception as e:
        logger.warning(f"[Macro-板块资金] 失败: {e}")
        raise HTTPException(status_code=502, detail={
            "error": "no_data",
            "message": f"无法获取板块资金流向数据: {e}",
        })

    if not all_records:
        raise HTTPException(status_code=502, detail={
            "error": "no_data",
            "message": "无法获取板块资金流向数据",
        })

    # 拆分: 流入 top N (净流入 > 0, 降序) + 流出 top N (净流入 < 0, 升序=流出最大在前)
    inflow_records = sorted(
        [r for r in all_records if r.get("main_net_inflow") is not None and r["main_net_inflow"] > 0],
        key=lambda r: r["main_net_inflow"], reverse=True,
    )
    outflow_records = sorted(
        [r for r in all_records if r.get("main_net_inflow") is not None and r["main_net_inflow"] < 0],
        key=lambda r: r["main_net_inflow"],
    )

    # 如果没有净流入字段（如新浪降级），按涨跌幅拆分
    if not inflow_records and not outflow_records:
        inflow_records = [r for r in all_records if r.get("pct_chg") is not None and r["pct_chg"] > 0]
        outflow_records = [r for r in all_records if r.get("pct_chg") is not None and r["pct_chg"] < 0]

    result = {
        "type": type,
        "top_n": top_n,
        "inflow_top": inflow_records,
        "outflow_top": outflow_records,
        "records": all_records[:top_n],  # 兼容旧前端
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
        "source": source,
        "errors": errors,
    }

    # 写缓存（存全量，请求时按 top_n 截取）
    _macro_cache_put(cache_prefix, result)

    # 返回时截取
    result["inflow_top"] = inflow_records[:top_n]
    result["outflow_top"] = outflow_records[:top_n]
    return result


# ============================================================================
# Market breadth endpoint
# ============================================================================

def _fetch_market_breadth_data() -> dict:
    """抓取市场宽度全量数据（不含缓存逻辑）。"""
    import akshare as ak

    errors: list[str] = []
    today_str = datetime.now().strftime("%Y%m%d")
    source_parts: list[str] = []
    import akshare as ak

    errors: list[str] = []
    today_str = datetime.now().strftime("%Y%m%d")
    source_parts: list[str] = []

    # ---- 1. 涨跌家数 & 成交额 ----
    up_count = None
    down_count = None
    flat_count = None
    volume = None

    try:
        ths_df = ak.stock_board_industry_summary_ths()
        if ths_df is not None and not ths_df.empty:
            if "上涨家数" in ths_df.columns:
                up_count = int(ths_df["上涨家数"].sum())
            if "下跌家数" in ths_df.columns:
                down_count = int(ths_df["下跌家数"].sum())
            if "总成交额" in ths_df.columns:
                # THS 总成交额单位是亿元
                volume = float(ths_df["总成交额"].sum()) * 1e8
            source_parts.append("同花顺")
    except Exception as e:
        errors.append(f"同花顺行业汇总: {e}")
        logger.warning(f"[Macro-市场宽度-THS] 降级: {e}")

    # 同花顺失败时降级到新浪行业板块
    if up_count is None and down_count is None:
        try:
            sina_df = ak.stock_sector_spot(indicator="行业")
            if sina_df is not None and not sina_df.empty:
                if "总成交额" in sina_df.columns:
                    volume = float(sina_df["总成交额"].sum())
                if "涨跌幅" in sina_df.columns:
                    up_count = int((sina_df["涨跌幅"] > 0).sum())
                    down_count = int((sina_df["涨跌幅"] < 0).sum())
                source_parts.append("新浪")
        except Exception as e:
            errors.append(f"新浪行业板块: {e}")
            logger.warning(f"[Macro-市场宽度-新浪] 失败: {e}")

    # ---- 1b. 平盘家数 ----
    # 复用 market_status.py 的逻辑: 沪深交易所总股票数 - 上涨 - 下跌
    if up_count is not None and down_count is not None:
        try:
            sse = ak.stock_sse_summary()
            szse = ak.stock_szse_summary()
            sse_stocks = int(float(sse[sse['项目'] == '上市股票']['股票'].iloc[0]))
            szse_stocks = int(szse[szse['证券类别'] == '股票']['数量'].iloc[0])
            total = sse_stocks + szse_stocks
            flat = total - up_count - down_count
            if flat < 0:
                # 行业汇总覆盖范围可能略大于纯A股，用北向持平比例估算
                try:
                    hsgt_df = ak.stock_hsgt_fund_flow_summary_em()
                    if hsgt_df is not None and not hsgt_df.empty:
                        north = hsgt_df[(hsgt_df['板块'].isin(['沪股通', '深股通'])) & (hsgt_df['资金方向'] == '北向')]
                        hsgt_up = hsgt_down = hsgt_flat = 0
                        for _, row in north.iterrows():
                            hsgt_up += int(row.get('上涨数', 0) or 0)
                            hsgt_down += int(row.get('下跌数', 0) or 0)
                            hsgt_flat += int(row.get('持平数', 0) or 0)
                        hsgt_total = hsgt_up + hsgt_down + hsgt_flat
                        if hsgt_total > 0:
                            flat = round(total * hsgt_flat / hsgt_total)
                except Exception:
                    pass
            flat_count = max(0, flat)
        except Exception as e:
            errors.append(f"平盘计算: {e}")
            logger.warning(f"[Macro-市场宽度-平盘] 失败: {e}")

    # ---- 2. 涨停池 (东方财富, <2s) ----
    limit_up_count = None
    broken_board_rate = None

    try:
        zt_df = ak.stock_zt_pool_em(date=today_str)
        if zt_df is not None and not zt_df.empty:
            limit_up_count = len(zt_df)
            if "炸板次数" in zt_df.columns:
                broken_count = int((zt_df["炸板次数"] > 0).sum())
            else:
                broken_count = 0
            broken_board_rate = round(broken_count / limit_up_count * 100, 2) if limit_up_count > 0 else 0.0
            source_parts.append("东方财富")
    except Exception as e:
        errors.append(f"涨停池: {e}")
        logger.warning(f"[Macro-市场宽度-涨停池] 失败: {e}")

    # ---- 3. 跌停池 (东方财富, <2s) ----
    limit_down_count = None

    try:
        dt_df = ak.stock_zt_pool_dtgc_em(date=today_str)
        if dt_df is not None and not dt_df.empty:
            limit_down_count = len(dt_df)
    except Exception as e:
        errors.append(f"跌停池: {e}")
        logger.warning(f"[Macro-市场宽度-跌停池] 失败: {e}")

    # ---- 4. 60日新高家数 (东方财富强势涨停池, <2s) ----
    new_high_60d = None
    try:
        strong_df = ak.stock_zt_pool_strong_em(date=today_str)
        if strong_df is not None and not strong_df.empty:
            # "是否新高" 列值为 "是" 表示 60 日新高
            if "是否新高" in strong_df.columns:
                new_high_60d = int((strong_df["是否新高"] == "是").sum())
            else:
                # 无此列时，整池即为强势股近似
                new_high_60d = len(strong_df)
    except Exception as e:
        errors.append(f"强势涨停池: {e}")
        logger.warning(f"[Macro-市场宽度-强势池] 失败: {e}")

    # ---- 5. 连涨/连跌天数 (新浪上证指数日线, <1s) ----
    consecutive_up_days = None
    consecutive_down_days = None

    try:
        idx_df = ak.stock_zh_index_daily(symbol="sh000001")
        if idx_df is not None and not idx_df.empty:
            recent = idx_df.tail(20).reset_index(drop=True)
            if "close" in recent.columns and len(recent) >= 2:
                last_close = _safe_float(recent.iloc[-1]["close"])
                prev_close = _safe_float(recent.iloc[-2]["close"])
                if last_close is not None and prev_close is not None:
                    is_last_up = last_close > prev_close
                    count = 1
                    for i in range(len(recent) - 2, 0, -1):
                        c = _safe_float(recent.iloc[i]["close"])
                        p = _safe_float(recent.iloc[i - 1]["close"])
                        if c is None or p is None:
                            break
                        if is_last_up and c > p:
                            count += 1
                        elif not is_last_up and c < p:
                            count += 1
                        else:
                            break
                    consecutive_up_days = count if is_last_up else 0
                    consecutive_down_days = count if not is_last_up else 0
    except Exception as e:
        errors.append(f"连涨连跌: {e}")
        logger.warning(f"[Macro-市场宽度-连涨连跌] 失败: {e}")

    # ---- 6. 计算衍生指标 ----
    advance_decline_ratio = None
    if up_count is not None and down_count is not None and down_count > 0:
        advance_decline_ratio = round(up_count / down_count, 2)

    source_label = " + ".join(source_parts) if source_parts else "未知"

    return {
        "up_count": up_count,
        "down_count": down_count,
        "flat_count": flat_count,
        "advance_decline_ratio": advance_decline_ratio,
        "new_high_60d": new_high_60d,
        "new_low_60d": limit_down_count,  # 跌停数近似为创新低数
        "consecutive_up_days": consecutive_up_days,
        "consecutive_down_days": consecutive_down_days,
        "limit_up_count": limit_up_count,
        "limit_down_count": limit_down_count,
        "broken_board_rate": broken_board_rate,
        "volume": volume,
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
        "source": source_label,
        "errors": errors,
    }


@router.get("/market-breadth", summary="获取市场宽度")
def get_market_breadth():
    """获取市场整体参与度（赚钱效应），辅助判断市场情绪。

    按天缓存。数据源同 _fetch_market_breadth_data。
    """
    # 尝试读缓存
    cached = _macro_cache_get("market-breadth")
    if cached:
        cached["_cached"] = True
        return cached

    data = _fetch_market_breadth_data()
    _macro_cache_put("market-breadth", data)
    return data
