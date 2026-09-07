"""Chip distribution and institutional trading evidence."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .common import fetch_frame, section_envelope


def _lhb_details(ak: Any, symbol: str, daily: dict[str, Any]) -> dict[str, Any]:
    dates = []
    for item in daily.get("items") or []:
        value = str(item.get("上榜日期") or "").replace("-", "")[:8]
        if len(value) == 8 and value.isdigit() and value not in dates:
            dates.append(value)
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    cached = True
    successful_queries = 0
    for day in dates[:3]:
        for side in ("买入", "卖出"):
            result = fetch_frame(
                "stock_lhb_stock_detail_em",
                f"company:lhb_detail:{symbol}:{day}:{side}",
                lambda day=day, side=side: ak.stock_lhb_stock_detail_em(
                    symbol=symbol, date=day, flag=side
                ),
                ttl_seconds=24 * 3600,
                limit=20,
            )
            cached = cached and bool(result.get("cached"))
            if result.get("success"):
                successful_queries += 1
                items.extend(
                    {"上榜日期": day, "方向": side, **item}
                    for item in result.get("items") or []
                )
            elif result.get("error"):
                errors.append(f"{day}/{side}: {result['error']}")
    return {
        "success": successful_queries > 0 if dates else True,
        "partial": bool(items) and bool(errors),
        "source_api": "AKShare.stock_lhb_stock_detail_em",
        "items": items,
        "item_count": len(items),
        "source_row_count": len(items),
        "cached": cached if dates else False,
        "error": "；".join(errors)[:1200] if errors else None,
        "queried_dates": dates[:3],
    }


def get_trading_evidence(symbol: str, *, days: int = 30) -> dict[str, Any]:
    import akshare as ak

    code = str(symbol)
    today = datetime.now().astimezone().date()
    start = (today - timedelta(days=max(7, min(int(days), 365)))).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    daily_lhb = fetch_frame(
        "stock_lhb_jgmmtj_em",
        f"market:lhb_institution_daily:{start}:{end}",
        lambda: ak.stock_lhb_jgmmtj_em(start_date=start, end_date=end),
        ttl_seconds=3600,
        symbol=code,
        limit=100,
    )
    datasets = {
        "chip_distribution": fetch_frame(
            "stock_cyq_em",
            f"company:chip_distribution:{code}",
            lambda: ak.stock_cyq_em(symbol=code, adjust=""),
            ttl_seconds=1800,
            limit=120,
        ),
        "lhb_institution_daily": daily_lhb,
        "lhb_institution_period": fetch_frame(
            "stock_lhb_jgstatistic_em",
            "market:lhb_institution_period:one_month",
            lambda: ak.stock_lhb_jgstatistic_em(symbol="近一月"),
            ttl_seconds=3600,
            symbol=code,
            limit=20,
        ),
        "lhb_broker_details": _lhb_details(ak, code, daily_lhb),
    }
    return section_envelope(
        "trading_evidence",
        code,
        datasets,
        units={"amount": "元", "ratio": "%", "price": "元/股"},
        notes=["筹码和龙虎榜是交易结构证据，不替代基本面和估值判断。"],
    )


__all__ = ["get_trading_evidence"]
