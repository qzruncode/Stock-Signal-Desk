# -*- coding: utf-8 -*-
"""``get_stock_capital_flow`` — individual A-share order-size money flow."""

from __future__ import annotations

import math
import json
import re
import time
from datetime import date, datetime, time as clock_time
from typing import Any
from urllib.parse import urlencode

import httpx
import pandas as pd

from data_provider.utils import is_bse_code
from src.tools._akshare import bare_local_symbol, bare_symbol, cached_call
from src.tools._trading_calendar import is_trading_time, latest_completed_trade_day
from src.tools.base import ToolSpec, object_schema

DESCRIPTION = (
    "获取个股每日主力、超大单、大单、中单和小单净流入及占比，并汇总近5/10/20个交易日"
    "的资金持续性。主力净流入采用东方财富的大单加超大单口径，不代表真实机构持仓。"
)

_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
_REALTIME_URL = "https://push2delay.eastmoney.com/api/qt/ulist.np/get"
_RAW_COLUMNS = [
    "date",
    "main_net_inflow",
    "small_net_inflow",
    "medium_net_inflow",
    "large_net_inflow",
    "super_large_net_inflow",
    "main_net_inflow_pct",
    "small_net_inflow_pct",
    "medium_net_inflow_pct",
    "large_net_inflow_pct",
    "super_large_net_inflow_pct",
    "close",
    "pct_chg",
    "_unused_1",
    "_unused_2",
]
_NUMERIC_FIELDS = [column for column in _RAW_COLUMNS if column not in {"date", "_unused_1", "_unused_2"}]


def _market_for(code: str) -> str:
    if is_bse_code(code):
        return "bj"
    if code.startswith(("6", "9")):
        return "sh"
    return "sz"


