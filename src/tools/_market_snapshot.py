# -*- coding: utf-8 -*-
"""Shared, business-correct market snapshot for market Agent tools.

The snapshot deliberately keeps each metric tied to a source with the same
business meaning:

* Shanghai/Shenzhen A-share breadth: Legu's market-activity table.  AKShare's
  wrapper for this page is currently broken, so the public page is parsed
  directly and validated.
* Major indices and Shanghai/Shenzhen turnover: Sina real-time index quotes.
* Limit-up, limit-down and failed-limit pools: the matching AKShare Eastmoney
  pool APIs for the expected trading day.
* Consecutive index direction: Shanghai Composite daily history, including a
  live virtual close while the current trading day is not in daily history.

Industry board counts are never used as stock counts.  Likewise, unsupported
"60-day high/low" values and undisclosed northbound net-buy values are not
manufactured from unrelated fields.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "market_snapshot:v1"
_LEGU_URL = "https://legulegu.com/stockdata/market-activity"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)
_lock = threading.Lock()

_INDEX_CODES = {
    "shanghai_composite": "sh000001",
    "shenzhen_component": "sz399001",
    "chinext": "sz399006",
    "star50": "sh000688",
}


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _safe_int(value: Any) -> int | None:
    number = _safe_float(value)
    return int(number) if number is not None else None


def _iso_local(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return value.isoformat()


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed


@lru_cache(maxsize=1)
def _fetch_trade_dates() -> list[date]:
    import akshare as ak

    frame = ak.tool_trade_date_hist_sina()
    if frame is None or frame.empty or "trade_date" not in frame.columns:
        raise RuntimeError("交易日历为空")
    values: list[date] = []
    for value in frame["trade_date"].tolist():
        if isinstance(value, datetime):
            values.append(value.date())
        elif isinstance(value, date):
            values.append(value)
        else:
            try:
                values.append(datetime.fromisoformat(str(value)[:10]).date())
            except ValueError:
                continue
    if not values:
        raise RuntimeError("交易日历没有可解析日期")
    return sorted(set(values))


def expected_trade_day(now: datetime, trade_dates: list[date]) -> date:
    """Return the session whose intraday pools should be queried.

    On a trading day we switch to today's pool at 09:15 (call auction); before
    that, and on holidays, the most recent earlier session is used.
    """

    calendar = sorted(day for day in trade_dates if day <= now.date())
    if not calendar:
        raise RuntimeError("交易日历中找不到当前日期之前的交易日")
    if now.date() in calendar and now.time() >= time(9, 15):
        return now.date()
    earlier = [day for day in calendar if day < now.date()]
    return earlier[-1] if earlier else calendar[-1]


def _fallback_trade_day(now: datetime) -> date:
    day = now.date()
    if day.weekday() < 5 and now.time() >= time(9, 15):
        return day
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def is_trading_time(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    current = now.time()
    return time(9, 30) <= current <= time(11, 30) or time(13, 0) <= current <= time(15, 0)


def _parse_legu_activity(html: str) -> dict[str, Any]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if table is None:
        raise RuntimeError("市场活跃度页面没有统计表")

    values: dict[str, int] = {}
    for row in table.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
        for index in range(0, len(cells) - 1, 2):
            number = re.search(r"-?\d+", cells[index + 1].replace(",", ""))
            if number:
                values[cells[index].strip().lower()] = int(number.group())

    required = {"上涨", "下跌", "平盘"}
    if not required.issubset(values):
        raise RuntimeError("市场活跃度页面缺少上涨、下跌或平盘家数")
    total = values["上涨"] + values["下跌"] + values["平盘"] + values.get("停牌", 0)
    if total < 3000 or total > 7000:
        raise RuntimeError(f"市场活跃度股票总数异常: {total}")

    description = soup.find("meta", attrs={"name": "description"})
    description_text = str(description.get("content") or "") if description else ""
    activity_text = " ".join(node.get_text(" ", strip=True) for node in soup.select(".market-activity"))
    combined = f"{activity_text} {description_text}"
    time_match = re.search(r"(20\d{2}-\d{2}-\d{2})(?:\s+(\d{2}:\d{2}:\d{2}))?", combined)
    data_time = None
    if time_match:
        clock = time_match.group(2) or "15:00:00"
        data_time = _iso_local(datetime.fromisoformat(f"{time_match.group(1)}T{clock}"))
    activity_match = re.search(r"活跃度\s*([\d.]+)%", activity_text)

    return {
        "up_count": values["上涨"],
        "down_count": values["下跌"],
        "flat_count": values["平盘"],
        "halt_count": values.get("停牌"),
        "legu_limit_up_count": values.get("涨停"),
        "legu_limit_down_count": values.get("跌停"),
        "real_limit_up_count": values.get("真实涨停"),
        "real_limit_down_count": values.get("真实跌停"),
        "market_activity_pct": _safe_float(activity_match.group(1)) if activity_match else None,
        "data_time": data_time,
        "source": "乐咕乐股市场活跃度",
        "breadth_scope": "沪深A股",
    }


def _fetch_legu_activity() -> dict[str, Any]:
    response = httpx.get(
        _LEGU_URL,
        headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"},
        timeout=httpx.Timeout(10.0, connect=3.0),
        follow_redirects=True,
    )
    response.raise_for_status()
    return _parse_legu_activity(response.text)


def _fetch_sina_a_breadth() -> dict[str, Any]:
    """Slow but semantically correct breadth fallback for Legu failure."""

    import akshare as ak

    frame = ak.stock_zh_a_spot()
    if frame is None or frame.empty or "涨跌幅" not in frame.columns:
        raise RuntimeError("新浪全A实时行情为空")
    change = frame["涨跌幅"]
    active = change.notna()
    up = int((change[active] > 0).sum())
    down = int((change[active] < 0).sum())
    flat = int((change[active] == 0).sum())
    if up + down + flat < 3000:
        raise RuntimeError("新浪全A实时行情覆盖股票过少")
    timestamps = [str(value) for value in frame.get("时间戳", []) if value]
    now = datetime.now().astimezone()
    data_time = _iso_local(now)
    if timestamps and re.fullmatch(r"\d{2}:\d{2}:\d{2}", max(timestamps)):
        data_time = _iso_local(datetime.combine(now.date(), time.fromisoformat(max(timestamps))))
    return {
        "up_count": up,
        "down_count": down,
        "flat_count": flat,
        "halt_count": None,
        "data_time": data_time,
        "source": "新浪全A实时行情",
        "breadth_scope": "A股（含北交所）",
    }


def _fetch_index_spot() -> dict[str, Any]:
    import akshare as ak

    frame = ak.stock_zh_index_spot_sina()
    if frame is None or frame.empty or "代码" not in frame.columns:
        raise RuntimeError("新浪指数实时行情为空")
    rows = {str(row.get("代码")): row for _, row in frame.iterrows()}
    indices: dict[str, dict[str, Any]] = {}
    for key, code in _INDEX_CODES.items():
        row = rows.get(code)
        if row is None:
            continue
        indices[key] = {
            "code": code,
            "name": str(row.get("名称") or ""),
            "price": _safe_float(row.get("最新价")),
            "change": _safe_float(row.get("涨跌额")),
            "change_pct": _safe_float(row.get("涨跌幅")),
            "previous_close": _safe_float(row.get("昨收")),
            "open": _safe_float(row.get("今开")),
            "high": _safe_float(row.get("最高")),
            "low": _safe_float(row.get("最低")),
            "volume": _safe_float(row.get("成交量")),
            "amount": _safe_float(row.get("成交额")),
        }
    if "shanghai_composite" not in indices:
        raise RuntimeError("新浪指数实时行情缺少上证指数")

    sh_amount = (indices.get("shanghai_composite") or {}).get("amount")
    sz_amount = (indices.get("shenzhen_component") or {}).get("amount")
    total_amount = None
    if sh_amount is not None and sz_amount is not None:
        total_amount = round((sh_amount + sz_amount) / 1e8, 2)
    return {
        "indices": indices,
        "total_amount": total_amount,
        "total_amount_unit": "亿元",
        "turnover_scope": "沪深市场",
        "source": "新浪实时指数行情",
    }


def _fetch_index_daily() -> list[dict[str, Any]]:
    import akshare as ak

    frame = ak.stock_zh_index_daily(symbol="sh000001")
    if frame is None or frame.empty:
        raise RuntimeError("上证指数日线为空")
    records: list[dict[str, Any]] = []
    for _, row in frame.tail(40).iterrows():
        records.append({
            "date": str(row.get("date") or "")[:10],
            "open": _safe_float(row.get("open")),
            "high": _safe_float(row.get("high")),
            "low": _safe_float(row.get("low")),
            "close": _safe_float(row.get("close")),
        })
    return records


def _fetch_pool(function_name: str, trade_day: str) -> dict[str, Any]:
    import akshare as ak

    function = getattr(ak, function_name)
    frame = function(date=trade_day)
    return {"count": 0 if frame is None else len(frame), "source": function_name}


def _consecutive_direction(
    daily: list[dict[str, Any]],
    current_index: dict[str, Any] | None,
    trade_day: date,
) -> tuple[int | None, int | None]:
    closes: list[tuple[date, float]] = []
    for row in daily:
        value = _safe_float(row.get("close"))
        try:
            row_date = date.fromisoformat(str(row.get("date"))[:10])
        except ValueError:
            continue
        if value is not None:
            closes.append((row_date, value))
    closes.sort(key=lambda item: item[0])
    current_price = _safe_float((current_index or {}).get("price"))
    if current_price is not None and (not closes or closes[-1][0] < trade_day):
        closes.append((trade_day, current_price))
    if len(closes) < 2:
        return None, None

    last_change = closes[-1][1] - closes[-2][1]
    if last_change == 0:
        return 0, 0
    direction = 1 if last_change > 0 else -1
    count = 0
    for index in range(len(closes) - 1, 0, -1):
        change = closes[index][1] - closes[index - 1][1]
        if change == 0 or (1 if change > 0 else -1) != direction:
            break
        count += 1
    return (count, 0) if direction > 0 else (0, count)


def _call_named(name: str, function: Callable[[], Any]) -> tuple[str, Any, str | None]:
    try:
        return name, function(), None
    except Exception as exc:
        return name, None, f"{name}: {type(exc).__name__}: {exc}"


def fetch_market_snapshot(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.now().astimezone().tzinfo)
    errors: list[str] = []
    warnings: list[str] = []
    fallback_used = False

    try:
        trade_dates = _fetch_trade_dates()
        trade_day = expected_trade_day(now, trade_dates)
    except Exception as exc:
        trade_day = _fallback_trade_day(now)
        errors.append(f"交易日历: {type(exc).__name__}: {exc}")
        fallback_used = True
    trade_day_text = trade_day.strftime("%Y%m%d")

    jobs: dict[str, Callable[[], Any]] = {
        "breadth": _fetch_legu_activity,
        "index_spot": _fetch_index_spot,
        "index_daily": _fetch_index_daily,
        "limit_up_pool": lambda: _fetch_pool("stock_zt_pool_em", trade_day_text),
        "limit_down_pool": lambda: _fetch_pool("stock_zt_pool_dtgc_em", trade_day_text),
        "broken_pool": lambda: _fetch_pool("stock_zt_pool_zbgc_em", trade_day_text),
    }
    fetched: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=len(jobs), thread_name_prefix="market-snapshot") as executor:
        futures = {executor.submit(_call_named, name, function): name for name, function in jobs.items()}
        for future in as_completed(futures):
            name, value, error = future.result()
            if error:
                errors.append(error)
            else:
                fetched[name] = value

    breadth = fetched.get("breadth")
    if breadth is None:
        fallback_used = True
        try:
            breadth = _fetch_sina_a_breadth()
            warnings.append("乐咕乐股市场活跃度不可用，已使用较慢的新浪全A实时行情")
        except Exception as exc:
            errors.append(f"breadth_fallback: {type(exc).__name__}: {exc}")
            breadth = {}

    index_spot = fetched.get("index_spot") or {}
    indices = index_spot.get("indices") or {}
    daily = fetched.get("index_daily") or []
    current_sh = indices.get("shanghai_composite")
    consecutive_up_days, consecutive_down_days = _consecutive_direction(daily, current_sh, trade_day)

    limit_up_pool = fetched.get("limit_up_pool")
    limit_down_pool = fetched.get("limit_down_pool")
    broken_pool = fetched.get("broken_pool")
    limit_up_count = (limit_up_pool or {}).get("count")
    limit_down_count = (limit_down_pool or {}).get("count")
    broken_board_count = (broken_pool or {}).get("count")
    if limit_up_count is None:
        limit_up_count = breadth.get("legu_limit_up_count")
        fallback_used = True
    if limit_down_count is None:
        limit_down_count = breadth.get("legu_limit_down_count")
        fallback_used = True

    broken_board_rate = None
    if limit_up_count is not None and broken_board_count is not None:
        attempted = limit_up_count + broken_board_count
        broken_board_rate = round(broken_board_count / attempted * 100, 2) if attempted else 0.0

    up_count = breadth.get("up_count")
    down_count = breadth.get("down_count")
    flat_count = breadth.get("flat_count")
    active_count = sum(value for value in (up_count, down_count, flat_count) if value is not None)
    advance_decline_ratio = None
    advance_rate_pct = None
    decline_rate_pct = None
    if up_count is not None and down_count is not None:
        advance_decline_ratio = round(up_count / down_count, 3) if down_count else None
    if active_count:
        advance_rate_pct = round((up_count or 0) / active_count * 100, 2)
        decline_rate_pct = round((down_count or 0) / active_count * 100, 2)

    data_time = breadth.get("data_time")
    parsed_data_time = _parse_datetime(data_time)
    is_stale = parsed_data_time is None or parsed_data_time.date() < trade_day
    if (
        parsed_data_time is not None
        and parsed_data_time.date() == trade_day
        and is_trading_time(now)
        and (now - parsed_data_time).total_seconds() > 15 * 60
    ):
        is_stale = True
    if parsed_data_time is not None and parsed_data_time.date() > trade_day:
        is_stale = False

    sources = [
        value
        for value in (
            breadth.get("source"),
            index_spot.get("source"),
            "东方财富涨跌停池" if limit_up_pool or limit_down_pool else None,
            "东方财富炸板池" if broken_pool else None,
            "新浪上证指数日线" if daily else None,
        )
        if value
    ]
    fetched_at = _iso_local(now)
    return {
        "market_date": trade_day.isoformat(),
        "is_trading_time": is_trading_time(now) and trade_day == now.date(),
        "up_count": up_count,
        "down_count": down_count,
        "flat_count": flat_count,
        "halt_count": breadth.get("halt_count"),
        "advance_decline_ratio": advance_decline_ratio,
        "advance_rate_pct": advance_rate_pct,
        "decline_rate_pct": decline_rate_pct,
        "market_activity_pct": breadth.get("market_activity_pct"),
        "real_limit_up_count": breadth.get("real_limit_up_count"),
        "real_limit_down_count": breadth.get("real_limit_down_count"),
        "limit_up_count": limit_up_count,
        "limit_down_count": limit_down_count,
        "broken_board_count": broken_board_count,
        "broken_board_rate": broken_board_rate,
        "consecutive_up_days": consecutive_up_days,
        "consecutive_down_days": consecutive_down_days,
        "total_amount": index_spot.get("total_amount"),
        "total_amount_unit": index_spot.get("total_amount_unit") or "亿元",
        "turnover_scope": index_spot.get("turnover_scope") or "沪深市场",
        "indices": indices,
        "sh_index": current_sh,
        "breadth_scope": breadth.get("breadth_scope"),
        "breadth_source": breadth.get("source"),
        # Northbound buy/sell/net-buy turnover is no longer publicly available
        # in real time.  Keep an explicit compatibility field, never a false 0.
        "north_flow": None,
        "north_flow_available": False,
        "north_flow_note": "北向实时买入、卖出及净买入金额已停止披露，不能将接口中的 0 解释为净流入为零",
        "source": " + ".join(dict.fromkeys(sources)) or "未知",
        "errors": errors,
        "warnings": warnings,
        "data_time": data_time or fetched_at,
        "is_stale": is_stale,
        "fallback_used": fallback_used,
        "_fetched_at": fetched_at,
        "_cached": False,
    }


def _cache_key(now: datetime) -> str:
    return f"{_CACHE_PREFIX}:{now.strftime('%Y%m%d')}"


def _cache_get(now: datetime) -> dict[str, Any] | None:
    try:
        from src.storage import DatabaseManager

        raw = DatabaseManager.get_instance().get_kline_snapshot(_cache_key(now))
        if not raw:
            return None
        result = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(result, dict):
            fetched_at = _parse_datetime(result.get("_fetched_at"))
            if fetched_at is not None:
                result["_fetched_at"] = _iso_local(fetched_at)
        return result
    except Exception:
        logger.warning("读取市场快照缓存失败", exc_info=True)
        return None


def _cache_put(now: datetime, data: dict[str, Any]) -> None:
    try:
        from src.storage import DatabaseManager

        DatabaseManager.get_instance().save_kline_snapshot(
            _cache_key(now), json.dumps(data, ensure_ascii=False)
        )
    except Exception:
        logger.warning("写入市场快照缓存失败", exc_info=True)


def _cache_ttl(now: datetime) -> int:
    if is_trading_time(now):
        return 60
    if now.weekday() < 5 and time(9, 15) <= now.time() <= time(15, 30):
        return 180
    if now.weekday() < 5 and time(15, 30) < now.time() <= time(18, 0):
        return 900
    return 6 * 60 * 60


def _cache_is_fresh(cached: dict[str, Any], now: datetime) -> bool:
    fetched_at = _parse_datetime(cached.get("_fetched_at"))
    if fetched_at is None or cached.get("is_stale") is True:
        return False
    current = now if now.tzinfo is not None else now.replace(tzinfo=fetched_at.tzinfo)
    return (current - fetched_at).total_seconds() <= _cache_ttl(now)


def get_market_snapshot(*, force: bool = False, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now().astimezone()
    stale_cached = None if force else _cache_get(now)
    if stale_cached and _cache_is_fresh(stale_cached, now):
        result = dict(stale_cached)
        result["_cached"] = True
        return result

    with _lock:
        if not force:
            current_cached = _cache_get(now)
            if current_cached and _cache_is_fresh(current_cached, now):
                result = dict(current_cached)
                result["_cached"] = True
                return result
        fresh = fetch_market_snapshot(now)
        usable = fresh.get("up_count") is not None or bool(fresh.get("indices"))
        if usable:
            _cache_put(now, fresh)
            return fresh
        if stale_cached:
            result = dict(stale_cached)
            result["_cached"] = True
            result["is_stale"] = True
            result["fallback_used"] = True
            result.setdefault("warnings", []).append("实时市场快照获取失败，返回最近一次缓存")
            result.setdefault("errors", []).extend(fresh.get("errors") or [])
            return result
        return fresh


def market_status_view(snapshot: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "market_date", "is_trading_time", "up_count", "down_count", "flat_count", "halt_count",
        "limit_up_count", "limit_down_count", "total_amount", "total_amount_unit", "turnover_scope",
        "indices", "sh_index", "breadth_scope", "breadth_source", "north_flow",
        "north_flow_available", "north_flow_note", "source", "errors", "warnings", "data_time",
        "is_stale", "fallback_used", "_fetched_at", "_cached",
    )
    result = {key: snapshot.get(key) for key in fields}
    result["success"] = snapshot.get("up_count") is not None or bool(snapshot.get("indices"))
    result["partial"] = result["success"] and bool(snapshot.get("errors"))
    return result


def market_breadth_view(snapshot: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "market_date", "up_count", "down_count", "flat_count", "halt_count",
        "advance_decline_ratio", "advance_rate_pct", "decline_rate_pct", "market_activity_pct",
        "limit_up_count", "limit_down_count", "real_limit_up_count", "real_limit_down_count",
        "broken_board_count", "broken_board_rate", "consecutive_up_days", "consecutive_down_days",
        "total_amount", "total_amount_unit", "turnover_scope", "breadth_scope", "breadth_source",
        "source", "errors", "warnings", "data_time", "is_stale", "fallback_used", "_fetched_at", "_cached",
    )
    result = {key: snapshot.get(key) for key in fields}
    result["success"] = snapshot.get("up_count") is not None and snapshot.get("down_count") is not None
    result["partial"] = result["success"] and bool(snapshot.get("errors"))
    return result
