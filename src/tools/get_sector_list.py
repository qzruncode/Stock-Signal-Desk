# -*- coding: utf-8 -*-
"""``get_sector_list`` tool backed by the bounded sector-flow feed."""

from typing import Any

from src.tools.get_sector_flow import get_sector_flow


def get_sector_list(type: str = "industry") -> Any:
    """Return the complete board directory without the legacy unbounded crawl.

    ``api.v1.endpoints.sectors`` uses AKShare calls that can wait indefinitely
    and then retry another source.  The sector-flow tool already reads the same
    Eastmoney board universe with explicit page and network bounds, so this
    directory projects its complete records into the stable sector-list shape.
    """
    sector_type = str(type or "industry").strip().lower()
    if sector_type not in {"industry", "concept"}:
        raise ValueError("type 仅支持 industry 或 concept")
    flow = get_sector_flow(type=sector_type, period="today", top_n=30)
    records = flow.get("records") if isinstance(flow, dict) else []
    items = [
        {
            "name": record.get("name"),
            "code": record.get("sector_code"),
            "change_pct": record.get("pct_chg"),
            "lead_stock": record.get("leading_stock"),
            "lead_stock_code": record.get("leading_stock_code"),
            "lead_stock_price": None,
            "lead_stock_change_pct": None,
            "up_count": None,
            "down_count": None,
            "company_count": None,
            "net_flow": record.get("main_net_inflow"),
            "net_flow_pct": record.get("main_net_inflow_pct"),
            "data_source": "东方财富",
        }
        for record in (records or [])
        if isinstance(record, dict) and record.get("name")
    ]
    errors = list(flow.get("errors") or []) if isinstance(flow, dict) else ["板块目录返回格式异常"]
    warnings = list(flow.get("warnings") or []) if isinstance(flow, dict) else []
    success = bool(items)
    return {
        "type": sector_type,
        "items": items,
        "item_count": len(items),
        "success": success,
        "partial": success and bool(errors or warnings),
        "source": flow.get("source") if isinstance(flow, dict) else None,
        "data_time": flow.get("data_time") if isinstance(flow, dict) else None,
        "is_stale": flow.get("is_stale") if isinstance(flow, dict) and success else None,
        "freshness_unknown": flow.get("freshness_unknown", True) if isinstance(flow, dict) else True,
        "fallback_used": bool(flow.get("fallback_used")) if isinstance(flow, dict) else False,
        "errors": errors,
        "warnings": warnings,
        "_cached": bool(flow.get("_cached")) if isinstance(flow, dict) else False,
        "_fetched_at": flow.get("_fetched_at") if isinstance(flow, dict) else None,
    }
__all__ = ["get_sector_list"]