def _fetch_eastmoney_direct(code: str, market: str) -> pd.DataFrame:
    market_code = 1 if market == "sh" else 0
    params = {
        "lmt": "0",
        "klt": "101",
        "secid": f"{market_code}.{code}",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "_": int(time.time() * 1000),
    }
    last_error: Exception | None = None
    referers = (
        "https://data.eastmoney.com/zjlx/detail.html",
        f"https://quote.eastmoney.com/{'sh' if market == 'sh' else 'sz'}{code}.html",
        "https://www.google.com/",
    )
    for attempt in range(4):
        try:
            # Eastmoney intermittently closes generic TLS clients.  Scrapling's
            # curl_cffi dependency supplies a real browser TLS fingerprint.
            from curl_cffi import requests as curl_requests

            response = curl_requests.get(
                _URL,
                params={**params, "_": int(time.time() * 1000)},
                headers={
                    "Referer": referers[attempt % len(referers)],
                    "Accept": "application/json,text/plain,*/*",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Connection": "close",
                },
                impersonate="chrome",
                timeout=12,
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") or {}
            klines = data.get("klines") or []
            if not klines:
                raise RuntimeError(f"东方财富没有返回 {code} 的个股资金流")
            rows = [str(item).split(",") for item in klines]
            if any(len(row) != len(_RAW_COLUMNS) for row in rows):
                raise RuntimeError("个股资金流字段数量与接口契约不一致")
            frame = pd.DataFrame(rows, columns=_RAW_COLUMNS).drop(columns=["_unused_1", "_unused_2"])
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
            for column in _NUMERIC_FIELDS:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
            frame = (
                frame.dropna(subset=["date"])
                .sort_values("date")
                .drop_duplicates("date", keep="last")
                .reset_index(drop=True)
            )
            frame.attrs["transport"] = "curl_cffi"
            return frame
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(0.25 * (attempt + 1))
    # Final local-browser transport: this is materially different from another
    # HTTP retry and survives Eastmoney's TLS-client disconnects in practice.
    try:
        from scrapling.fetchers import DynamicFetcher

        url = f"{_URL}?{urlencode(params)}"
        page = DynamicFetcher.fetch(
            url,
            headless=True,
            disable_resources=True,
            timeout=15_000,
            retries=2,
        )
        payload = json.loads(bytes(page.body).decode("utf-8"))
        data = payload.get("data") or {}
        klines = data.get("klines") or []
        rows = [str(item).split(",") for item in klines]
        if not rows or any(len(row) != len(_RAW_COLUMNS) for row in rows):
            raise RuntimeError("浏览器降级未返回有效资金流序列")
        frame = pd.DataFrame(rows, columns=_RAW_COLUMNS).drop(columns=["_unused_1", "_unused_2"])
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
        for column in _NUMERIC_FIELDS:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = (
            frame.dropna(subset=["date"])
            .sort_values("date")
            .drop_duplicates("date", keep="last")
            .reset_index(drop=True)
        )
        frame.attrs["transport"] = "scrapling_dynamic"
        return frame
    except Exception as browser_exc:
        raise RuntimeError(
            f"东方财富个股资金流请求失败: {last_error}; Scrapling 浏览器降级失败: {browser_exc}"
        ) from browser_exc


def _fetch_current_flow(code: str, market: str) -> dict[str, Any] | None:
    """Fetch the current/last session flow; the history API often lags intraday."""
    market_code = 1 if market == "sh" else 0
    field_map = {
        "f2": "close",
        "f3": "pct_chg",
        "f62": "main_net_inflow",
        "f184": "main_net_inflow_pct",
        "f66": "super_large_net_inflow",
        "f69": "super_large_net_inflow_pct",
        "f72": "large_net_inflow",
        "f75": "large_net_inflow_pct",
        "f78": "medium_net_inflow",
        "f81": "medium_net_inflow_pct",
        "f84": "small_net_inflow",
        "f87": "small_net_inflow_pct",
    }
    params = {
        "secids": f"{market_code}.{code}",
        "fields": ",".join(["f12", "f14", "f124", *field_map]),
        "fltt": 2,
        "invt": 2,
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "_": int(time.time() * 1000),
    }
    response = httpx.get(
        _REALTIME_URL,
        params=params,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/zjlx/detail.html"},
        timeout=12,
    )
    response.raise_for_status()
    rows = (response.json().get("data") or {}).get("diff") or []
    if isinstance(rows, dict):
        rows = list(rows.values())
    raw = rows[0] if rows and isinstance(rows[0], dict) else None
    timestamp = _json_number(raw.get("f124")) if raw else None
    if not raw or timestamp is None or _json_number(raw.get("f62")) is None:
        return None
    observed = datetime.fromtimestamp(timestamp).astimezone()
    item: dict[str, Any] = {"date": observed.date(), "_data_time": observed.isoformat()}
    for upstream, field in field_map.items():
        item[field] = _json_number(raw.get(upstream))
    return item


def _fetch_flow_with_current(code: str, market: str) -> pd.DataFrame:
    history_error: str | None = None
    history_transport: str | None = None
    try:
        history = _fetch_eastmoney_direct(code, market)
        history_transport = str(history.attrs.get("transport") or "http")
    except Exception as exc:
        history_error = str(exc)
        history = pd.DataFrame(columns=["date", *_NUMERIC_FIELDS])
    current: dict[str, Any] | None = None
    try:
        current = _fetch_current_flow(code, market)
    except Exception:
        # The daily history remains valid evidence when the realtime snapshot
        # is temporarily unavailable; freshness validation will expose lag.
        current = None
    if current:
        current_date = current["date"]
        history = history[history["date"] != current_date]
        history = pd.concat([history, pd.DataFrame([current])], ignore_index=True)
    if history.empty:
        raise RuntimeError(history_error or "东方财富没有返回个股资金流")
    history = history.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    history.attrs["history_transport"] = history_transport or "unavailable"
    if history_error:
        history.attrs["history_error"] = history_error
    return history


def _json_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _record(row: dict[str, Any], now: datetime) -> dict[str, Any]:
    raw_date = row.get("date")
    trading_date = raw_date if isinstance(raw_date, date) else datetime.fromisoformat(str(raw_date)[:10]).date()
    is_partial = trading_date == now.date() and now.time() < clock_time(15, 5)
    result = {"date": trading_date.isoformat()}
    for field in _NUMERIC_FIELDS:
        result[field] = _json_number(row.get(field))
    raw_data_time = row.get("_data_time")
    result["data_time"] = None if raw_data_time is None or pd.isna(raw_data_time) else str(raw_data_time)
    result["bar_complete"] = not is_partial
    return result


def _window_summary(items: list[dict[str, Any]], window: int) -> dict[str, Any]:
    bucket = items[-window:]
    amounts = [item.get("main_net_inflow") for item in bucket if item.get("main_net_inflow") is not None]
    ratios = [item.get("main_net_inflow_pct") for item in bucket if item.get("main_net_inflow_pct") is not None]
    return {
        "requested_trading_days": window,
        "observations": len(amounts),
        "complete_window": len(amounts) >= window,
        "main_net_inflow": sum(amounts) if amounts else None,
        "average_main_net_inflow": sum(amounts) / len(amounts) if amounts else None,
        "average_main_net_inflow_pct": sum(ratios) / len(ratios) if ratios else None,
        "positive_days": sum(1 for value in amounts if value > 0),
        "negative_days": sum(1 for value in amounts if value < 0),
    }


def _is_stale(latest_date: date | None, now: datetime) -> tuple[bool | None, str | None]:
    if latest_date is None:
        return None, "没有可用的资金流交易日，无法判断新鲜度"
    # Let the shared helper own calendar loading and its weekday fallback; an
    # eager calendar call here would bypass that error boundary.
    expected = latest_completed_trade_day(now)
    if latest_date < expected:
        return True, f"最新资金流日期 {latest_date} 早于应有交易日 {expected}"
    return False, None


def get_stock_capital_flow(symbol: str, days: int = 20) -> dict[str, Any]:
    code = bare_symbol(symbol)
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("symbol 必须能解析为 6 位股票代码")
    market = _market_for(code)
    limit = max(1, min(int(days), 100))
    now = datetime.now().astimezone()
    ttl = 75 if is_trading_time(now) else 30 * 60
    errors: list[str] = []
    try:
        frame, cached = cached_call(
            f"stock_capital_flow:v2:{market}:{code}",
            lambda: _fetch_flow_with_current(code, market),
            ttl_seconds=ttl,
            attempts=1,
        )
    except Exception as exc:
        frame, cached = None, False
        errors.append(str(exc))

    raw_records = frame.to_dict(orient="records") if isinstance(frame, pd.DataFrame) and not frame.empty else []
    if isinstance(frame, pd.DataFrame) and frame.attrs.get("history_error"):
        errors.append(f"历史资金流降级失败，仅返回实时交易日: {frame.attrs['history_error']}")
    source_transport = (
        str(frame.attrs.get("history_transport") or "unknown") if isinstance(frame, pd.DataFrame) else "unavailable"
    )
    all_items = [_record(row, now) for row in raw_records]
    items = all_items[-limit:]
    latest = items[-1] if items else None
    latest_date = datetime.fromisoformat(latest["date"]).date() if latest else None
    stale, warning = _is_stale(latest_date, now)
    warnings = [warning] if warning else []
    windows = {f"{window}d": _window_summary(all_items, window) for window in (5, 10, 20)}
    summary: dict[str, Any] = {"windows": windows}
    for window in (5, 10, 20):
        window_data = windows[f"{window}d"]
        summary[f"main_net_inflow_{window}d"] = window_data["main_net_inflow"]
        summary[f"positive_days_{window}d"] = window_data["positive_days"]
        summary[f"observations_{window}d"] = window_data["observations"]

    success = bool(items)
    return {
        "symbol": code,
        "market": market,
        "days": limit,
        "latest": latest,
        "summary": summary,
        "items": items,
        "item_count": len(items),
        "available_history_count": len(all_items),
        "amount_unit": "元",
        "ratio_unit": "%",
        "price_unit": "人民币元",
        "main_flow_definition": "主力净流入=超大单净流入+大单净流入（东方财富口径）",
        "interpretation_warning": "资金流按成交单大小估算，不等同于机构账户真实买卖或持仓变化",
        "source": "东方财富个股资金流（AKShare 同源公开接口）",
        "source_url": "https://data.eastmoney.com/zjlx/detail.html",
        "source_transport": f"{source_transport}+realtime_http" if success else source_transport,
        "success": success,
        "errors": errors,
        "warnings": warnings,
        "data_time": (latest.get("data_time") or latest.get("date")) if latest else None,
        "source_data_time_granularity": "timestamp" if latest and latest.get("data_time") else "trading_date",
        "is_stale": stale if success else None,
        "freshness_unknown": stale is None,
        "fallback_used": source_transport in {"scrapling_dynamic", "unavailable"},
        "_cached": cached,
        "_fetched_at": now.isoformat(),
    }


def read_stock_capital_flow_history_eastmoney(
    symbol: str,
    days: int = 20,
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Read one Eastmoney daily capital-flow series without joining a quote."""
    code = bare_local_symbol(symbol)
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("symbol 必须能解析为 6 位股票代码")
    market = _market_for(code)
    limit = max(1, min(int(days), 100))
    now = datetime.now().astimezone()
    ttl = 75 if is_trading_time(now) else 30 * 60
    frame, cached = (
        cached_call(
            f"stock-capital-flow:history:v1:{market}:{code}",
            lambda: _fetch_eastmoney_direct(code, market),
            ttl_seconds=ttl,
            attempts=1,
        )
        if use_cache
        else (_fetch_eastmoney_direct(code, market), False)
    )
    records = [_record(row, now) for row in frame.to_dict(orient="records")]
    items = records[-limit:]
    latest = items[-1] if items else None
    latest_date = datetime.fromisoformat(latest["date"]).date() if latest else None
    stale, warning = _is_stale(latest_date, now)
    transport = str(frame.attrs.get("transport") or "http")
    return {
        "symbol": code,
        "market": market,
        "days": limit,
        "items": items,
        "item_count": len(items),
        "latest": latest,
        "amount_unit": "元",
        "ratio_unit": "%",
        "price_unit": "人民币元",
        "main_flow_definition": "主力净流入=超大单净流入+大单净流入（东方财富口径）",
        "source": "东方财富个股资金流日线",
        "source_url": "https://data.eastmoney.com/zjlx/detail.html",
        "source_scope": "daily_capital_flow_history",
        "source_transport": transport,
        "success": bool(items),
        "partial": False,
        "errors": [] if items else ["东方财富没有返回个股资金流日线"],
        "warnings": [warning] if warning else [],
        "data_time": (latest.get("data_time") or latest.get("date")) if latest else None,
        "source_data_time_granularity": "timestamp" if latest and latest.get("data_time") else "trading_date",
        "is_stale": stale if items else None,
        "freshness_unknown": stale is None,
        # This names a transport fallback, never a substituted data source.
        "fallback_used": transport == "scrapling_dynamic",
        "_cached": cached,
        "_fetched_at": now.isoformat(),
    }


def read_stock_capital_flow_quote_eastmoney(
    symbol: str,
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Read one Eastmoney current-session capital-flow quote."""
    code = bare_local_symbol(symbol)
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("symbol 必须能解析为 6 位股票代码")
    market = _market_for(code)
    now = datetime.now().astimezone()
    ttl = 75 if is_trading_time(now) else 30 * 60
    raw, cached = (
        cached_call(
            f"stock-capital-flow:quote:v1:{market}:{code}",
            lambda: _fetch_current_flow(code, market),
            ttl_seconds=ttl,
            attempts=1,
        )
        if use_cache
        else (_fetch_current_flow(code, market), False)
    )
    item = _record(raw, now) if isinstance(raw, dict) else None
    return {
        "symbol": code,
        "market": market,
        "item": item,
        "amount_unit": "元",
        "ratio_unit": "%",
        "price_unit": "人民币元",
        "main_flow_definition": "主力净流入=超大单净流入+大单净流入（东方财富口径）",
        "source": "东方财富个股资金流实时快照",
        "source_url": "https://data.eastmoney.com/zjlx/detail.html",
        "source_scope": "current_session_capital_flow_quote",
        "success": item is not None,
        "partial": False,
        "errors": [] if item else ["东方财富没有返回当前交易日资金流快照"],
        "warnings": [],
        "data_time": item.get("data_time") if item else None,
        "source_data_time_granularity": "timestamp",
        "is_stale": False if item and item.get("data_time") else None,
        "freshness_unknown": not bool(item and item.get("data_time")),
        "fallback_used": False,
        "_cached": cached,
        "_fetched_at": now.isoformat(),
    }


TOOLS = (
    ToolSpec(
        name="read_stock_capital_flow_history_eastmoney",
        description=(
            "从东方财富读取一只 A 股的资金流日线序列，包含主力、超大单、大单、中单、小单净流入及占比；"
            "不补入实时快照，不汇总 5/10/20 日结论。"
        ),
        parameters=object_schema(
            {
                "symbol": {"type": "string", "description": "A股股票代码或可解析的股票名称"},
                "days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 20,
                    "description": "返回最近交易日数量",
                },
            },
            ["symbol"],
        ),
        executor=read_stock_capital_flow_history_eastmoney,
        category="market",
    ),
    ToolSpec(
        name="read_stock_capital_flow_quote_eastmoney",
        description=(
            "从东方财富读取一只 A 股当前交易日的资金流快照；"
            "不查询历史日线、不计算资金持续性，也不把成交单大小解释为机构持仓。"
        ),
        parameters=object_schema(
            {"symbol": {"type": "string", "description": "A股股票代码或可解析的股票名称"}},
            ["symbol"],
        ),
        executor=read_stock_capital_flow_quote_eastmoney,
        category="market",
    ),
)


__all__ = [
    "TOOLS",
    "_market_for",
    "get_stock_capital_flow",
    "read_stock_capital_flow_history_eastmoney",
    "read_stock_capital_flow_quote_eastmoney",
]
