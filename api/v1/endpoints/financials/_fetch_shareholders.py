# -*- coding: utf-8 -*-
"""Shareholder structure: holder count, top-10, changes, actual controller."""
from __future__ import annotations

import concurrent.futures
import logging
import re
import threading
import time
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

from ._helpers import (
    _normalize_symbol, _to_em_symbol, _to_ts_code, _to_top_holder_symbol,
    _safe_float, _safe_str, _safe_pct, _safe_int_like, _parse_chinese_share_amount,
    _pick_col, _row_pick, _parse_date, _latest_quarter_dates,
)
from ._cache import _daily_cache_get, _daily_cache_put, SHAREHOLDER_CACHE_KEY

# 全市场慢接口的超时上限（秒）。stock_hold_control_cninfo / stock_hold_management_detail_em
# 均为无参全市场接口，网络不佳时可能数 tens 秒不返回，必须加超时保护。
_SLOW_MARKET_TIMEOUT = 30


class _ProcessTtlCache:
    """进程级 TTL 缓存（线程安全）。

    用于缓存全市场慢接口（如实际控制人持股变动）的结果——这类数据全市场共享、
    一天只变一次，没必要每查一只股票都重新拉全市场。
    """

    def __init__(self, ttl: int):
        self._ttl = ttl
        self._lock = threading.Lock()
        self._data: Any = None
        self._ts: float = 0.0

    def get(self) -> Any:
        with self._lock:
            if self._data is not None and (time.time() - self._ts) < self._ttl:
                return self._data
            return None

    def set(self, data: Any) -> None:
        with self._lock:
            self._data = data
            self._ts = time.time()


# 实际控制人持股变动：全市场数据，缓存 6 小时（一天最多刷新 4 次）。
_actual_controller_cache = _ProcessTtlCache(ttl=6 * 3600)


def _run_with_timeout(func, timeout: float, *args, **kwargs):
    """在子线程里执行 func，超过 timeout 秒返回 None（不抛错）。

    超时后不等待后台线程——Python 无法强制中断线程，但 akshare 的网络请求最终会因
    系统级 socket 超时而自行返回；这里优先保证调用方不被阻塞。
    """
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(func, *args, **kwargs)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        logger.warning("[Shareholder] %s 超时 (%ss)", getattr(func, "__name__", "call"), timeout)
        pool.shutdown(wait=False)  # 不阻塞，后台线程自行结束
        return None
    except Exception as exc:
        logger.warning("[Shareholder] %s 失败: %s", getattr(func, "__name__", "call"), exc)
        pool.shutdown(wait=False)
        return None
    finally:
        # 正常返回时也要释放线程（future 已完成，wait=True 不会阻塞）
        pool.shutdown(wait=False)

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
    # 主源是单股接口（快），降级源 stock_hold_management_detail_em 无参拉全市场（慢）。
    # 两者都加超时——降级源实测可能 45s 不返回，不加超时会拖住整个股东结构请求。
    candidates = [
        ("stock_shareholder_change_ths", {"symbol": code}, 15),
        ("stock_hold_management_detail_em", {}, _SLOW_MARKET_TIMEOUT),
    ]
    for func_name, kwargs, timeout in candidates:
        fn = getattr(ak, func_name, None)
        if fn is None:
            continue
        df = _run_with_timeout(fn, timeout, **kwargs)
        if df is None or df.empty:
            continue
        try:
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
    """实际控制人。

    ``stock_hold_control_cninfo`` 的 symbol 是控制类型枚举（"全部"/"实际控制人"/...），
    不是股票代码，akshare 没有单股实际控制人接口——传 "全部" 返回全市场（从 2010 年起
    所有公司）。该接口慢且全市场共享，故用进程级缓存（6h TTL）+ 超时保护：首只股票
    触发一次全量拉取，之后所有股票复用缓存，仅在缓存里按代码过滤。
    """
    import akshare as ak

    code = _normalize_symbol(symbol)
    errors: list[str] = []

    df = _actual_controller_cache.get()
    if df is None:
        df = _run_with_timeout(ak.stock_hold_control_cninfo, _SLOW_MARKET_TIMEOUT, symbol="全部")
        if df is not None and not df.empty:
            _actual_controller_cache.set(df)
        else:
            return None, None, ["stock_hold_control_cninfo:empty_or_timeout"]

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

    def _holder_count_task():
        """股东户数：akshare 为主，空则降级 tushare（降级逻辑保持在同一任务内）。"""
        payload, source, errs = _fetch_holder_count_from_akshare(code)
        if not payload:
            payload, source, tushare_errs = _fetch_holder_count_from_tushare(code)
            errs.extend(tushare_errs)
        return payload, source, errs

    # 四个子抓取互相独立，并发执行（原先串行，actual_controller 的全市场慢接口
    # 会阻塞其余三个）。每个子任务单独兜底——一个失败不影响其余结果回收。
    def _safe_result(fut, default):
        try:
            return fut.result()
        except Exception as exc:
            logger.warning("[Shareholder] sub-fetcher failed for %s: %s", code, exc)
            return default

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        fut_count = pool.submit(_holder_count_task)
        fut_top10 = pool.submit(_fetch_top10_holders, code)
        fut_changes = pool.submit(_fetch_holder_changes, code)
        fut_controller = pool.submit(_fetch_actual_controller, code)

        holder_payload, holder_source, holder_errors = _safe_result(fut_count, ({}, None, []))
        holders, institution_pct, top_source, top_errors = _safe_result(fut_top10, ([], None, None, []))
        changes, changes_source, changes_errors = _safe_result(fut_changes, ([], None, []))
        controller, controller_source, controller_errors = _safe_result(fut_controller, (None, None, []))

    result.update(holder_payload)
    result["errors"].extend(holder_errors)
    if holder_source:
        result["source_chain"].append(holder_source)

    result["top10_holders"] = holders
    result["institution_holding_pct"] = institution_pct
    result["errors"].extend(top_errors)
    if top_source:
        result["source_chain"].append(top_source)

    result["major_holder_changes"] = changes
    result["errors"].extend(changes_errors)
    if changes_source:
        result["source_chain"].append(changes_source)

    result["actual_controller"] = controller
    result["errors"].extend(controller_errors)
    if controller_source:
        result["source_chain"].append(controller_source)
    return result
