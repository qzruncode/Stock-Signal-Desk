# -*- coding: utf-8 -*-
"""``get_stock_capital_flow`` — individual stock money-flow history."""

from __future__ import annotations

from typing import Any

import akshare as ak
import httpx
import pandas as pd

from src.tools._akshare import bare_symbol, cached_call, frame_records, source_meta
from src.tools.base import ToolSpec, object_schema

DESCRIPTION = (
    "获取个股近一段时间的主力、超大单、大单、中单和小单资金净流入及占比，"
    "并给出近 5/10/20 日主力净流入汇总；用于判断资金持续性，不等同于真实机构持仓。"
)


def _number(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _fetch_eastmoney_direct(code: str, market: str) -> pd.DataFrame:
    """Same official Eastmoney dataset as AKShare, with bounded timeout."""
    market_code = 1 if market == "sh" else 0
    response = httpx.get(
        "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
        params={
            "lmt": "0",
            "klt": "101",
            "secid": f"{market_code}.{code}",
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        },
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"},
        timeout=12,
    )
    response.raise_for_status()
    klines = ((response.json().get("data") or {}).get("klines") or [])
    columns = [
        "日期", "主力净流入-净额", "小单净流入-净额", "中单净流入-净额", "大单净流入-净额",
        "超大单净流入-净额", "主力净流入-净占比", "小单净流入-净占比", "中单净流入-净占比",
        "大单净流入-净占比", "超大单净流入-净占比", "收盘价", "涨跌幅", "_1", "_2",
    ]
    frame = pd.DataFrame([str(item).split(",") for item in klines], columns=columns)
    return frame.drop(columns=["_1", "_2"])


def get_stock_capital_flow(symbol: str, days: int = 20) -> dict[str, Any]:
    code = bare_symbol(symbol)
    market = "sh" if code.startswith(("6", "5", "9")) else "bj" if code.startswith(("8", "4")) else "sz"
    errors: list[str] = []
    fallback_used = False
    cached = False
    try:
        frame, cached = cached_call(
            f"stock_capital_flow:{code}",
            lambda: ak.stock_individual_fund_flow(stock=code, market=market),
            ttl_seconds=15 * 60,
        )
    except Exception as exc:
        # AKShare wrapper does not set a timeout in this endpoint. Retry the
        # same underlying dataset with a bounded HTTP client before reporting
        # an unavailable source.
        errors.append(f"AKShare 资金流接口失败: {exc}")
        fallback_used = True
        try:
            frame, cached = cached_call(
                f"stock_capital_flow:direct:{code}",
                lambda: _fetch_eastmoney_direct(code, market),
                ttl_seconds=15 * 60,
                attempts=1,
            )
        except Exception as fallback_exc:
            errors.append(f"东方财富限时直连降级失败: {fallback_exc}")
            frame = None
    records = frame_records(frame)
    limit = max(1, min(int(days), 100))
    recent = records[-limit:]
    main_keys = ("主力净流入-净额", "主力净流入", "主力净额")
    summaries: dict[str, Any] = {}
    for window in (5, 10, 20):
        values = [_number(row, *main_keys) for row in recent[-window:]]
        valid = [value for value in values if value is not None]
        summaries[f"main_net_inflow_{window}d"] = sum(valid) if valid else None
        summaries[f"positive_days_{window}d"] = sum(1 for value in valid if value > 0)
    return {
        "symbol": code,
        "market": market,
        "days": limit,
        "latest": recent[-1] if recent else {},
        "summary": summaries,
        "items": recent,
        "item_count": len(recent),
        "errors": errors if recent else [*errors, "AKShare 未返回个股资金流数据"],
        **source_meta(
            cached=cached,
            source="AKShare/东方财富资金流",
            available=bool(recent),
            fallback_used=fallback_used,
        ),
    }


TOOL = ToolSpec(
    name="get_stock_capital_flow",
    description=DESCRIPTION,
    parameters=object_schema(
        {
            "symbol": {"type": "string", "description": "股票代码或股票名称"},
            "days": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20, "description": "返回最近交易日数量"},
        },
        ["symbol"],
    ),
    executor=get_stock_capital_flow,
    category="market",
)
