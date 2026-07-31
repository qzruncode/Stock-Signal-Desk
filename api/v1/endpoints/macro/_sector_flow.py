# -*- coding: utf-8 -*-
"""HTTP adapters for the module-owned sector-flow tool."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from src.tools.get_sector_flow import get_sector_flow as _tool_get_sector_flow

router = APIRouter()


def _fetch_sector_flow_industry() -> list[dict]:
    """Backward-compatible full industry ranking for in-project services."""
    return list(_tool_get_sector_flow(type="industry", period="today", top_n=30).get("records") or [])


@router.get("/sector-flow", summary="获取板块资金流向")
def get_sector_flow(
    type: str = Query("industry", description="板块类型: industry | concept"),
    top_n: int = Query(10, ge=1, le=30, description="净流入和净流出各返回数量"),
    period: str = Query("today", description="统计周期: today | 5d | 10d"),
):
    try:
        result = _tool_get_sector_flow(type=type, top_n=top_n, period=period)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_argument", "message": str(exc)}) from exc
    if not result.get("success"):
        raise HTTPException(
            status_code=502,
            detail={
                "error": "no_data",
                "message": "; ".join(result.get("errors") or ["无法获取板块资金流数据"]),
            },
        )
    return result
