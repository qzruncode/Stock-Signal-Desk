# -*- coding: utf-8 -*-
"""Real-time stock quote endpoints."""

from __future__ import annotations

import json
import logging
from datetime import datetime, time, timedelta

from fastapi import APIRouter, Query

from data_provider.akshare_fetcher import AkshareFetcher

logger = logging.getLogger(__name__)

router = APIRouter()

_fetcher: AkshareFetcher | None = None


def _quote_data_time(item: dict) -> str | None:
    return item.get("_fetched_at") or item.get("data_time") or item.get("trade_time")


def _mark_quote_freshness(items: list[dict], *, trading: bool, fallback_used: bool) -> list[dict]:
    today = datetime.now().date()
    for item in items:
        data_time = _quote_data_time(item)
        is_stale = False
        if data_time:
            try:
                parsed = datetime.fromisoformat(str(data_time).replace("Z", "+00:00"))
                if trading:
                    is_stale = parsed.date() < today
            except ValueError:
                pass
        item["data_time"] = data_time
        item["is_stale"] = is_stale
        item["fallback_used"] = fallback_used
    return items


def _get_fetcher() -> AkshareFetcher:
    global _fetcher
    if _fetcher is None:
        from src.config import get_config
        from src.patches.eastmoney_patch import eastmoney_patch
        if get_config().enable_eastmoney_patch:
            eastmoney_patch()
        _fetcher = AkshareFetcher()
    return _fetcher


def _is_trading_hours() -> bool:
    """判断当前是否在 A 股交易时段（周一至周五 9:30-11:30, 13:00-15:00）。"""
    now = datetime.now()
    if now.weekday() >= 5:  # 周末
        return False
    t = now.time()
    return (time(9, 30) <= t <= time(11, 30)) or (time(13, 0) <= t <= time(15, 0))


def _last_trading_day() -> datetime:
    """返回最近一个交易日的起始时间（0:00），用于判断缓存是否来自最近一次交易。

    例如：周二 8:00 → 返回周一 0:00，周一写入的缓存 >= 周一 0:00 → 有效。
    """
    now = datetime.now()
    today = now.date()
    # 如果今天是交易日且已过 9:30（交易已开始或即将开始），最近交易日就是今天
    if today.weekday() < 5 and now.time() >= time(9, 30):
        return datetime.combine(today, time(0, 0))
    # 否则往前找最近一个交易日
    d = today - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return datetime.combine(d, time(0, 0))


@router.get(
    "/realtime",
    summary="Get real-time stock quotes",
)
def get_realtime_quotes(
    symbol: str = Query(default="", description="股票代码（如 600519）"),
    symbols: list[str] | None = Query(default=None, description="股票代码列表"),
    market: str = Query(default="A股", description="市场（A股 / 港股 / 美股），默认 A股"),
):
    """获取股票实时行情数据。

    非交易时段：优先从数据库缓存读取，首次访问时自动拉取并缓存。
    交易时段：每次实时拉取并更新缓存。
    """
    symbols_list = list(symbols) if symbols else []
    if symbol and symbol not in symbols_list:
        symbols_list.insert(0, symbol)

    if not symbols_list:
        return {"items": [], "total": 0}

    from src.storage import DatabaseManager
    db = DatabaseManager.get_instance()
    trading = _is_trading_hours()

    # --- 非交易时段：优先从缓存读取（仅使用上次收盘后写入的缓存）---
    if not trading:
        cached = db.get_quote_snapshots(symbols_list, since=_last_trading_day())
        if cached:
            cached_symbols = set(cached.keys())
            missing = [s for s in symbols_list if s not in cached_symbols]
            results = [
                {**cached[code], '_cached': True}
                for code in symbols_list if code in cached
            ]
            if missing:
                logger.info(f"[行情缓存] 缓存命中 {len(cached)} 只, 需拉取 {len(missing)} 只")
            else:
                logger.info(f"[行情缓存] 全部命中 {len(cached)} 只，跳过 API")
                return {
                    "items": _mark_quote_freshness(results, trading=trading, fallback_used=True),
                    "total": len(results),
                    "data_time": max((_quote_data_time(item) for item in results if _quote_data_time(item)), default=None),
                    "is_stale": any(item.get("is_stale") for item in results),
                    "fallback_used": True,
                }
            symbols_list = missing
        else:
            logger.info("[行情缓存] 无缓存，需全量拉取")
            results = []

    # --- 拉取实时数据 ---
    fetcher = _get_fetcher()
    fetch_results = []
    now_ts = datetime.now().isoformat()
    for sym in symbols_list:
        quote = fetcher.get_realtime_quote(sym)
        if quote and quote.has_basic_data():
            d = quote.to_dict()
            d['_fetched_at'] = now_ts
            fetch_results.append(d)
            # 写入缓存
            db.save_quote_snapshot(sym, json.dumps(d, ensure_ascii=False))

    if not trading:
        # 合并缓存结果和拉取结果
        merged = _mark_quote_freshness(results + fetch_results, trading=trading, fallback_used=bool(results))
        return {
            "items": merged,
            "total": len(merged),
            "data_time": max((_quote_data_time(item) for item in merged if _quote_data_time(item)), default=None),
            "is_stale": any(item.get("is_stale") for item in merged),
            "fallback_used": bool(results),
        }

    marked = _mark_quote_freshness(fetch_results, trading=trading, fallback_used=False)
    return {
        "items": marked,
        "total": len(marked),
        "data_time": max((_quote_data_time(item) for item in marked if _quote_data_time(item)), default=None),
        "is_stale": any(item.get("is_stale") for item in marked),
        "fallback_used": False,
    }
