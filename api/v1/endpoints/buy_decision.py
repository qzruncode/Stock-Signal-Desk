# -*- coding: utf-8 -*-
"""Buy-decision endpoints."""

from __future__ import annotations

import base64
import json
from datetime import date as date_type
from typing import Any

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()


# ── Criteria Analysis ─────────────────────────────────────────────────────


@router.get(
    "/criteria/analyze",
    summary="八维专业买入分析（SSE流）",
    description="完整评估8项买入维度并通过SSE推送结果；任何单项都不会终止后续分析。",
)
async def analyze_buy_criteria(
    symbol: str = Query(..., description="股票代码"),
    pre_fetched: str | None = Query(
        None,
        max_length=65536,
        description="Base64url-encoded JSON of pre-fetched data (e.g. valuation)",
    ),
):
    """Stream criterion evaluation results via SSE."""
    if not symbol or not symbol.strip():
        raise HTTPException(status_code=400, detail="symbol is required")

    pre_fetched_data: dict[str, Any] | None = None
    if pre_fetched:
        try:
            padded = pre_fetched + "=" * (-len(pre_fetched) % 4)
            decoded = base64.b64decode(padded, altchars=b"-_", validate=True).decode("utf-8")
            parsed = json.loads(decoded)
            if not isinstance(parsed, dict):
                raise ValueError("payload must be a JSON object")
            pre_fetched_data = parsed
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid pre_fetched data") from exc

    from src.services.buy_criteria.orchestrator import CriterionOrchestrator

    return CriterionOrchestrator.make_sse_endpoint(symbol.strip(), pre_fetched_data)


# ── Cached Records ────────────────────────────────────────────────────────


@router.get(
    "/criteria/cached/{symbol}",
    summary="查询今日买入判断缓存",
    description="按股票代码+日期查询已保存的买入判断结果，默认今天。",
)
async def get_cached_buy_criteria(
    symbol: str,
    target_date: str | None = Query(None, description="日期 YYYY-MM-DD，默认今天"),
):
    """Return cached buy criteria results for a symbol + date."""
    if not symbol or not symbol.strip():
        raise HTTPException(status_code=400, detail="symbol is required")

    try:
        trade_date = date_type.fromisoformat(target_date) if target_date else date_type.today()
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {target_date}")

    from src.storage import get_db

    db = get_db()
    record = db.get_buy_criteria_record(symbol.strip(), trade_date)
    if record is None:
        raise HTTPException(status_code=404, detail="No cached record found")
    return record
