"""``get_valuation_ratios`` — current, historical and relative valuation."""

from __future__ import annotations
import math
import re
from datetime import date, datetime, time, timedelta
from typing import Any
import httpx
from market_data_service.data_provider.utils import is_bse_code
from market_data_service.providers.common import (
    bare_local_symbol,
    cached_call,
    exchange_prefix,
    frame_records,
)

_QUOTE_URL = "https://push2delay.eastmoney.com/api/qt/stock/get"
_COMPARISON_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"


def _number(value: Any) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _date_text(value: Any) -> str | None:
    text = str(value or "")[:10]
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return None


def _secid(code: str) -> str:
    return (
        f"{(0 if is_bse_code(code) or not code.startswith(('6', '9')) else 1)}.{code}"
    )


def _fetch_history(code: str):
    import akshare as ak

    frame = ak.stock_value_em(symbol=code)
    if frame is None or frame.empty:
        raise RuntimeError("AKShare stock_value_em 没有估值历史")
    return frame


def _fetch_quote(code: str) -> dict[str, Any]:
    response = httpx.get(
        _QUOTE_URL,
        params={
            "secid": _secid(code),
            "fields": "f43,f57,f58,f116,f117,f124,f162,f163,f164,f167",
            "fltt": 2,
            "invt": 2,
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        },
        headers={"User-Agent": _UA, "Referer": "https://quote.eastmoney.com/"},
        timeout=httpx.Timeout(10.0, connect=3.0),
    )
    response.raise_for_status()
    data = response.json().get("data") or {}
    if str(data.get("f57") or "").zfill(6) != code:
        raise RuntimeError("东方财富估值快照返回了其他证券")
    timestamp = _number(data.get("f124"))
    return {
        "name": str(data.get("f58") or "").strip() or None,
        "price": _number(data.get("f43")),
        "pe_dynamic": _number(data.get("f162")),
        "pe_static": _number(data.get("f163")),
        "pe_ttm": _number(data.get("f164")),
        "pb_annual": _number(data.get("f167")),
        "total_market_cap": _number(data.get("f116")),
        "circulating_market_cap": _number(data.get("f117")),
        "quote_time": datetime.fromtimestamp(timestamp).astimezone().isoformat()
        if timestamp
        else None,
    }


def _fetch_comparison(code: str) -> dict[str, Any]:
    prefixed = exchange_prefix(code, upper=True)
    response = httpx.get(
        _COMPARISON_URL,
        params={
            "reportName": "RPT_PCF10_INDUSTRY_CVALUE",
            "columns": "ALL",
            "quoteColumns": "",
            "filter": f'(SECUCODE="{code}.{prefixed[:2]}")',
            "pageNumber": "",
            "pageSize": "",
            "sortTypes": "1",
            "sortColumns": "PAIMING",
            "source": "HSF10",
            "client": "PC",
        },
        headers={
            "User-Agent": _UA,
            "Referer": "https://emweb.securities.eastmoney.com/",
        },
        timeout=httpx.Timeout(12.0, connect=3.0),
    )
    response.raise_for_status()
    rows = (response.json().get("result") or {}).get("data") or []
    if not rows:
        raise RuntimeError("东方财富没有返回同行估值比较")
    target = next(
        (row for row in rows if str(row.get("CORRE_SECURITY_CODE")) == code), {}
    )
    average = next(
        (row for row in rows if row.get("CORRE_SECURITY_CODE") == "行业平均"), {}
    )
    median = next(
        (row for row in rows if row.get("CORRE_SECURITY_CODE") == "行业中值"), {}
    )
    if not target:
        raise RuntimeError("同行估值比较中没有目标证券")
    report_date = _date_text(target.get("REPORT_DATE"))
    base_year = datetime.fromisoformat(report_date).year if report_date else None
    forward_pe = []
    forward_ps = []
    for offset, pe_field, ps_field in (
        (0, "PE_1Y", "PS_1Y"),
        (1, "PE_2Y", "PS_2Y"),
        (2, "PE_3Y", "PS_3Y"),
    ):
        year = base_year + offset if base_year else None
        pe = _number(target.get(pe_field))
        ps = _number(target.get(ps_field))
        if pe is not None:
            forward_pe.append({"year": year, "value": pe})
        if ps is not None:
            forward_ps.append({"year": year, "value": ps})

    def benchmark(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "pe_ttm": _number(row.get("PE_TTM")),
            "pb_mrq": _number(row.get("PB_MRQ")),
            "ps_ttm": _number(row.get("PS_TTM")),
            "pcf_ttm": _number(row.get("PCE_TTM")),
            "peg_forward": _number(row.get("PEG")),
        }

    total = int(_number(target.get("TOTAL_COUNT")) or 0)
    rank = int(_number(target.get("PAIMING")) or 0) or None
    return {
        "report_date": report_date,
        "forward_pe": forward_pe,
        "forward_ps": forward_ps,
        "peg_forward": _number(target.get("PEG")),
        "rank": rank,
        "total": total,
        "percentile_rank_pct": round(rank / total * 100, 4) if rank and total else None,
        "average": benchmark(average),
        "median": benchmark(median),
    }


