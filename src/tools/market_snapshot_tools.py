"""Source-level market observations for the LangGraph tool catalog.

The legacy market snapshot remains an API convenience view outside the Agent.
The Agent gets these direct reads instead, so it can choose which evidence is
needed and never receives a pre-composed market judgement as one tool result.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable

from src.tools._akshare import cached_call
from src.tools._market_snapshot import (
    _fetch_index_spot,
    _fetch_legu_activity,
    _fetch_pool,
    _fetch_sina_a_breadth,
    _parse_datetime,
)
from src.tools.base import ToolSpec, object_schema
from src.tools._trading_calendar import expected_trade_day


def _read_breadth(
    *,
    source_name: str,
    cache_key: str,
    fetcher: Callable[[], dict[str, Any]],
    use_cache: bool,
) -> dict[str, Any]:
    now = datetime.now().astimezone()
    data, cached = (
        cached_call(cache_key, fetcher, ttl_seconds=60)
        if use_cache
        else (fetcher(), False)
    )
    data_time = data.get("data_time")
    parsed_time = _parse_datetime(data_time)
    inferred_time = bool(data.get("data_time_inferred"))
    # A Friday close is the current snapshot on a weekend/holiday. Reuse
    # the same exchange calendar as the other direct market readers.
    expected_day = None
    if parsed_time is not None and not inferred_time:
        try:
            expected_day = expected_trade_day(now)
        except Exception:
            # Calendar failure cannot certify freshness (especially holidays).
            pass
    return {
        **data,
        "source": source_name,
        "source_scope": "market_breadth",
        "success": data.get("up_count") is not None and data.get("down_count") is not None,
        "partial": False,
        "errors": [],
        "warnings": [],
        "data_time": data_time,
        "data_time_provenance": (
            "inferred" if data_time and inferred_time else "source" if data_time else "unavailable"
        ),
        "data_time_note": (
            "新浪全市场表只返回时分秒；日期由本轮日期拼接，不能当作来源完整时间。"
            if data_time and inferred_time
            else (
                "来源未返回可用数据时间；_fetched_at 仅表示本服务获取时间。"
                if not data_time
                else None
            )
        ),
        "data_time_inferred": inferred_time,
        "is_stale": (
            parsed_time.date() < expected_day
            if parsed_time is not None and expected_day is not None
            else None
        ),
        "freshness_unknown": parsed_time is None or inferred_time or expected_day is None,
        "fallback_used": False,
        "_cached": cached,
        "_fetched_at": now.isoformat(),
    }


def read_market_breadth_legu(*, use_cache: bool = True) -> dict[str, Any]:
    """Read the Legu aggregate A-share breadth table only."""
    return _read_breadth(
        source_name="乐咕乐股市场活跃度",
        cache_key="market-breadth:legu:atomic:v1",
        fetcher=_fetch_legu_activity,
        use_cache=use_cache,
    )


def read_market_breadth_sina(*, use_cache: bool = True) -> dict[str, Any]:
    """Read the Sina A-share spot table and derive only its raw breadth counts."""
    return _read_breadth(
        source_name="新浪全A实时行情",
        cache_key="market-breadth:sina:atomic:v1",
        fetcher=_fetch_sina_a_breadth,
        use_cache=use_cache,
    )


def read_market_indices_sina(*, use_cache: bool = True) -> dict[str, Any]:
    """Read the single Sina index-spot endpoint without combining breadth/pools."""
    data, cached = (
        cached_call("market-indices:sina:atomic:v1", _fetch_index_spot, ttl_seconds=30, attempts=1)
        if use_cache
        else (_fetch_index_spot(), False)
    )
    now = datetime.now().astimezone()
    indices = data.get("indices") or {}
    return {
        "indices": indices,
        "total_amount": data.get("total_amount"),
        "total_amount_unit": data.get("total_amount_unit") or "亿元",
        "turnover_scope": data.get("turnover_scope") or "沪深市场",
        "source": "新浪实时指数行情",
        "source_scope": "major_index_quotes_and_turnover",
        "success": bool(indices),
        "partial": False,
        "errors": [] if indices else ["新浪实时指数行情没有返回主要指数"],
        "warnings": [],
        # The provider response carries no source timestamp; do not fabricate
        # one from local fetch time.
        "data_time": None,
        "is_stale": None,
        "freshness_unknown": True,
        "fallback_used": False,
        "_cached": cached,
        "_fetched_at": now.isoformat(),
    }


def _trade_date(value: str) -> str:
    normalized = re.sub(r"[^0-9]", "", str(value or ""))
    if not re.fullmatch(r"[0-9]{8}", normalized):
        raise ValueError("trade_date 必须为 YYYYMMDD")
    return normalized


def _read_limit_pool(
    *,
    function_name: str,
    pool_name: str,
    trade_date: str,
    use_cache: bool,
) -> dict[str, Any]:
    day = _trade_date(trade_date)
    now = datetime.now().astimezone()
    data, cached = (
        cached_call(
            f"market-pool:{function_name}:atomic:v1:{day}",
            lambda: _fetch_pool(function_name, day),
            ttl_seconds=60,
            attempts=1,
        )
        if use_cache
        else (_fetch_pool(function_name, day), False)
    )
    iso_date = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    return {
        "trade_date": iso_date,
        "pool": pool_name,
        "count": data.get("count"),
        "source": "东方财富涨跌停池/AKShare",
        "source_function": data.get("source") or function_name,
        "source_scope": "market_limit_pool",
        "success": data.get("count") is not None,
        "partial": False,
        "errors": [] if data.get("count") is not None else [f"东方财富没有返回{pool_name}"],
        "warnings": [],
        "data_time": iso_date,
        # This endpoint explicitly reads the requested historical date.
        # An older requested period is not a stale response to that request.
        "is_stale": False if data.get("count") is not None else None,
        "freshness_unknown": False,
        "fallback_used": False,
        "_cached": cached,
        "_fetched_at": now.isoformat(),
    }


def read_market_limit_up_pool_eastmoney(
    trade_date: str,
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    return _read_limit_pool(
        function_name="stock_zt_pool_em",
        pool_name="涨停池",
        trade_date=trade_date,
        use_cache=use_cache,
    )


def read_market_limit_down_pool_eastmoney(
    trade_date: str,
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    return _read_limit_pool(
        function_name="stock_zt_pool_dtgc_em",
        pool_name="跌停池",
        trade_date=trade_date,
        use_cache=use_cache,
    )


def read_market_broken_board_pool_eastmoney(
    trade_date: str,
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    return _read_limit_pool(
        function_name="stock_zt_pool_zbgc_em",
        pool_name="炸板池",
        trade_date=trade_date,
        use_cache=use_cache,
    )


TOOLS = (
    ToolSpec(
        name="read_market_breadth_legu",
        description=(
            "从乐咕乐股读取沪深 A 股市场活跃度表中的上涨、下跌、平盘、停牌及其原始涨跌停计数；"
            "不改用新浪兜底，也不计算赚钱效应、涨跌比或市场结论。"
        ),
        parameters=object_schema(),
        executor=read_market_breadth_legu,
        category="market",
    ),
    ToolSpec(
        name="read_market_breadth_sina",
        description=(
            "从新浪全 A 股实时行情读取上涨、下跌、平盘计数；"
            "这是独立来源，供模型在需要时与其他来源交叉核对，不作为乐咕的程序兜底。"
        ),
        parameters=object_schema(),
        executor=read_market_breadth_sina,
        category="market",
    ),
    ToolSpec(
        name="read_market_indices_sina",
        description=(
            "从新浪实时指数行情读取上证、深证、创业板、科创50的价格、涨跌和沪深成交额；"
            "不读取市场宽度、涨跌停池或指数日线。"
        ),
        parameters=object_schema(),
        executor=read_market_indices_sina,
        category="market",
    ),
    ToolSpec(
        name="read_market_limit_up_pool_eastmoney",
        description="从东方财富（AKShare）读取一个指定交易日的涨停池数量。",
        parameters=object_schema(
            {"trade_date": {"type": "string", "pattern": "^[0-9]{8}$", "description": "交易日，YYYYMMDD"}},
            ["trade_date"],
        ),
        executor=read_market_limit_up_pool_eastmoney,
        category="market",
    ),
    ToolSpec(
        name="read_market_limit_down_pool_eastmoney",
        description="从东方财富（AKShare）读取一个指定交易日的跌停池数量。",
        parameters=object_schema(
            {"trade_date": {"type": "string", "pattern": "^[0-9]{8}$", "description": "交易日，YYYYMMDD"}},
            ["trade_date"],
        ),
        executor=read_market_limit_down_pool_eastmoney,
        category="market",
    ),
    ToolSpec(
        name="read_market_broken_board_pool_eastmoney",
        description="从东方财富（AKShare）读取一个指定交易日的炸板池数量。",
        parameters=object_schema(
            {"trade_date": {"type": "string", "pattern": "^[0-9]{8}$", "description": "交易日，YYYYMMDD"}},
            ["trade_date"],
        ),
        executor=read_market_broken_board_pool_eastmoney,
        category="market",
    ),
)


__all__ = ["TOOLS"]
