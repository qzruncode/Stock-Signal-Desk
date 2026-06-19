# -*- coding: utf-8 -*-
"""Buy-decision endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()


# ── Criteria Analysis ─────────────────────────────────────────────────────


@router.get(
    "/criteria/analyze",
    summary="买入准则分析（SSE流）",
    description="顺序评估8项买入准则，通过SSE实时推送结果。任意一项不通过即终止。",
)
async def analyze_buy_criteria(symbol: str = Query(..., description="股票代码")):
    """Stream criterion evaluation results via SSE."""
    if not symbol or not symbol.strip():
        raise HTTPException(status_code=400, detail="symbol is required")

    from src.services.buy_criteria.orchestrator import CriterionOrchestrator
    return CriterionOrchestrator.make_sse_endpoint(symbol.strip())
