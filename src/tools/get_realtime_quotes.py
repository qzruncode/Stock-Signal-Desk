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
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time, timedelta
from typing import Any

from data_provider.akshare_fetcher import AkshareFetcher
from src.tools.base import ToolSpec, object_schema
from src.tools._trading_calendar import expected_trade_day, is_trading_time, trade_dates

logger = logging.getLogger(__name__)

REALTIME_QUOTES_DESCRIPTION = (
    "获取单只或多只 A 股的实时报价，包括最新价、涨跌幅、成交量、成交额、换手率、市值等。"
    "非交易时段自动复用最近交易日的缓存快照。"
)

_fetcher: AkshareFetcher | None = None


def _quote_data_time(item: dict) -> str | None:
    return item.get("trade_time") or item.get("data_time") or item.get("_fetched_at")


def _get_fetcher() -> AkshareFetcher:
    global _fetcher
    if _fetcher is None:
        from src.config import get_config
        from src.patches.eastmoney_patch import eastmoney_patch
        if get_config().enable_eastmoney_patch:
            eastmoney_patch()
        _fetcher = AkshareFetcher()
    return _fetcher


_is_trading_hours = is_trading_time


def _last_trading_day() -> datetime:
    """返回最近一个交易日的起始时间（0:00），用于判断缓存是否来自最近一次交易。

    例如：周二 8:00 → 返回周一 0:00，周一写入的缓存 >= 周一 0:00 → 有效。
    """
    now = datetime.now().astimezone()
    try:
        day = expected_trade_day(now, trade_dates())
    except Exception:
        day = now.date()
        if day.weekday() >= 5 or now.time() < time(9, 15):
            day -= timedelta(days=1)
            while day.weekday() >= 5:
                day -= timedelta(days=1)
    return datetime.combine(day, time(0, 0))


def _mark_quote_freshness(items: list[dict], *, trading: bool, fallback_used: bool) -> list[dict]:
    now = datetime.now().astimezone()
    expected_day = _last_trading_day().date()
    for item in items:
        data_time = _quote_data_time(item)
        is_stale: bool | None = None
        if data_time:
            try:
                parsed = datetime.fromisoformat(str(data_time).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=now.tzinfo)
                is_stale = parsed.date() < expected_day
                if trading and parsed.date() == expected_day:
                    is_stale = (now - parsed).total_seconds() > 15 * 60
            except ValueError:
                is_stale = None
        item["data_time"] = data_time
        item["is_stale"] = is_stale
        source = str(item.get("source") or "")
        item["fallback_used"] = source != "eastmoney_push" if source else fallback_used
    return items


def _build_response(
    items: list[dict],
    *,
    requested: list[str],
    invalid: list[str] | None = None,
) -> dict[str, Any]:
    """组装统一的行情响应体。"""
    marked = items  # freshness 已由调用方在合并后标记
    returned = {str(item.get("code")) for item in marked}
    invalid_set = set(invalid or [])
    missing = [symbol for symbol in requested if symbol not in returned and symbol not in invalid_set]
    errors = []
    if invalid:
        errors.append(f"无法识别或不支持的证券代码: {', '.join(invalid)}")
    if missing:
        errors.append(f"实时行情无数据: {', '.join(missing)}")
    success = bool(marked)
    freshness_states = [item.get("is_stale") for item in marked]
    if any(state is True for state in freshness_states):
        is_stale: bool | None = True
    elif freshness_states and all(state is False for state in freshness_states):
        is_stale = False
    else:
        is_stale = None
    return {
        "success": success,
        "partial": success and bool(errors),
        "items": marked,
        "total": len(marked),
        "data_time": max((_quote_data_time(item) for item in marked if _quote_data_time(item)), default=None),
        "is_stale": is_stale,
        "freshness_unknown": is_stale is None,
        "fallback_used": any(item.get("fallback_used") for item in marked),
        "_cached": bool(marked) and all(item.get("_cached") for item in marked),
        "source": sorted({str(item.get("source")) for item in marked if item.get("source")}),
        "requested_symbols": requested,
        "missing_symbols": missing,
        "invalid_symbols": invalid or [],
        "volume_unit": "股",
        "amount_unit": "元",
        "market_value_unit": "元",
        "errors": errors,
    }


def get_realtime_quotes(symbols: list[str]) -> dict[str, Any]:
    """获取 A 股实时行情数据（交易时段感知缓存）。

    Args:
        symbols: 股票代码列表（已去重、保序）。为空时返回空结果。

    Returns:
        dict: {items, total, data_time, is_stale, fallback_used}
    """
    if not symbols:
        return _build_response([], requested=[])

    requested = list(dict.fromkeys(str(symbol).strip() for symbol in symbols if str(symbol).strip()))[:20]
    valid = [symbol for symbol in requested if re.fullmatch(r"\d{6}", symbol)]
    invalid = [symbol for symbol in requested if symbol not in valid]
    if not valid:
        return _build_response([], requested=requested, invalid=invalid)
    symbols = valid

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
                marked = _mark_quote_freshness(results, trading=trading, fallback_used=False)
                return _build_response(marked, requested=requested, invalid=invalid)
            symbols = missing
        else:
            logger.info("[行情缓存] 无缓存，需全量拉取")
            results = []

    # --- 拉取实时数据 ---
    fetcher = _get_fetcher()
    fetch_results_by_symbol: dict[str, dict[str, Any]] = {}
    now_ts = datetime.now().astimezone().isoformat()
    with ThreadPoolExecutor(max_workers=min(8, len(symbols)), thread_name_prefix="realtime-quote") as executor:
        futures = {executor.submit(fetcher.get_realtime_quote, sym): sym for sym in symbols}
        for future in as_completed(futures):
            sym = futures[future]
            try:
                quote = future.result()
            except Exception as exc:
                logger.warning("[实时行情] %s 获取失败: %s", sym, exc)
                continue
            if quote and quote.has_basic_data():
                data = quote.to_dict()
                data['_fetched_at'] = now_ts
                data['_cached'] = False
                fetch_results_by_symbol[sym] = data
                db.save_quote_snapshot(sym, json.dumps(data, ensure_ascii=False))
    fetch_results = [fetch_results_by_symbol[symbol] for symbol in symbols if symbol in fetch_results_by_symbol]

    if not trading:
        # 合并缓存结果和拉取结果
        combined = {str(item.get("code")): item for item in results + fetch_results}
        merged = [combined[symbol] for symbol in requested if symbol in combined]
        marked = _mark_quote_freshness(merged, trading=trading, fallback_used=False)
        return _build_response(marked, requested=requested, invalid=invalid)

    marked = _mark_quote_freshness(fetch_results, trading=trading, fallback_used=False)
    return _build_response(marked, requested=requested, invalid=invalid)


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
