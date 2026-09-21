"""Company ownership, pledge, unlock and holdings evidence."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from market_data_service.akshare_isolation import call_akshare_isolated

from .common import fetch_frame, section_envelope


def _equity_pledges(ak: Any, symbol: str) -> dict[str, Any]:
    direct = fetch_frame(
        "stock_gpzy_individual_pledge_ratio_detail_em",
        f"company:pledges:{symbol}",
        lambda: ak.stock_gpzy_individual_pledge_ratio_detail_em(symbol=symbol),
        ttl_seconds=6 * 3600,
        limit=200,
    )
    if direct.get("success"):
        return direct
    profile = fetch_frame(
        "stock_gpzy_profile_em",
        "market:equity_pledge_profile",
        lambda: ak.stock_gpzy_profile_em(),
        ttl_seconds=6 * 3600,
    )
    profile_dates = sorted(
        str(item.get("交易日期") or "").replace("-", "")[:8]
        for item in profile.get("items") or []
        if len(str(item.get("交易日期") or "").replace("-", "")[:8]) == 8
    )
    if not profile_dates:
        profile["error"] = (
            f"individual={direct.get('error')}；profile={profile.get('error') or 'latest trade date unavailable'}"
        )[:1200]
        return profile
    trade_date = profile_dates[-1]
    fallback = fetch_frame(
        "stock_gpzy_pledge_ratio_em",
        f"market:equity_pledge_ratio:{trade_date}",
        lambda: ak.stock_gpzy_pledge_ratio_em(date=trade_date),
        ttl_seconds=6 * 3600,
        symbol=symbol,
        limit=20,
    )
    if fallback.get("success"):
        fallback["fallback_from"] = direct.get("source_api")
        fallback["fallback_reason"] = direct.get("error")
        fallback["pledge_snapshot_date"] = trade_date
        fallback["warning"] = (
            "单股质押明细接口未返回可解析结果，已从最新市场质押比例快照中过滤。"
        )
        return fallback
    fallback["error"] = (
        f"individual={direct.get('error')}；ratio_fallback={fallback.get('error')}"
    )[:1200]
    return fallback


def _restricted_shareholders(
    ak: Any, symbol: str, queue: dict[str, Any]
) -> dict[str, Any]:
    dates: list[str] = []
    for item in queue.get("items") or []:
        value = str(item.get("解禁时间") or "").replace("-", "")[:8]
        if len(value) == 8 and value.isdigit() and value not in dates:
            dates.append(value)
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    cached = True
    successful_queries = 0
    for day in dates[:3]:
        result = fetch_frame(
            "stock_restricted_release_stockholder_em",
            f"company:restricted_release_stockholders:{symbol}:{day}",
            lambda day=day: ak.stock_restricted_release_stockholder_em(
                symbol=symbol, date=day
            ),
            ttl_seconds=24 * 3600,
            limit=200,
        )
        cached = cached and bool(result.get("cached"))
        if result.get("success"):
            successful_queries += 1
            items.extend(
                {"解禁时间": day, **item} for item in result.get("items") or []
            )
        elif result.get("error"):
            errors.append(f"{day}: {result['error']}")
    return {
        "success": successful_queries > 0 if dates else queue.get("success") is True,
        "partial": bool(items) and bool(errors),
        "source_api": "AKShare.stock_restricted_release_stockholder_em",
        "items": items,
        "item_count": len(items),
        "source_row_count": len(items),
        "cached": cached if dates else bool(queue.get("cached")),
        "error": "；".join(errors)[:1200] if errors else None,
        "queried_dates": dates[:3],
    }


def get_ownership_evidence(symbol: str, *, days: int = 730) -> dict[str, Any]:
    import akshare as ak

    code = str(symbol)
    today = datetime.now().astimezone().date()
    fallback_start = (today - timedelta(days=max(30, min(int(days), 1460)))).strftime(
        "%Y%m%d"
    )
    fallback_end = today.strftime("%Y%m%d")
    northbound = fetch_frame(
        "stock_hsgt_individual_em",
        f"company:northbound_holding:{code}",
        lambda: ak.stock_hsgt_individual_em(symbol=code),
        ttl_seconds=3600,
    )
    northbound["items"] = (northbound.get("items") or [])[
        -max(30, min(int(days), 250)) :
    ]
    northbound["requested_history_points"] = int(days)
    northbound["output_history_point_limit"] = 250
    northbound["item_count"] = len(northbound["items"])
    holding_dates = sorted(
        str(item.get("持股日期") or "").replace("-", "")[:8]
        for item in northbound["items"]
        if len(str(item.get("持股日期") or "").replace("-", "")[:8]) == 8
    )
    start = holding_dates[0] if holding_dates else fallback_start
    end = holding_dates[-1] if holding_dates else fallback_end
    restricted_queue = fetch_frame(
        "stock_restricted_release_queue_em",
        f"company:restricted_releases:{code}",
        lambda: ak.stock_restricted_release_queue_em(symbol=code),
        ttl_seconds=6 * 3600,
        limit=100,
    )
    datasets = {
        "equity_pledges": _equity_pledges(ak, code),
        "restricted_releases": restricted_queue,
        "restricted_release_shareholders": _restricted_shareholders(
            ak, code, restricted_queue
        ),
        "northbound_holding_history": northbound,
        "northbound_holding_details": fetch_frame(
            "stock_hsgt_individual_detail_em",
            f"company:northbound_holding_details:{code}:{start}:{end}",
            lambda: ak.stock_hsgt_individual_detail_em(
                symbol=code,
                start_date=start,
                end_date=end,
            ),
            ttl_seconds=6 * 3600,
            limit=200,
        ),
        "shareholder_changes": fetch_frame(
            "stock_ggcg_em",
            "market:shareholder_changes:all",
            lambda: ak.stock_ggcg_em(symbol="全部"),
            ttl_seconds=3600,
            symbol=code,
            limit=100,
        ),
        "control_structure": fetch_frame(
            "stock_hold_control_cninfo",
            f"market:control_structure:{datetime.now().astimezone().date().isoformat()}",
            lambda: call_akshare_isolated(
                "stock_hold_control_cninfo", symbol="全部"
            ),
            ttl_seconds=24 * 3600,
            symbol=code,
            limit=20,
        ),
    }
    return section_envelope(
        "ownership",
        code,
        datasets,
        units={"shares": "股", "ratio": "%", "market_value": "元"},
        notes=["北向持股为个股历史持仓，不等同于已停止公开披露的实时北向净买入。"],
    )


__all__ = ["get_ownership_evidence"]
