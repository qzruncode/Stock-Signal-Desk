# -*- coding: utf-8 -*-
"""Market-level data fetchers — stats, all A-share list, index, limit-up pool, hot stocks."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..utils import is_bse_code, is_st_stock, is_kc_cy_stock, normalize_stock_code


logger = logging.getLogger(__name__)


# ── Market stats ─────────────────────────────────────────────────────────


def get_market_stats(enforce_rate_limit=None, set_user_agent=None) -> Optional[Dict[str, Any]]:
    """获取市场涨跌统计，优先东财接口，失败后降级新浪。"""
    import akshare as ak

    if set_user_agent:
        set_user_agent()
    if enforce_rate_limit:
        enforce_rate_limit()
    try:
        logger.info("[API调用] ak.stock_zh_a_spot_em() 获取市场统计...")
        df = ak.stock_zh_a_spot_em()
        if df is not None and not df.empty:
            return _calc_market_stats(df)
    except Exception as e:
        logger.warning("[Akshare] 东财接口获取市场统计失败: %s，尝试新浪接口", e)

    if set_user_agent:
        set_user_agent()
    if enforce_rate_limit:
        enforce_rate_limit()
    try:
        logger.info("[API调用] ak.stock_zh_a_spot() 获取市场统计(新浪)...")
        df = ak.stock_zh_a_spot()
        if df is not None and not df.empty:
            return _calc_market_stats(df)
    except Exception as e:
        logger.error("[Akshare] 新浪接口获取市场统计也失败: %s", e)

    return None


def _calc_market_stats(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """从行情 DataFrame 计算涨跌统计。"""
    df = df.copy()

    code_col = next((c for c in ['代码', '股票代码', 'ts_code', 'stock_code'] if c in df.columns), None)
    name_col = next((c for c in ['名称', '股票名称', 'name'] if c in df.columns), None)
    close_col = next((c for c in ['最新价', 'close', 'lastPrice'] if c in df.columns), None)
    pre_close_col = next((c for c in ['昨收', '昨日收盘', 'pre_close', 'lastClose'] if c in df.columns), None)
    amount_col = next((c for c in ['成交额', 'amount'] if c in df.columns), None)

    limit_up_count = limit_down_count = up_count = down_count = flat_count = 0

    for code, name, current_price, pre_close, amount in zip(
        df[code_col], df[name_col], df[close_col], df[pre_close_col], df[amount_col]
    ):
        if pd.isna(current_price) or pd.isna(pre_close) or current_price in ['-'] or pre_close in ['-'] or amount == 0:
            continue
        current_price = float(current_price)
        pre_close = float(pre_close)
        pure_code = normalize_stock_code(str(code))

        if is_bse_code(pure_code):
            ratio = 0.30
        elif is_kc_cy_stock(pure_code):
            ratio = 0.20
        elif is_st_stock(name):
            ratio = 0.05
        else:
            ratio = 0.10

        limit_up_price = np.floor(pre_close * (1 + ratio) * 100 + 0.5) / 100.0
        limit_down_price = np.floor(pre_close * (1 - ratio) * 100 + 0.5) / 100.0

        if current_price > 0:
            is_limit_up = abs(current_price - limit_up_price) <= round(abs(pre_close * (1 + ratio) - limit_up_price), 10)
            is_limit_down = abs(current_price - limit_down_price) <= round(abs(pre_close * (1 - ratio) - limit_down_price), 10)

            if is_limit_up:
                limit_up_count += 1
            if is_limit_down:
                limit_down_count += 1
            if current_price > pre_close:
                up_count += 1
            elif current_price < pre_close:
                down_count += 1
            else:
                flat_count += 1

    stats = {
        'up_count': up_count, 'down_count': down_count, 'flat_count': flat_count,
        'limit_up_count': limit_up_count, 'limit_down_count': limit_down_count, 'total_amount': 0.0,
    }
    if amount_col and amount_col in df.columns:
        df[amount_col] = pd.to_numeric(df[amount_col], errors='coerce')
        stats['total_amount'] = df[amount_col].sum() / 1e8
    return stats


# ── All A-share list ────────────────────────────────────────────────────


def get_all_a_stocks(enforce_rate_limit=None) -> Optional[List[Dict[str, Any]]]:
    """获取全部 A 股股票列表（含基础元数据）。"""
    import akshare as ak

    try:
        logger.info("[StocksSync] Step 1: 获取 A 股代码名称列表...")
        name_df = ak.stock_info_a_code_name()
        if name_df is None or name_df.empty:
            return None

        spot_df = None
        try:
            if enforce_rate_limit:
                enforce_rate_limit()
            logger.info("[StocksSync] Step 2: 尝试获取估值快照...")
            spot_df = ak.stock_zh_a_spot_em()
            if spot_df is not None and not spot_df.empty:
                logger.info("[StocksSync] 估值快照: %d 条", len(spot_df))
            else:
                spot_df = None
        except Exception as e:
            logger.warning("[StocksSync] 估值快照获取失败: %s", str(e)[:120])

        results: List[Dict[str, Any]] = []
        spot_map: Dict[str, Any] = {}
        if spot_df is not None:
            for _, row in spot_df.iterrows():
                c = str(row.get('代码', '')).strip()
                if len(c) >= 6:
                    spot_map[c] = row

        for _, row in name_df.iterrows():
            code_str = str(row.get('code', '')).strip()
            name_str = str(row.get('name', '')).strip()
            if not code_str or len(code_str) < 6:
                continue
            market = _classify_a_stock_market(code_str)
            if market == 'bj' or 'ST' in name_str.upper():
                continue
            spot = spot_map.get(code_str)
            item = {
                'code': code_str, 'name': name_str, 'market': market,
                'pe_ttm': _safe_float(spot.get('市盈率-动态')) if spot is not None else None,
                'pb': _safe_float(spot.get('市净率')) if spot is not None else None,
                'total_market_cap': _safe_float(spot.get('总市值')) if spot is not None else None,
                'circulating_market_cap': _safe_float(spot.get('流通市值')) if spot is not None else None,
            }
            results.append(item)

        logger.info("[StocksSync] 完成: %d 只 A 股（已过滤北交所、ST）", len(results))
        return results
    except Exception as e:
        logger.error("[StocksSync] 获取全 A 股列表失败: %s", e, exc_info=True)
        return None


def _classify_a_stock_market(code: str) -> str:
    code = code.strip()
    if code.startswith('68'):
        return 'kcb'
    if code.startswith(('300', '301')):
        return 'cyb'
    if code.startswith(('8', '9')) and len(code) == 6:
        return 'bj'
    if code.startswith('60'):
        return 'sh'
    if code.startswith(('000', '001', '002', '003')):
        return 'sz'
    return 'other'


# ── Limit-up pool ────────────────────────────────────────────────────────


def get_limit_up_pool(
    date: Optional[str] = None, n: int = 20,
    enforce_rate_limit=None, set_user_agent=None,
) -> Optional[List[Dict[str, Any]]]:
    """获取涨停池，按连板数和封板时间排序。"""
    import akshare as ak

    query_date = date or datetime.now().strftime('%Y%m%d')
    if set_user_agent:
        set_user_agent()
    if enforce_rate_limit:
        enforce_rate_limit()

    try:
        logger.info("[API调用] ak.stock_zt_pool_em(date=%s) 获取涨停池...", query_date)
        df = ak.stock_zt_pool_em(date=query_date)
        if df is None or df.empty:
            return None

        df = df.copy()
        for col in ('连板数', '封板资金', '成交额', '换手率', '涨跌幅'):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        if '首次封板时间' in df.columns:
            df['首次封板时间'] = df['首次封板时间'].map(_normalize_limit_time_value)
            df['_首次封板时间排序'] = df['首次封板时间'].where(df['首次封板时间'] != '', '999999')
        sort_cols = [col for col in ('连板数', '_首次封板时间排序') if col in df.columns]
        if sort_cols:
            ascending = [False if col == '连板数' else True for col in sort_cols]
            df = df.sort_values(sort_cols, ascending=ascending)

        rows: List[Dict[str, Any]] = []
        for _, row in df.head(n).iterrows():
            rows.append({
                'code': str(row.get('代码', '')).strip(),
                'name': str(row.get('名称', '')).strip(),
                'change_pct': _safe_float(row.get('涨跌幅')),
                'price': _safe_float(row.get('最新价')),
                'amount': _safe_float(row.get('成交额')),
                'turnover_rate': _safe_float(row.get('换手率')),
                'seal_amount': _safe_float(row.get('封板资金')),
                'first_limit_time': str(row.get('首次封板时间', '')).strip(),
                'last_limit_time': _normalize_limit_time_value(row.get('最后封板时间')),
                'break_count': _safe_int(row.get('炸板次数')),
                'limit_stat': str(row.get('涨停统计', '')).strip(),
                'consecutive_boards': _safe_int(row.get('连板数')),
                'industry': str(row.get('所属行业', '')).strip(),
            })
        return rows
    except Exception as e:
        logger.warning("[Akshare] 获取涨停池失败: %s", e)
        return None


# ── Hot stocks ───────────────────────────────────────────────────────────


def get_hot_stocks(n: int = 10, enforce_rate_limit=None, set_user_agent=None) -> Optional[List[Dict[str, Any]]]:
    """获取人气股榜，按多数据源降级。"""
    import akshare as ak

    fetch_attempts = (
        ("东方财富人气榜", lambda top_n: _get_eastmoney_hot_stocks(ak, top_n, enforce_rate_limit, set_user_agent)),
        ("东方财富飙升榜", lambda top_n: _get_eastmoney_hot_up_stocks(ak, top_n, enforce_rate_limit, set_user_agent)),
        ("雪球关注榜", lambda top_n: _get_xueqiu_hot_stocks(ak, top_n, enforce_rate_limit, set_user_agent)),
    )
    last_error = ""
    for source, fetch in fetch_attempts:
        try:
            rows = fetch(n)
            if rows:
                return rows[:n]
        except Exception as e:
            last_error = f"{source}: {e}"
            logger.debug("[Akshare] 人气股候选源失败 %s: %s", source, e)
    if last_error:
        logger.warning("[Akshare] 获取人气股全部失败: %s", last_error)
    return None


def _get_eastmoney_hot_stocks(ak, n: int, enforce_rate_limit=None, set_user_agent=None) -> Optional[List[Dict[str, Any]]]:
    if set_user_agent:
        set_user_agent()
    if enforce_rate_limit:
        enforce_rate_limit()
    logger.info("[API调用] ak.stock_hot_rank_em() 获取东方财富人气股...")
    df = ak.stock_hot_rank_em()
    if df is None or df.empty:
        return None
    return [{
        'rank': _safe_int(row.get('当前排名')), 'code': str(row.get('代码', '')).strip(),
        'name': str(row.get('股票名称', '')).strip(), 'price': _safe_float(row.get('最新价')),
        'change_pct': _safe_float(row.get('涨跌幅')), 'source': '东方财富人气榜',
    } for _, row in df.head(n).iterrows()]


def _get_eastmoney_hot_up_stocks(ak, n: int, enforce_rate_limit=None, set_user_agent=None) -> Optional[List[Dict[str, Any]]]:
    if set_user_agent:
        set_user_agent()
    if enforce_rate_limit:
        enforce_rate_limit()
    logger.info("[API调用] ak.stock_hot_up_em() 获取东方财富飙升榜...")
    df = ak.stock_hot_up_em()
    if df is None or df.empty:
        return None
    code_col = _find_first_column(df, ("代码", "股票代码"))
    name_col = _find_first_column(df, ("股票名称", "名称", "股票简称"))
    if not code_col or not name_col:
        return None
    return [{
        'rank': _safe_int(row.get(_find_first_column(df, ("当前排名", "排名", "序号")))) if _find_first_column(df, ("当前排名", "排名", "序号")) else i + 1,
        'code': str(row.get(code_col, '')).strip(), 'name': str(row.get(name_col, '')).strip(),
        'price': _safe_float(row.get(_find_first_column(df, ("最新价", "现价")))),
        'change_pct': _safe_float(row.get(_find_column_containing(df, ("涨跌幅",)))),
        'source': '东方财富飙升榜',
    } for i, (_, row) in enumerate(df.head(n).iterrows())]


def _get_xueqiu_hot_stocks(ak, n: int, enforce_rate_limit=None, set_user_agent=None) -> Optional[List[Dict[str, Any]]]:
    if set_user_agent:
        set_user_agent()
    if enforce_rate_limit:
        enforce_rate_limit()
    logger.info("[API调用] ak.stock_hot_follow_xq() 获取雪球关注榜...")
    df = ak.stock_hot_follow_xq(symbol='最热门')
    if df is None or df.empty:
        return None
    return [{
        'rank': idx, 'code': str(row.get('股票代码', '')).strip(),
        'name': str(row.get('股票简称', '')).strip(), 'price': _safe_float(row.get('最新价')),
        'change_pct': None, 'source': '雪球关注榜',
    } for idx, (_, row) in enumerate(df.head(n).iterrows(), 1)]


# ── Small helpers ────────────────────────────────────────────────────────


def _normalize_limit_time_value(value) -> str:
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none", "null", "-", "--"}:
        return ""
    if ":" in text:
        parts = text.split(":")
        try:
            return f"{int(parts[0]):02d}{int(parts[1]):02d}{int(parts[2]):02d}" if len(parts) > 2 else f"{int(parts[0]):02d}{int(parts[1]):02d}00"
        except (TypeError, ValueError):
            return text
    try:
        return f"{int(float(text)):06d}"
    except (TypeError, ValueError):
        digits = "".join(ch for ch in text if ch.isdigit())
        return digits.zfill(6) if digits else text


def _safe_float(value) -> Optional[float]:
    try:
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value) -> int:
    try:
        if pd.isna(value):
            return 0
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _find_first_column(df: pd.DataFrame, candidates: Tuple[str, ...]) -> Optional[str]:
    columns = [str(col) for col in df.columns]
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


def _find_column_containing(df: pd.DataFrame, keywords: Tuple[str, ...]) -> Optional[str]:
    for col in df.columns:
        if all(kw in str(col) for kw in keywords):
            return col
    return None