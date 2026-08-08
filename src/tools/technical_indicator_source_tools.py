"""Internal one-source, one-indicator calculation adapter.

The only model-visible contract is
``source_operations.calculate_technical_indicator``.  This module contains
the deterministic math and one-provider data read behind that contract; it
does not manufacture a second, per-provider tool catalogue.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Callable

import pandas as pd

from src.tools._kline import (
    _fetch_kline_em,
    _fetch_kline_sina,
    _fetch_kline_tencent,
    _kline_data_time,
    _kline_is_stale,
    _normalize_kline_df,
)
from src.tools.symbols import resolve_local_symbol


_SourceFetcher = Callable[[str, str, str], Any]
_SOURCES: dict[str, tuple[str, _SourceFetcher]] = {
    "eastmoney": ("东方财富日线（AKShare）", _fetch_kline_em),
    "sina": ("新浪财经日线（AKShare）", _fetch_kline_sina),
    "tencent": ("腾讯财经日线（AKShare）", _fetch_kline_tencent),
}
_MIN_COUNT = 30
_MAX_COUNT = 250


def _resolve_a_share_symbol(symbol: str) -> str:
    """Resolve only through the local security master, never another provider."""
    code = resolve_local_symbol(symbol).strip()
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError(f"无法在本地证券主数据中确认 A 股证券代码或名称: {symbol}")
    return code


def _read_source_rows(code: str, *, source_key: str, count: int) -> dict[str, Any]:
    source_label, fetcher = _SOURCES[source_key]
    now = datetime.now().astimezone()
    lookback_days = max(160, int(count * 1.7) + 45)
    try:
        frame = fetcher(
            code,
            (now - timedelta(days=lookback_days)).strftime("%Y%m%d"),
            now.strftime("%Y%m%d"),
        )
        records = _normalize_kline_df(frame, code, source_key)
    except Exception as exc:
        records = []
        error = f"{type(exc).__name__}: {exc}"
    else:
        error = ""
    if len(records) > count:
        records = records[-count:]
    data_time = _kline_data_time(records)
    return {
        "success": bool(records),
        "partial": False,
        "data": records,
        "source": source_label,
        "data_time": data_time,
        "data_time_provenance": "source" if data_time else "unavailable",
        "data_time_note": (
            None
            if data_time
            else f"{source_label}未返回有效日线日期；_fetched_at 仅表示本服务获取时间。"
        ),
        "is_stale": _kline_is_stale(records) if records else None,
        "freshness_unknown": data_time is None,
        "fallback_used": False,
        "_cached": False,
        "_fetched_at": now.isoformat(),
        "errors": [] if records else [error or f"{source_label}未返回 K 线数据"],
        "warnings": [],
    }


def _normalized_frame(raw: dict[str, Any]) -> pd.DataFrame:
    frame = pd.DataFrame(list(raw.get("data") or []))
    if frame.empty:
        return frame
    if "date" in frame.columns:
        frame = frame.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    for column in ("high", "low", "close"):
        if column not in frame.columns:
            frame[column] = None
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["high", "low", "close"]).reset_index(drop=True)


def _round(value: Any, digits: int = 4) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), digits)


def _failure(
    *,
    code: str,
    source_key: str,
    indicator: str,
    raw: dict[str, Any],
    error: str,
) -> dict[str, Any]:
    source_label, _ = _SOURCES[source_key]
    return {
        "success": False,
        "partial": False,
        "symbol": code,
        "indicator": indicator,
        "calculation": {},
        "source": source_label,
        "source_scope": f"{source_key}_daily_qfq_kline_plus_{indicator}",
        "data_time": raw.get("data_time"),
        "data_time_provenance": raw.get("data_time_provenance", "unavailable"),
        "data_time_note": raw.get("data_time_note"),
        "is_stale": raw.get("is_stale"),
        "freshness_unknown": raw.get("freshness_unknown", True),
        "fallback_used": False,
        "errors": [*list(raw.get("errors") or []), error],
        "warnings": list(raw.get("warnings") or []),
        "_cached": False,
        "_fetched_at": raw.get("_fetched_at"),
    }


def _success(
    *,
    code: str,
    source_key: str,
    indicator: str,
    raw: dict[str, Any],
    frame: pd.DataFrame,
    calculation: dict[str, Any],
) -> dict[str, Any]:
    source_label, _ = _SOURCES[source_key]
    latest = frame.iloc[-1]
    return {
        "success": True,
        "partial": False,
        "symbol": code,
        "date": str(latest.get("date") or raw.get("data_time") or "")[:10] or None,
        "close": _round(latest["close"]),
        "indicator": indicator,
        "calculation": calculation,
        "source": source_label,
        "source_scope": f"{source_key}_daily_qfq_kline_plus_{indicator}",
        "data_time": raw.get("data_time"),
        "data_time_provenance": raw.get("data_time_provenance", "unavailable"),
        "data_time_note": raw.get("data_time_note"),
        "is_stale": raw.get("is_stale"),
        "freshness_unknown": raw.get("freshness_unknown", True),
        "fallback_used": False,
        "adjust": "qfq",
        "period": "daily",
        "errors": [],
        "warnings": list(raw.get("warnings") or []),
        "_cached": False,
        "_fetched_at": raw.get("_fetched_at"),
    }


def _read_and_calculate(
    *,
    symbol: str,
    count: int,
    source_key: str,
    indicator: str,
    calculate: Callable[[pd.DataFrame], dict[str, Any]],
) -> dict[str, Any]:
    bounded_count = max(_MIN_COUNT, min(int(count), _MAX_COUNT))
    code = _resolve_a_share_symbol(symbol)
    raw = _read_source_rows(code, source_key=source_key, count=bounded_count)
    frame = _normalized_frame(raw)
    if not raw.get("success"):
        return _failure(
            code=code,
            source_key=source_key,
            indicator=indicator,
            raw=raw,
            error=f"{_SOURCES[source_key][0]}未返回可用于计算的日线数据",
        )
    try:
        calculation = calculate(frame)
    except ValueError as exc:
        return _failure(
            code=code,
            source_key=source_key,
            indicator=indicator,
            raw=raw,
            error=str(exc),
        )
    return _success(
        code=code,
        source_key=source_key,
        indicator=indicator,
        raw=raw,
        frame=frame,
        calculation=calculation,
    )


def _require_window(frame: pd.DataFrame, window: int, *, label: str, extra_rows: int = 0) -> None:
    if not 2 <= int(window) <= _MAX_COUNT:
        raise ValueError(f"{label} 必须在 2 到 {_MAX_COUNT} 之间")
    required = int(window) + int(extra_rows)
    if len(frame) < required:
        raise ValueError(f"有效 OHLC 日线少于 {required} 条，无法计算 {label}={window}")


def _moving_average_executor(source_key: str) -> Callable[[str, int, int], dict[str, Any]]:
    def execute(symbol: str, count: int = 120, window: int = 20) -> dict[str, Any]:
        return _read_and_calculate(
            symbol=symbol,
            count=count,
            source_key=source_key,
            indicator="moving_average",
            calculate=lambda frame: (
                _require_window(frame, window, label="window")
                or {"window": int(window), "value": _round(frame["close"].rolling(int(window)).mean().iloc[-1])}
            ),
        )

    execute.__name__ = f"calculate_moving_average_{source_key}"
    return execute


def _exponential_moving_average_executor(source_key: str) -> Callable[[str, int, int], dict[str, Any]]:
    def execute(symbol: str, count: int = 120, window: int = 20) -> dict[str, Any]:
        return _read_and_calculate(
            symbol=symbol,
            count=count,
            source_key=source_key,
            indicator="exponential_moving_average",
            calculate=lambda frame: (
                _require_window(frame, window, label="window")
                or {
                    "window": int(window),
                    "value": _round(frame["close"].ewm(span=int(window), adjust=False).mean().iloc[-1]),
                }
            ),
        )

    execute.__name__ = f"calculate_exponential_moving_average_{source_key}"
    return execute


def _macd_executor(source_key: str) -> Callable[[str, int, int, int, int], dict[str, Any]]:
    def execute(
        symbol: str,
        count: int = 120,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
    ) -> dict[str, Any]:
        def calculate(frame: pd.DataFrame) -> dict[str, Any]:
            if not 2 <= int(fast_period) < int(slow_period) <= _MAX_COUNT:
                raise ValueError("fast_period 必须小于 slow_period，且二者均在 2 到 250 之间")
            _require_window(frame, int(slow_period) + int(signal_period), label="slow_period")
            close = frame["close"]
            dif = close.ewm(span=int(fast_period), adjust=False).mean() - close.ewm(
                span=int(slow_period), adjust=False
            ).mean()
            dea = dif.ewm(span=int(signal_period), adjust=False).mean()
            return {
                "fast_period": int(fast_period),
                "slow_period": int(slow_period),
                "signal_period": int(signal_period),
                "dif": _round(dif.iloc[-1]),
                "dea": _round(dea.iloc[-1]),
                "histogram": _round((dif.iloc[-1] - dea.iloc[-1]) * 2),
            }

        return _read_and_calculate(
            symbol=symbol,
            count=count,
            source_key=source_key,
            indicator="macd",
            calculate=calculate,
        )

    execute.__name__ = f"calculate_macd_{source_key}"
    return execute


def _rsi_executor(source_key: str) -> Callable[[str, int, int], dict[str, Any]]:
    def execute(symbol: str, count: int = 120, period: int = 14) -> dict[str, Any]:
        def calculate(frame: pd.DataFrame) -> dict[str, Any]:
            _require_window(frame, period, label="period", extra_rows=1)
            delta = frame["close"].diff()
            gain = delta.clip(lower=0).ewm(alpha=1 / int(period), adjust=False).mean()
            loss = (-delta.clip(upper=0)).ewm(alpha=1 / int(period), adjust=False).mean()
            relative_strength = gain / loss.where(loss != 0)
            rsi = 100 - 100 / (1 + relative_strength)
            rsi = rsi.mask((loss == 0) & (gain > 0), 100.0)
            rsi = rsi.mask((loss == 0) & (gain == 0), 50.0)
            return {"period": int(period), "value": _round(rsi.iloc[-1], 2)}

        return _read_and_calculate(
            symbol=symbol,
            count=count,
            source_key=source_key,
            indicator="rsi",
            calculate=calculate,
        )

    execute.__name__ = f"calculate_rsi_{source_key}"
    return execute


def _atr_executor(source_key: str) -> Callable[[str, int, int], dict[str, Any]]:
    def execute(symbol: str, count: int = 120, period: int = 14) -> dict[str, Any]:
        def calculate(frame: pd.DataFrame) -> dict[str, Any]:
            _require_window(frame, period, label="period", extra_rows=1)
            previous_close = frame["close"].shift(1)
            true_range = pd.concat(
                [
                    frame["high"] - frame["low"],
                    (frame["high"] - previous_close).abs(),
                    (frame["low"] - previous_close).abs(),
                ],
                axis=1,
            ).max(axis=1)
            atr = true_range.ewm(alpha=1 / int(period), adjust=False).mean().iloc[-1]
            close = float(frame["close"].iloc[-1])
            return {
                "period": int(period),
                "value": _round(atr),
                "percent_of_close": _round(float(atr) / close * 100, 2) if close else None,
            }

        return _read_and_calculate(
            symbol=symbol,
            count=count,
            source_key=source_key,
            indicator="atr",
            calculate=calculate,
        )

    execute.__name__ = f"calculate_atr_{source_key}"
    return execute


def _bollinger_executor(source_key: str) -> Callable[[str, int, int, float], dict[str, Any]]:
    def execute(
        symbol: str,
        count: int = 120,
        period: int = 20,
        standard_deviations: float = 2.0,
    ) -> dict[str, Any]:
        def calculate(frame: pd.DataFrame) -> dict[str, Any]:
            _require_window(frame, period, label="period")
            multiplier = float(standard_deviations)
            if not 0 < multiplier <= 5:
                raise ValueError("standard_deviations 必须在 0 到 5 之间")
            mid = frame["close"].rolling(int(period)).mean().iloc[-1]
            deviation = frame["close"].rolling(int(period)).std().iloc[-1]
            return {
                "period": int(period),
                "standard_deviations": multiplier,
                "middle": _round(mid),
                "upper": _round(mid + multiplier * deviation),
                "lower": _round(mid - multiplier * deviation),
            }

        return _read_and_calculate(
            symbol=symbol,
            count=count,
            source_key=source_key,
            indicator="bollinger_bands",
            calculate=calculate,
        )

    execute.__name__ = f"calculate_bollinger_bands_{source_key}"
    return execute


def _period_return_executor(source_key: str) -> Callable[[str, int, int], dict[str, Any]]:
    def execute(symbol: str, count: int = 120, period: int = 20) -> dict[str, Any]:
        def calculate(frame: pd.DataFrame) -> dict[str, Any]:
            _require_window(frame, period, label="period", extra_rows=1)
            latest = float(frame["close"].iloc[-1])
            baseline = float(frame["close"].iloc[-int(period) - 1])
            if baseline == 0:
                raise ValueError("基期收盘价为 0，无法计算阶段收益")
            return {"period": int(period), "percent": _round((latest / baseline - 1) * 100, 2)}

        return _read_and_calculate(
            symbol=symbol,
            count=count,
            source_key=source_key,
            indicator="period_return",
            calculate=calculate,
        )

    execute.__name__ = f"calculate_period_return_{source_key}"
    return execute


_INDICATOR_EXECUTOR_FACTORIES: dict[str, Callable[[str], Callable[..., dict[str, Any]]]] = {
    "moving_average": _moving_average_executor,
    "exponential_moving_average": _exponential_moving_average_executor,
    "macd": _macd_executor,
    "rsi": _rsi_executor,
    "atr": _atr_executor,
    "bollinger_bands": _bollinger_executor,
    "period_return": _period_return_executor,
}


def calculate_indicator(
    *,
    source_key: str,
    indicator: str,
    symbol: str,
    count: int = 120,
    window: int | None = None,
    period: int | None = None,
    fast_period: int | None = None,
    slow_period: int | None = None,
    signal_period: int | None = None,
    standard_deviations: float | None = None,
) -> dict[str, Any]:
    """Read exactly one source and calculate exactly one requested indicator."""
    selected_source = str(source_key or "").strip()
    selected_indicator = str(indicator or "").strip()
    if selected_source not in _SOURCES:
        raise ValueError(f"未知日线来源: {source_key}")
    factory = _INDICATOR_EXECUTOR_FACTORIES.get(selected_indicator)
    if factory is None:
        raise ValueError(f"不支持的技术指标: {indicator}")

    execute = factory(selected_source)
    arguments: dict[str, Any] = {"symbol": str(symbol), "count": int(count)}
    if selected_indicator in {"moving_average", "exponential_moving_average"}:
        arguments["window"] = 20 if window is None else int(window)
    elif selected_indicator == "macd":
        arguments.update(
            {
                "fast_period": 12 if fast_period is None else int(fast_period),
                "slow_period": 26 if slow_period is None else int(slow_period),
                "signal_period": 9 if signal_period is None else int(signal_period),
            }
        )
    elif selected_indicator in {"rsi", "atr", "period_return"}:
        arguments["period"] = 14 if period is None else int(period)
        if selected_indicator == "period_return" and period is None:
            arguments["period"] = 20
    elif selected_indicator == "bollinger_bands":
        arguments.update(
            {
                "period": 20 if period is None else int(period),
                "standard_deviations": 2.0 if standard_deviations is None else float(standard_deviations),
            }
        )
    return execute(**arguments)


__all__ = ["calculate_indicator"]
