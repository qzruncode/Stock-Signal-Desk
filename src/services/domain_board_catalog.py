# -*- coding: utf-8 -*-
"""Bounded live concept-board catalog for semantic domain resolution.

The catalog is context for the task planner, not a company-data source.  It
uses the same complete, cached Eastmoney board feed as the market tools so the
planner can select only board names the execution layer can later verify.
"""

from __future__ import annotations

from typing import Any


def get_domain_board_catalog() -> dict[str, Any]:
    from src.tools.get_sector_flow import get_sector_flow

    result = get_sector_flow(type="concept", period="today", top_n=30)
    records = result.get("records") if isinstance(result, dict) else []
    names = sorted({
        str(item.get("name") or "").strip()
        for item in records or []
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    })
    return {
        "success": bool(names),
        "board_names": names,
        "board_count": len(names),
        "source": result.get("source") if isinstance(result, dict) else None,
        "data_time": result.get("data_time") if isinstance(result, dict) else None,
        "errors": list(result.get("errors") or []) if isinstance(result, dict) else ["板块目录格式异常"],
        "warnings": list(result.get("warnings") or []) if isinstance(result, dict) else [],
    }
__all__ = ["get_domain_board_catalog"]
