# -*- coding: utf-8 -*-
"""HTTP adapter for the shared market-status tool implementation."""

from fastapi import APIRouter, Query

from src.tools._market_snapshot import get_market_snapshot, market_status_view

router = APIRouter()


@router.get("/status", summary="获取市场整体状态")
def get_market_status(
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """Return a source-validated Shanghai/Shenzhen market snapshot.

    Intraday cache is one minute.  Breadth, turnover and limit-pool metrics are
    kept on their actual source scopes; unavailable northbound net flow is
    explicitly marked unavailable instead of returning a misleading zero.
    """

    return market_status_view(get_market_snapshot(force=force if isinstance(force, bool) else False))
