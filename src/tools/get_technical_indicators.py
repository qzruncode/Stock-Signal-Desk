# -*- coding: utf-8 -*-
"""``get_technical_indicators`` — deterministic indicators over daily K-line."""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.tools._akshare import bare_symbol, json_value
from src.tools._kline import get_kline
from src.tools.base import ToolSpec, object_schema

DESCRIPTION = (
    "基于前复权日线计算 MA、EMA、MACD、RSI、ATR、布林带、20/60 日高低位、当日量/前5日均量和阶段收益；"
    "成交量统一为股，盘中指标会明确标记使用了未完成的当日 K 线。"
    "结果为确定性计算，不做买卖结论，适合技术趋势、波动和位置分析。"
)


def _round(value: Any, digits: int = 4) -> Any:
    value = json_value(value)
    return round(float(value), digits) if isinstance(value, (int, float)) else value


def get_technical_indicators(symbol: str, count: int = 120) -> dict[str, Any]:
    code = bare_symbol(symbol)
    try:
        raw = get_kline(code, count=max(80, min(int(count), 250)), use_cache=True)
    except Exception as exc:
        return {"symbol": code, "indicators": {}, "errors": [str(exc)], "source": "K线多源链", "success": False, "is_stale": True, "fallback_used": True}
    rows = raw.get("data") or []
    if len(rows) < 30:
        return {"symbol": code, "indicators": {}, "errors": ["有效 K 线少于 30 条，无法稳定计算技术指标"], "success": False, **{k: raw.get(k) for k in ("source", "data_time", "is_stale", "fallback_used", "_cached")}}

    frame = pd.DataFrame(rows)
    if "date" in frame.columns:
        frame = frame.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        frame[col] = pd.to_numeric(frame.get(col), errors="coerce")
    frame = frame.dropna(subset=["high", "low", "close"])
    if len(frame) < 30:
        return {"symbol": code, "indicators": {}, "errors": ["有效 OHLC K 线少于 30 条"], "success": False, **{k: raw.get(k) for k in ("source", "data_time", "is_stale", "fallback_used", "_cached")}}
    close = frame["close"]
    high = frame["high"]
    low = frame["low"]
    volume = frame["volume"]
    for window in (5, 10, 20, 60):
        frame[f"ma{window}"] = close.rolling(window).mean()
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd = (dif - dea) * 2
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    relative_strength = gain / loss.where(loss != 0)
    rsi = 100 - 100 / (1 + relative_strength)
    rsi = rsi.mask((loss == 0) & (gain > 0), 100.0)
    rsi = rsi.mask((loss == 0) & (gain == 0), 50.0)
    prev_close = close.shift(1)
    true_range = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = true_range.ewm(alpha=1 / 14, adjust=False).mean()
    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    latest = frame.iloc[-1]
    latest_close = float(latest["close"])

    def change(window: int) -> float | None:
        if len(close) <= window or not close.iloc[-window - 1]:
            return None
        return (latest_close / float(close.iloc[-window - 1]) - 1) * 100

    previous_five_volume = volume.tail(6).iloc[:-1].mean()
    volume_vs_prev5d = (
        volume.iloc[-1] / previous_five_volume
        if previous_five_volume is not None and pd.notna(previous_five_volume) and previous_five_volume > 0
        else None
    )
    bar_complete = bool(raw.get("bar_complete", True))
    indicators = {
        "close": _round(latest_close),
        "ma5": _round(latest.get("ma5")),
        "ma10": _round(latest.get("ma10")),
        "ma20": _round(latest.get("ma20")),
        "ma60": _round(latest.get("ma60")),
        "macd_dif": _round(dif.iloc[-1]),
        "macd_dea": _round(dea.iloc[-1]),
        "macd_hist": _round(macd.iloc[-1]),
        "rsi14": _round(rsi.iloc[-1], 2),
        "atr14": _round(atr.iloc[-1]),
        "atr14_pct": _round(atr.iloc[-1] / latest_close * 100, 2),
        "boll_mid": _round(mid.iloc[-1]),
        "boll_upper": _round((mid + 2 * std).iloc[-1]),
        "boll_lower": _round((mid - 2 * std).iloc[-1]),
        "high_20d": _round(high.tail(20).max()),
        "low_20d": _round(low.tail(20).min()),
        "high_60d": _round(high.tail(60).max()),
        "low_60d": _round(low.tail(60).min()),
        "volume_vs_prev5d": _round(volume_vs_prev5d, 2),
        "volume_vs_prev5d_is_partial": not bar_complete,
        "return_5d_pct": _round(change(5), 2),
        "return_20d_pct": _round(change(20), 2),
        "return_60d_pct": _round(change(60), 2),
    }
    return {
        "symbol": code,
        "date": rows[-1].get("date"),
        "indicators": indicators,
        "success": True,
        "errors": [],
        "adjust": "qfq",
        "period": "daily",
        "bar_complete": bar_complete,
        "volume_unit": "股",
        "calculation_basis": "前复权日线；盘中当日 K 线未收盘时，最新指标会随行情变化",
        **{key: raw.get(key) for key in ("source", "data_time", "is_stale", "fallback_used", "_cached", "_fetched_at")},
    }


TOOL = ToolSpec(
    name="get_technical_indicators",
    description=DESCRIPTION,
    parameters=object_schema(
        {
            "symbol": {"type": "string", "description": "股票代码或股票名称"},
            "count": {"type": "integer", "minimum": 80, "maximum": 250, "default": 120, "description": "用于计算的最近日线数量"},
        },
        ["symbol"],
    ),
    executor=get_technical_indicators,
    category="analysis",
)
