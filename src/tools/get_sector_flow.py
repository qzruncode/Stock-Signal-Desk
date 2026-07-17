# -*- coding: utf-8 -*-
"""``get_sector_flow`` — source-correct A-share sector money flow.

The previous implementation silently replaced a failed money-flow source with
Sina sector performance.  Price performance is useful, but it is not capital
flow.  This module therefore only returns records from Eastmoney's sector-flow
dataset (the same public dataset wrapped by AKShare) and reports failure rather
than manufacturing flow rankings from unrelated fields.
"""

from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any

import httpx

from src.tools._akshare import cached_call
from src.tools._trading_calendar import (
    _fallback_trade_day,
    _fetch_trade_dates,
    expected_trade_day,
    is_trading_time,
)
from src.tools.base import ToolSpec, object_schema

_URL = "https://push2delay.eastmoney.com/api/qt/clist/get"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)

_PERIODS: dict[str, dict[str, str]] = {
    "today": {
        "label": "今日",
        "stat": "1",
        "sort": "f62",
        "change": "f3",
        "main": "f62",
        "main_pct": "f184",
        "super": "f66",
        "super_pct": "f69",
        "large": "f72",
        "large_pct": "f75",
        "medium": "f78",
        "medium_pct": "f81",
        "small": "f84",
        "small_pct": "f87",
        "leader": "f204",
        "leader_code": "f205",
    },
    "5d": {
        "label": "5日",
        "stat": "5",
        "sort": "f164",
        "change": "f109",
        "main": "f164",
        "main_pct": "f165",
        "super": "f166",
        "super_pct": "f167",
        "large": "f168",
        "large_pct": "f169",
        "medium": "f170",
        "medium_pct": "f171",
        "small": "f172",
        "small_pct": "f173",
        "leader": "f257",
        "leader_code": "f258",
    },
    "10d": {
        "label": "10日",
        "stat": "10",
        "sort": "f174",
        "change": "f160",
        "main": "f174",
        "main_pct": "f175",
        "super": "f176",
        "super_pct": "f177",
        "large": "f178",
        "large_pct": "f179",
        "medium": "f180",
        "medium_pct": "f181",
        "small": "f182",
        "small_pct": "f183",
        "leader": "f260",
        "leader_code": "f261",
    },
}