def _fetch_dividends(code: str):
    import akshare as ak

    frame = ak.stock_fhps_detail_em(symbol=code)
    if frame is None or frame.empty:
        raise RuntimeError("AKShare stock_fhps_detail_em 没有分红记录")
    return frame


def _expected_completed_trade_day(now: datetime) -> date:
    try:
        from market_data_service.calendar import _fetch_trade_dates

        dates = [day for day in _fetch_trade_dates() if day <= now.date()]
        if now.date() in dates and now.time() >= time(15, 30):
            return now.date()
        prior = [day for day in dates if day < now.date()]
        if prior:
            return prior[-1]
    except Exception:
        pass
    day = (
        now.date()
        if now.weekday() < 5 and now.time() >= time(15, 30)
        else now.date() - timedelta(days=1)
    )
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _validated_symbol(symbol: str) -> str:
    code = bare_local_symbol(symbol)
    if not re.fullmatch("\\d{6}", code):
        raise ValueError("symbol 必须能解析为 6 位股票代码")
    return code


def _history_items(frame: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in frame_records(frame):
        trade_date = _date_text(row.get("数据日期"))
        if not trade_date:
            continue
        items.append(
            {
                "trade_date": trade_date,
                "price": _number(row.get("当日收盘价")),
                "total_market_cap": _number(row.get("总市值")),
                "circulating_market_cap": _number(row.get("流通市值")),
                "total_shares": _number(row.get("总股本")),
                "circulating_shares": _number(row.get("流通股本")),
                "pe_ttm": _number(row.get("PE(TTM)")),
                "pe_static": _number(row.get("PE(静)")),
                "pb_mrq": _number(row.get("市净率")),
                "peg_trailing": _number(row.get("PEG值")),
                "pcf_ttm": _number(row.get("市现率")),
                "ps_ttm": _number(row.get("市销率")),
            }
        )
    return sorted(items, key=lambda item: item["trade_date"])


def read_valuation_history_eastmoney(
    symbol: str, days: int = 250, *, use_cache: bool = True
) -> dict[str, Any]:
    """Read one provider's daily valuation series without derived percentiles."""
    code = _validated_symbol(symbol)
    limit = max(5, min(int(days), 1825))
    frame, cached = (
        cached_call(
            f"valuation:history:atomic:v1:{code}",
            lambda: _fetch_history(code),
            ttl_seconds=30 * 60,
            attempts=2,
        )
        if use_cache
        else (_fetch_history(code), False)
    )
    items = _history_items(frame)[-limit:]
    latest = items[-1] if items else None
    now = datetime.now().astimezone()
    expected = _expected_completed_trade_day(now)
    return {
        "symbol": code,
        "days": limit,
        "items": items,
        "item_count": len(items),
        "latest": latest,
        "price_unit": "人民币元",
        "market_cap_unit": "元",
        "share_unit": "股",
        "ratio_unit": "倍",
        "source": "东方财富估值历史/AKShare",
        "source_scope": "daily_valuation_history",
        "success": bool(items),
        "partial": False,
        "errors": [] if items else ["东方财富没有返回可用估值历史"],
        "warnings": [],
        "data_time": latest.get("trade_date") if latest else None,
        "is_stale": datetime.fromisoformat(str(latest["trade_date"])).date() < expected
        if latest and latest.get("trade_date")
        else None,
        "freshness_unknown": latest is None,
        "fallback_used": False,
        "_cached": cached,
        "_fetched_at": now.isoformat(),
    }


def read_valuation_quote_eastmoney(
    symbol: str, *, use_cache: bool = True
) -> dict[str, Any]:
    """Read one dated Eastmoney valuation snapshot, with history fallback."""
    code = _validated_symbol(symbol)
    quote, cached = (
        cached_call(
            f"valuation:quote:atomic:v1:{code}",
            lambda: _fetch_quote(code),
            ttl_seconds=60,
            attempts=2,
        )
        if use_cache
        else (_fetch_quote(code), False)
    )
    now = datetime.now().astimezone()
    quote = quote if isinstance(quote, dict) else {}
    success = any(
        (
            quote.get(key) is not None
            for key in (
                "price",
                "pe_ttm",
                "pe_static",
                "pe_dynamic",
                "total_market_cap",
                "circulating_market_cap",
            )
        )
    )
    data_time = quote.get("quote_time")
    source_attempts: list[dict[str, Any]] = [
        {
            "source": "eastmoney_quote",
            "label": "东方财富估值实时快照",
            "status": "success" if success else "failed",
            "data_time": data_time,
            **({} if success else {"error": "no_valuation_fields"}),
        }
    ]
    warnings: list[str] = []
    fallback_used = False
    fallback_provider: str | None = None
    fallback_attempted = False
    source_scope = "realtime_valuation_quote"
    snapshot = dict(quote)
    history_result: dict[str, Any] = {}
    if not data_time:
        fallback_attempted = True
        try:
            history_result = read_valuation_history_eastmoney(
                code, days=250, use_cache=use_cache
            )
        except Exception as exc:
            history_result = {
                "success": False,
                "latest": None,
                "errors": [f"{type(exc).__name__}: {exc}"],
            }
        latest = (
            history_result.get("latest") if isinstance(history_result, dict) else None
        )
        if isinstance(latest, dict) and latest.get("trade_date"):
            for quote_key, history_key in (
                ("price", "price"),
                ("pe_ttm", "pe_ttm"),
                ("pe_static", "pe_static"),
                ("total_market_cap", "total_market_cap"),
                ("circulating_market_cap", "circulating_market_cap"),
            ):
                if latest.get(history_key) is not None:
                    snapshot[quote_key] = latest[history_key]
            snapshot["trade_date"] = latest["trade_date"]
            data_time = str(latest["trade_date"])
            fallback_used = True
            fallback_provider = "东方财富估值历史/AKShare"
            source_scope = "dated_valuation_snapshot"
            source_attempts.append(
                {
                    "source": "eastmoney_history",
                    "label": "东方财富估值历史/AKShare",
                    "status": "success",
                    "data_time": data_time,
                }
            )
            warnings.append(
                "实时估值快照未返回 quote_time，已使用最近一条带日期的估值历史快照。"
            )
        else:
            source_attempts.append(
                {
                    "source": "eastmoney_history",
                    "label": "东方财富估值历史/AKShare",
                    "status": "failed",
                    "error": "; ".join(
                        (
                            str(item)
                            for item in list(history_result.get("errors") or [])[:2]
                        )
                    )
                    if isinstance(history_result, dict)
                    else "no_dated_history",
                }
            )
            warnings.append(
                "实时估值快照未返回 quote_time，且估值历史没有可用日期；时效性无法确认。"
            )
    snapshot_success = any(
        (
            snapshot.get(key) is not None
            for key in (
                "price",
                "pe_ttm",
                "pe_static",
                "pe_dynamic",
                "total_market_cap",
                "circulating_market_cap",
            )
        )
    )
    return {
        "symbol": code,
        **snapshot,
        "price_unit": "人民币元",
        "market_cap_unit": "元",
        "ratio_unit": "倍",
        "source": "东方财富实时估值快照",
        "source_scope": source_scope,
        "success": snapshot_success,
        "partial": False,
        "errors": [] if snapshot_success else ["东方财富没有返回可用估值快照字段"],
        "warnings": warnings,
        "data_time": data_time,
        "data_time_provenance": "source" if data_time else "unavailable",
        "data_time_note": None
        if data_time
        else "东方财富实时估值快照未返回 quote_time，且估值历史均未返回可验证时间；_fetched_at 仅表示本服务获取时间。",
        "is_stale": history_result.get("is_stale")
        if fallback_used
        else datetime.fromisoformat(str(data_time)[:10]).date()
        < _expected_completed_trade_day(now)
        if data_time
        else None,
        "freshness_unknown": data_time is None,
        "fallback_used": fallback_used,
        "fallback_attempted": fallback_attempted,
        "fallback_provider": fallback_provider,
        "source_attempts": source_attempts,
        "_cached": cached,
        "_fetched_at": now.isoformat(),
    }


def read_peer_valuation_eastmoney(
    symbol: str, *, use_cache: bool = True
) -> dict[str, Any]:
    """Read one Eastmoney peer-comparison table without ranking conclusions."""
    code = _validated_symbol(symbol)
    comparison, cached = (
        cached_call(
            f"valuation:peer-comparison:atomic:v1:{code}",
            lambda: _fetch_comparison(code),
            ttl_seconds=30 * 60,
            attempts=2,
        )
        if use_cache
        else (_fetch_comparison(code), False)
    )
    now = datetime.now().astimezone()
    report_date = comparison.get("report_date")
    return {
        "symbol": code,
        **comparison,
        "ratio_unit": "倍",
        "source": "东方财富同行估值比较",
        "source_scope": "peer_valuation_comparison",
        "success": True,
        "partial": False,
        "errors": [],
        "warnings": [],
        "data_time": report_date,
        "is_stale": None,
        "freshness_unknown": report_date is None,
        "fallback_used": False,
        "_cached": cached,
        "_fetched_at": now.isoformat(),
    }


def read_dividend_history_eastmoney(
    symbol: str, limit: int = 100, *, use_cache: bool = True
) -> dict[str, Any]:
    """Read Eastmoney's dividend implementation records without computing yield."""
    code = _validated_symbol(symbol)
    maximum = max(1, min(int(limit), 200))
    frame, cached = (
        cached_call(
            f"valuation:dividend:atomic:v1:{code}",
            lambda: _fetch_dividends(code),
            ttl_seconds=6 * 3600,
            attempts=2,
        )
        if use_cache
        else (_fetch_dividends(code), False)
    )
    items: list[dict[str, Any]] = []
    for row in frame_records(frame):
        ex_dividend_date = _date_text(row.get("除权除息日"))
        report_date = _date_text(row.get("报告期"))
        progress = str(row.get("方案进度") or "").strip() or None
        cash_per_10 = _number(row.get("现金分红-现金分红比例"))
        if not any((ex_dividend_date, report_date, progress, cash_per_10 is not None)):
            continue
        items.append(
            {
                "report_date": report_date,
                "ex_dividend_date": ex_dividend_date,
                "progress": progress,
                "cash_dividend_per_10_shares": cash_per_10,
                "cash_dividend_per_share": round(cash_per_10 / 10, 6)
                if cash_per_10 is not None
                else None,
            }
        )
    items.sort(
        key=lambda item: (
            item.get("ex_dividend_date") or "",
            item.get("report_date") or "",
        ),
        reverse=True,
    )
    items = items[:maximum]
    latest = next(
        (item.get("ex_dividend_date") or item.get("report_date") for item in items),
        None,
    )
    now = datetime.now().astimezone()
    return {
        "symbol": code,
        "limit": maximum,
        "items": items,
        "item_count": len(items),
        "cash_dividend_unit": "人民币元/10股",
        "source": "东方财富分红实施记录/AKShare",
        "source_scope": "dividend_implementation_history",
        "success": True,
        "partial": False,
        "errors": [],
        "warnings": [],
        "data_time": latest,
        "is_stale": None,
        "freshness_unknown": latest is None,
        "fallback_used": False,
        "_cached": cached,
        "_fetched_at": now.isoformat(),
    }
