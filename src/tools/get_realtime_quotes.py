# -*- coding: utf-8 -*-
"""``get_realtime_quotes`` tool — A 股实时行情获取（交易时段感知缓存）。

工具业务逻辑统一收口到 src/tools/，与 FastAPI 路由层解耦：
api/v1/endpoints/quotes.py 仅保留薄路由，从此处导入 get_realtime_quotes。

策略：
- 交易时段（周一至周五 9:30-11:30, 13:00-15:00）：逐只实时拉取并回写缓存。
- 非交易时段：优先读数据库缓存（仅采用最近交易日起写入的快照）；缺失部分才
  拉取并补缓存，避免夜/周末重复打 API。
- freshness：交易时段以"今天"为新鲜度下限；非交易时段以"最近交易日"为下限，
  否则周末/夜间会把上一交易日收盘缓存误判为 stale。

仅支持 A 股（数据源多级故障切换，见 data_provider/fetchers/realtime.py）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, time, timedelta
from typing import Any

from data_provider.akshare_fetcher import AkshareFetcher
from src.tools.base import ToolSpec, object_schema

logger = logging.getLogger(__name__)

REALTIME_QUOTES_DESCRIPTION = (
    "获取单只或多只 A 股的实时报价，包括最新价、涨跌幅、成交量、成交额、换手率、市值等。"
    "非交易时段自动复用最近交易日的缓存快照。"
)

_fetcher: AkshareFetcher | None = None


def _quote_data_time(item: dict) -> str | None:
    return item.get("_fetched_at") or item.get("data_time") or item.get("trade_time")


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


def _mark_quote_freshness(items: list[dict], *, trading: bool, fallback_used: bool) -> list[dict]:
    today = datetime.now().date()
    for item in items:
        data_time = _quote_data_time(item)
        is_stale = False
        if data_time:
            try:
                parsed = datetime.fromisoformat(str(data_time).replace("Z", "+00:00"))
                # 交易时段：行情应反映当日，早于今天即视为陈旧。
                # 非交易时段：缓存来自最近一次交易，用"最近交易日"而非"今天"
                # 作新鲜度下限——否则周末/夜间会把上一交易日收盘缓存全部误判为 stale。
                if trading:
                    is_stale = parsed.date() < today
                else:
                    is_stale = parsed.date() < _last_trading_day().date()
            except ValueError:
                pass
        item["data_time"] = data_time
        item["is_stale"] = is_stale
        item["fallback_used"] = fallback_used
    return items


def _build_response(items: list[dict], *, fallback_used: bool) -> dict[str, Any]:
    """组装统一的行情响应体。"""
    marked = items  # freshness 已由调用方在合并后标记
    return {
        "items": marked,
        "total": len(marked),
        "data_time": max((_quote_data_time(item) for item in marked if _quote_data_time(item)), default=None),
        "is_stale": any(item.get("is_stale") for item in marked),
        "fallback_used": fallback_used,
    }


def get_realtime_quotes(symbols: list[str]) -> dict[str, Any]:
    """获取 A 股实时行情数据（交易时段感知缓存）。

    Args:
        symbols: 股票代码列表（已去重、保序）。为空时返回空结果。

    Returns:
        dict: {items, total, data_time, is_stale, fallback_used}
    """
    if not symbols:
        return {"items": [], "total": 0}

    from src.storage import DatabaseManager
    db = DatabaseManager.get_instance()
    trading = _is_trading_hours()

    # --- 非交易时段：优先从缓存读取（仅使用上次收盘后写入的缓存）---
    if not trading:
        cached = db.get_quote_snapshots(symbols, since=_last_trading_day())
        if cached:
            cached_symbols = set(cached.keys())
            missing = [s for s in symbols if s not in cached_symbols]
            results = [
                {**cached[code], '_cached': True}
                for code in symbols if code in cached
            ]
            if missing:
                logger.info(f"[行情缓存] 缓存命中 {len(cached)} 只, 需拉取 {len(missing)} 只")
            else:
                logger.info(f"[行情缓存] 全部命中 {len(cached)} 只，跳过 API")
                marked = _mark_quote_freshness(results, trading=trading, fallback_used=True)
                return _build_response(marked, fallback_used=True)
            symbols = missing
        else:
            logger.info("[行情缓存] 无缓存，需全量拉取")
            results = []

    # --- 拉取实时数据 ---
    fetcher = _get_fetcher()
    fetch_results = []
    now_ts = datetime.now().isoformat()
    for sym in symbols:
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
        return _build_response(merged, fallback_used=bool(results))

    marked = _mark_quote_freshness(fetch_results, trading=trading, fallback_used=False)
    return _build_response(marked, fallback_used=False)


def _execute(symbols: str) -> dict[str, Any]:
    from src.tools.symbols import resolve_symbols_csv

    return get_realtime_quotes(resolve_symbols_csv(symbols))


TOOL = ToolSpec(
    name="get_realtime_quotes",
    description=REALTIME_QUOTES_DESCRIPTION,
    parameters=object_schema({
        "symbols": {"type": "string", "description": "股票代码或名称，多个用逗号分隔，如 600519,000001"},
    }, ["symbols"]),
    executor=_execute,
    category="data",
)