def _number(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _request_page(params: dict[str, Any], page: int) -> tuple[list[dict[str, Any]], int]:
    request_params = {**params, "pn": page, "_": int(time.time() * 1000)}
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = httpx.get(
                _URL,
                params=request_params,
                headers={"User-Agent": _UA, "Referer": "https://data.eastmoney.com/bkzj/"},
                timeout=12,
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") or {}
            rows = data.get("diff") or []
            if not isinstance(rows, list):
                raise RuntimeError("板块资金流接口返回格式异常")
            return [row for row in rows if isinstance(row, dict)], int(data.get("total") or len(rows))
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.35)
    raise RuntimeError(f"东方财富板块资金流第 {page} 页失败: {last_error}")


def _fetch_all(type: str, period: str) -> list[dict[str, Any]]:
    config = _PERIODS[period]
    fields = {
        "f2", "f12", "f14", "f124",
        config["change"], config["main"], config["main_pct"],
        config["super"], config["super_pct"], config["large"], config["large_pct"],
        config["medium"], config["medium_pct"], config["small"], config["small_pct"],
        config["leader"], config["leader_code"],
    }
    params = {
        "pz": 100,
        "po": 1,
        "np": 1,
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "fltt": 2,
        "invt": 2,
        "fid": config["sort"],
        "fid0": config["sort"],
        "fs": f"m:90 t:{'2' if type == 'industry' else '3'}",
        "stat": config["stat"],
        "fields": ",".join(sorted(fields)),
    }
    first, total = _request_page(params, 1)
    pages = max(1, math.ceil(total / 100))
    raw_rows = list(first)
    if pages > 1:
        with ThreadPoolExecutor(max_workers=min(4, pages - 1)) as pool:
            futures = {pool.submit(_request_page, params, page): page for page in range(2, pages + 1)}
            page_rows: dict[int, list[dict[str, Any]]] = {}
            for future in as_completed(futures):
                page = futures[future]
                rows, _ = future.result()
                page_rows[page] = rows
        for page in range(2, pages + 1):
            raw_rows.extend(page_rows.get(page, []))

    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_rows:
        code = str(raw.get("f12") or "").strip()
        name = str(raw.get("f14") or "").strip()
        if not code or not name or code in seen:
            continue
        seen.add(code)
        timestamp = _number(raw.get("f124"))
        records.append({
            "sector_code": code,
            "name": name,
            "period": period,
            "period_label": config["label"],
            "sector_index": _number(raw.get("f2")),
            "pct_chg": _number(raw.get(config["change"])),
            "main_net_inflow": _number(raw.get(config["main"])),
            "main_net_inflow_pct": _number(raw.get(config["main_pct"])),
            "super_large_net_inflow": _number(raw.get(config["super"])),
            "super_large_net_inflow_pct": _number(raw.get(config["super_pct"])),
            "large_net_inflow": _number(raw.get(config["large"])),
            "large_net_inflow_pct": _number(raw.get(config["large_pct"])),
            "medium_net_inflow": _number(raw.get(config["medium"])),
            "medium_net_inflow_pct": _number(raw.get(config["medium_pct"])),
            "small_net_inflow": _number(raw.get(config["small"])),
            "small_net_inflow_pct": _number(raw.get(config["small_pct"])),
            "leading_stock": str(raw.get(config["leader"]) or "").strip() or None,
            "leading_stock_code": str(raw.get(config["leader_code"]) or "").strip() or None,
            "data_time": datetime.fromtimestamp(timestamp).astimezone().isoformat() if timestamp else None,
        })
    records.sort(key=lambda item: item.get("main_net_inflow") if item.get("main_net_inflow") is not None else -math.inf, reverse=True)
    for rank, record in enumerate(records, 1):
        record["main_flow_rank"] = rank
    return records


def _freshness(data_time: str | None, now: datetime) -> tuple[bool | None, str | None]:
    if not data_time:
        return None, "上游没有返回数据时间，无法判断新鲜度"
    try:
        observed = datetime.fromisoformat(data_time)
    except ValueError:
        return None, "上游数据时间无法解析，无法判断新鲜度"
    try:
        expected = expected_trade_day(now, _fetch_trade_dates())
    except Exception:
        expected = _fallback_trade_day(now)
    if observed.date() < expected:
        return True, f"最新板块资金流日期 {observed.date()} 早于应有交易日 {expected}"
    if is_trading_time(now) and observed.date() == now.date() and now - observed > timedelta(minutes=15):
        return True, "盘中板块资金流超过 15 分钟未更新"
    return False, None


def get_sector_flow(type: str = "industry", top_n: int = 10, period: str = "today") -> dict[str, Any]:
    type = str(type).strip().lower()
    period = str(period).strip().lower()
    if type not in {"industry", "concept"}:
        raise ValueError("type 仅支持 industry 或 concept")
    if period not in _PERIODS:
        raise ValueError("period 仅支持 today、5d 或 10d")
    top_n = max(1, min(int(top_n), 30))
    now = datetime.now().astimezone()
    ttl = 75 if is_trading_time(now) else 30 * 60
    errors: list[str] = []
    try:
        records, cached = cached_call(
            f"sector_flow:v2:{type}:{period}",
            lambda: _fetch_all(type, period),
            ttl_seconds=ttl,
            attempts=2,
        )
    except Exception as exc:
        records, cached = [], False
        errors.append(str(exc))

    valid = [record for record in records if record.get("main_net_inflow") is not None]
    inflow = [record for record in valid if record["main_net_inflow"] > 0]
    outflow = sorted(
        [record for record in valid if record["main_net_inflow"] < 0],
        key=lambda item: item["main_net_inflow"],
    )
    data_times = [str(record["data_time"]) for record in records if record.get("data_time")]
    data_time = max(data_times) if data_times else None
    is_stale, warning = _freshness(data_time, now)
    warnings = [warning] if warning else []
    if records and not valid:
        errors.append("上游返回了板块行情，但没有有效主力净流入字段")

    success = bool(valid)
    return {
        "type": type,
        "period": period,
        "period_label": _PERIODS[period]["label"],
        "top_n": top_n,
        "sector_count": len(records),
        "inflow_top": inflow[:top_n],
        "outflow_top": outflow[:top_n],
        # Full records support in-project sector ranking consumers.  The Agent
        # payload compactor intentionally exposes only the two bounded lists.
        "records": records,
        "amount_unit": "元",
        "ratio_unit": "%",
        "price_unit": "人民币元",
        "main_flow_definition": "主力净流入=超大单净流入+大单净流入（东方财富口径）",
        "source": "东方财富板块资金流（AKShare 同源公开接口）",
        "source_url": "https://data.eastmoney.com/bkzj/",
        "success": success,
        "errors": errors if not success else errors,
        "warnings": warnings,
        "data_time": data_time,
        "is_stale": is_stale if success else None,
        "freshness_unknown": is_stale is None,
        "fallback_used": False,
        "_cached": cached,
        "_fetched_at": now.isoformat(),
    }


TOOL = ToolSpec(
    name="get_sector_flow",
    description=(
        "获取A股行业或概念板块在今日、近5日或近10日的真实资金流排名，返回主力、"
        "超大单、大单、中单和小单净流入及占比、板块涨跌幅和领涨股。"
    ),
    parameters=object_schema({
        "type": {"type": "string", "enum": ["industry", "concept"], "default": "industry", "description": "板块类型"},
        "period": {"type": "string", "enum": ["today", "5d", "10d"], "default": "today", "description": "资金流统计周期"},
        "top_n": {"type": "integer", "minimum": 1, "maximum": 30, "default": 10, "description": "净流入和净流出各返回数量"},
    }),
    executor=get_sector_flow,
    category="market",
)
