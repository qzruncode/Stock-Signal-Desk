# -*- coding: utf-8 -*-
"""Market-level data fetchers — stats, all A-share list, index, limit-up pool, hot stocks."""

from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..circuit_breaker import RealtimeCircuitBreaker

logger = logging.getLogger(__name__)
_spot_em_circuit_breaker = RealtimeCircuitBreaker()
_name_circuit_breaker = RealtimeCircuitBreaker()
_last_a_stock_list_error: str | None = None


def reset_a_stock_list_fetch_state() -> None:
    global _last_a_stock_list_error
    _last_a_stock_list_error = None
    _spot_em_circuit_breaker.reset()
    _name_circuit_breaker.reset()


def get_last_a_stock_list_error() -> str | None:
    return _last_a_stock_list_error


def _remember_a_stock_list_error(source: str, error: Any) -> None:
    global _last_a_stock_list_error
    _last_a_stock_list_error = f"{source}: {str(error)[:180]}"


def _parse_date(value: Any) -> Optional[date]:
    if value is None or str(value).strip() in ("", "nan", "NaT"):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _safe_intish(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in ("", "-", "nan", "None"):
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


# ── Market stats ─────────────────────────────────────────────────────────


def get_market_stats(enforce_rate_limit=None, set_user_agent=None) -> Optional[Dict[str, Any]]:
    """获取市场涨跌统计，使用东财 spot 接口短重试。"""
    import akshare as ak

    for attempt in range(1, 3):
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
            logger.warning("[Akshare] 东财接口获取市场统计失败(%d/2): %s", attempt, e)
        if attempt < 2:
            time.sleep(1.5 * attempt)

    return None


def _calc_market_stats(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """从行情 DataFrame 计算涨跌统计。"""
    df = df.copy()

    code_col = next((c for c in ["代码", "股票代码", "ts_code", "stock_code"] if c in df.columns), None)
    name_col = next((c for c in ["名称", "股票名称", "name"] if c in df.columns), None)
    close_col = next((c for c in ["最新价", "close", "lastPrice"] if c in df.columns), None)
    pre_close_col = next((c for c in ["昨收", "昨日收盘", "pre_close", "lastClose"] if c in df.columns), None)
    amount_col = next((c for c in ["成交额", "amount"] if c in df.columns), None)

    if not all([code_col, name_col, close_col, pre_close_col]):
        logger.warning("[Akshare] 市场统计字段缺失: columns=%s", list(df.columns))
        return None

    current = pd.to_numeric(df[close_col], errors="coerce")
    pre_close = pd.to_numeric(df[pre_close_col], errors="coerce")
    amount = pd.to_numeric(df[amount_col], errors="coerce") if amount_col else pd.Series(1, index=df.index)
    valid = current.notna() & pre_close.notna() & (current > 0) & (amount.fillna(0) != 0)

    codes = df[code_col].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    names = df[name_col].astype(str)
    ratios = pd.Series(0.10, index=df.index, dtype=float)
    ratios.loc[codes.str.startswith(("8", "9"))] = 0.30
    ratios.loc[codes.str.startswith(("300", "301", "688"))] = 0.20
    ratios.loc[names.str.contains("ST", case=False, na=False)] = 0.05

    limit_up_price = np.floor(pre_close * (1 + ratios) * 100 + 0.5) / 100.0
    limit_down_price = np.floor(pre_close * (1 - ratios) * 100 + 0.5) / 100.0

    limit_up_count = int((valid & np.isclose(current, limit_up_price, atol=0.001)).sum())
    limit_down_count = int((valid & np.isclose(current, limit_down_price, atol=0.001)).sum())
    up_count = int((valid & (current > pre_close)).sum())
    down_count = int((valid & (current < pre_close)).sum())
    flat_count = int((valid & (current == pre_close)).sum())

    stats = {
        "up_count": up_count,
        "down_count": down_count,
        "flat_count": flat_count,
        "limit_up_count": limit_up_count,
        "limit_down_count": limit_down_count,
        "total_amount": 0.0,
    }
    if amount_col and amount_col in df.columns:
        df[amount_col] = pd.to_numeric(df[amount_col], errors="coerce")
        stats["total_amount"] = df[amount_col].sum() / 1e8
    return stats


# ── All A-share list ────────────────────────────────────────────────────


def get_all_a_stocks(enforce_rate_limit=None, set_user_agent=None) -> Optional[List[Dict[str, Any]]]:
    """获取全部 A 股股票列表（含基础元数据）。

    主方案：单次股票实时行情接口（代码/名称）。
    兜底方案：交易所名录或代码名称接口。
    """
    import akshare as ak

    global _last_a_stock_list_error

    def _normalize_a_stock_code(value: Any) -> str:
        code = str(value or "").strip()
        if code.endswith(".0"):
            code = code[:-2]
        if code.isdigit() and len(code) < 6:
            code = code.zfill(6)
        return code

    def _build_stock_items(
        df: pd.DataFrame,
        *,
        col_code: str,
        col_name: str,
        col_sector: str | None = None,
        col_ipo_date: str | None = None,
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            code_str = _normalize_a_stock_code(row.get(col_code, ""))
            name_str = str(row.get(col_name, "")).strip()
            if not code_str or len(code_str) < 6:
                continue
            market = _classify_a_stock_market(code_str)
            items.append(
                {
                    "code": code_str,
                    "name": name_str,
                    "market": market,
                    "sector": (
                        str(row.get(col_sector, "")).strip()
                        if col_sector and col_sector in row.index and str(row.get(col_sector, "")).strip() != "nan"
                        else None
                    ),
                    "ipo_date": (
                        _parse_date(row.get(col_ipo_date)) if col_ipo_date and col_ipo_date in row.index else None
                    ),
                }
            )
        return items

    def _fetch_exchange_lists() -> List[Dict[str, Any]]:
        frames: list[tuple[pd.DataFrame, dict[str, str]]] = []

        def _append(name: str, fetch, mapping: dict[str, str]) -> None:
            try:
                if set_user_agent:
                    set_user_agent()
                if enforce_rate_limit:
                    enforce_rate_limit()
                df = fetch()
                if df is None or df.empty:
                    logger.warning("[StocksSync] %s 返回空", name)
                    return
                logger.info("[StocksSync] %s: %d 条", name, len(df))
                frames.append((df, mapping))
            except Exception as exc:
                _remember_a_stock_list_error(name, exc)
                logger.warning("[StocksSync] %s 失败: %s", name, str(exc)[:120])

        _append(
            "上交所主板名录",
            lambda: ak.stock_info_sh_name_code(symbol="主板A股"),
            {"code": "证券代码", "name": "证券简称", "ipo_date": "上市日期"},
        )
        _append(
            "上交所科创板名录",
            lambda: ak.stock_info_sh_name_code(symbol="科创板"),
            {"code": "证券代码", "name": "证券简称", "ipo_date": "上市日期"},
        )
        _append(
            "深交所A股名录",
            lambda: ak.stock_info_sz_name_code(symbol="A股列表"),
            {"code": "A股代码", "name": "A股简称", "sector": "所属行业", "ipo_date": "A股上市日期"},
        )
        _append(
            "北交所名录",
            lambda: ak.stock_info_bj_name_code(),
            {"code": "证券代码", "name": "证券简称", "sector": "所属行业", "ipo_date": "上市日期"},
        )

        merged: dict[str, Dict[str, Any]] = {}
        for df, mapping in frames:
            for item in _build_stock_items(
                df,
                col_code=mapping["code"],
                col_name=mapping["name"],
                col_sector=mapping.get("sector"),
                col_ipo_date=mapping.get("ipo_date"),
            ):
                merged[item["code"]] = item

        return [merged[code] for code in sorted(merged)]

    # ── 主方案: 单次 spot 接口 → code + name ──
    if _spot_em_circuit_breaker.is_available("stock_zh_a_spot_em"):
        try:
            if set_user_agent:
                set_user_agent()
            if enforce_rate_limit:
                enforce_rate_limit()
            logger.info("[StocksSync] 获取东财实时行情...")
            spot_df = ak.stock_zh_a_spot_em()
            if spot_df is not None and not spot_df.empty:
                _last_a_stock_list_error = None
                _spot_em_circuit_breaker.record_success("stock_zh_a_spot_em")
                logger.info("[StocksSync] 实时行情: %d 条", len(spot_df))
                results = _build_stock_items(
                    spot_df,
                    col_code="代码",
                    col_name="名称",
                )
                logger.info("[StocksSync] 完成: %d 只 A 股（含北交所、ST）", len(results))
                return results
            _spot_em_circuit_breaker.record_failure("stock_zh_a_spot_em")
            _remember_a_stock_list_error("东财实时行情返回空", "empty dataframe")
            logger.warning("[StocksSync] 实时行情返回空，退化到代码名称接口")
        except Exception as spot_error:
            _spot_em_circuit_breaker.record_failure("stock_zh_a_spot_em", spot_error)
            _remember_a_stock_list_error("东财实时行情", spot_error)
            logger.warning("[StocksSync] 东财实时行情失败，退化到代码名称接口: %s", str(spot_error)[:120])
    else:
        logger.warning("[StocksSync] 东财实时行情熔断中，跳过 spot 接口")

    exchange_results = _fetch_exchange_lists()
    if exchange_results:
        _last_a_stock_list_error = None
        logger.info("[StocksSync] 完成(交易所名录): %d 只 A 股（含基础资料）", len(exchange_results))
        return exchange_results

    # ── 兜底: spot 接口异常/空/熔断，退化到 code+name 接口 ──
    if not _name_circuit_breaker.is_available("stock_info_a_code_name"):
        _remember_a_stock_list_error("代码名称接口", "熔断中")
        logger.warning("[StocksSync] 代码名称接口熔断中，跳过兜底")
        return None

    try:
        if set_user_agent:
            set_user_agent()
        if enforce_rate_limit:
            enforce_rate_limit()

        name_df = ak.stock_info_a_code_name()
        if name_df is None or name_df.empty:
            _name_circuit_breaker.record_failure("stock_info_a_code_name")
            _remember_a_stock_list_error("代码名称接口返回空", "empty dataframe")
            return None

        _name_circuit_breaker.record_success("stock_info_a_code_name")
        _last_a_stock_list_error = None
        results = _build_stock_items(name_df, col_code="code", col_name="name")
        logger.info("[StocksSync] 完成(降级): %d 只 A 股（仅代码名称）", len(results))
        return results
    except Exception as e:
        _name_circuit_breaker.record_failure("stock_info_a_code_name", e)
        _remember_a_stock_list_error("代码名称接口", e)
        logger.error("[StocksSync] 获取全 A 股列表失败: %s", e, exc_info=True)
        return None


def _classify_a_stock_market(code: str) -> str:
    code = code.strip()
    if code.startswith("68"):
        return "kcb"
    if code.startswith(("300", "301")):
        return "cyb"
    if code.startswith(("8", "9")) and len(code) == 6:
        return "bj"
    if code.startswith("60"):
        return "sh"
    if code.startswith(("000", "001", "002", "003")):
        return "sz"
    return "other"


# ── Limit-up pool ────────────────────────────────────────────────────────


def get_limit_up_pool(
    date: Optional[str] = None,
    n: int = 20,
    enforce_rate_limit=None,
    set_user_agent=None,
) -> Optional[List[Dict[str, Any]]]:
    """获取涨停池，按连板数和封板时间排序。"""
    import akshare as ak

    query_date = date or datetime.now().strftime("%Y%m%d")
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
        for col in ("连板数", "封板资金", "成交额", "换手率", "涨跌幅"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "首次封板时间" in df.columns:
            df["首次封板时间"] = df["首次封板时间"].map(_normalize_limit_time_value)
            df["_首次封板时间排序"] = df["首次封板时间"].where(df["首次封板时间"] != "", "999999")
        sort_cols = [col for col in ("连板数", "_首次封板时间排序") if col in df.columns]
        if sort_cols:
            ascending = [False if col == "连板数" else True for col in sort_cols]
            df = df.sort_values(sort_cols, ascending=ascending)

        rows: List[Dict[str, Any]] = []
        for _, row in df.head(n).iterrows():
            rows.append(
                {
                    "code": str(row.get("代码", "")).strip(),
                    "name": str(row.get("名称", "")).strip(),
                    "change_pct": _safe_float(row.get("涨跌幅")),
                    "price": _safe_float(row.get("最新价")),
                    "amount": _safe_float(row.get("成交额")),
                    "turnover_rate": _safe_float(row.get("换手率")),
                    "seal_amount": _safe_float(row.get("封板资金")),
                    "first_limit_time": str(row.get("首次封板时间", "")).strip(),
                    "last_limit_time": _normalize_limit_time_value(row.get("最后封板时间")),
                    "break_count": _safe_int(row.get("炸板次数")),
                    "limit_stat": str(row.get("涨停统计", "")).strip(),
                    "consecutive_boards": _safe_int(row.get("连板数")),
                    "industry": str(row.get("所属行业", "")).strip(),
                }
            )
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


def _get_eastmoney_hot_stocks(
    ak, n: int, enforce_rate_limit=None, set_user_agent=None
) -> Optional[List[Dict[str, Any]]]:
    if set_user_agent:
        set_user_agent()
    if enforce_rate_limit:
        enforce_rate_limit()
    logger.info("[API调用] ak.stock_hot_rank_em() 获取东方财富人气股...")
    df = ak.stock_hot_rank_em()
    if df is None or df.empty:
        return None
    return [
        {
            "rank": _safe_int(row.get("当前排名")),
            "code": str(row.get("代码", "")).strip(),
            "name": str(row.get("股票名称", "")).strip(),
            "price": _safe_float(row.get("最新价")),
            "change_pct": _safe_float(row.get("涨跌幅")),
            "source": "东方财富人气榜",
        }
        for _, row in df.head(n).iterrows()
    ]


def _get_eastmoney_hot_up_stocks(
    ak, n: int, enforce_rate_limit=None, set_user_agent=None
) -> Optional[List[Dict[str, Any]]]:
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
    return [
        {
            "rank": (
                _safe_int(row.get(_find_first_column(df, ("当前排名", "排名", "序号"))))
                if _find_first_column(df, ("当前排名", "排名", "序号"))
                else i + 1
            ),
            "code": str(row.get(code_col, "")).strip(),
            "name": str(row.get(name_col, "")).strip(),
            "price": _safe_float(row.get(_find_first_column(df, ("最新价", "现价")))),
            "change_pct": _safe_float(row.get(_find_column_containing(df, ("涨跌幅",)))),
            "source": "东方财富飙升榜",
        }
        for i, (_, row) in enumerate(df.head(n).iterrows())
    ]


def _get_xueqiu_hot_stocks(ak, n: int, enforce_rate_limit=None, set_user_agent=None) -> Optional[List[Dict[str, Any]]]:
    if set_user_agent:
        set_user_agent()
    if enforce_rate_limit:
        enforce_rate_limit()
    logger.info("[API调用] ak.stock_hot_follow_xq() 获取雪球关注榜...")
    df = ak.stock_hot_follow_xq(symbol="最热门")
    if df is None or df.empty:
        return None
    return [
        {
            "rank": idx,
            "code": str(row.get("股票代码", "")).strip(),
            "name": str(row.get("股票简称", "")).strip(),
            "price": _safe_float(row.get("最新价")),
            "change_pct": None,
            "source": "雪球关注榜",
        }
        for idx, (_, row) in enumerate(df.head(n).iterrows(), 1)
    ]


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
            return (
                f"{int(parts[0]):02d}{int(parts[1]):02d}{int(parts[2]):02d}"
                if len(parts) > 2
                else f"{int(parts[0]):02d}{int(parts[1]):02d}00"
            )
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
