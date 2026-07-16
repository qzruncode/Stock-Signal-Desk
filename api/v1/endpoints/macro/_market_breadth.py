# -*- coding: utf-8 -*-
"""HTTP adapter for the shared market-breadth tool implementation."""

from fastapi import APIRouter

from src.tools._market_snapshot import get_market_snapshot, market_breadth_view

router = APIRouter()


@router.get("/market-breadth", summary="获取市场宽度")
def get_market_breadth():
    """Return market participation, limit-pool and failed-limit metrics."""

    return market_breadth_view(get_market_snapshot())
