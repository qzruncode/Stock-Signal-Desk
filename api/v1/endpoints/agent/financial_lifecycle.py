# -*- coding: utf-8 -*-
"""Financial conclusion lifecycle and calibration endpoints."""

from __future__ import annotations

from fastapi import Depends, Query, Request

from api.deps import get_database_manager
from api.v1.endpoints.agent import router
from src.storage import DatabaseManager


def _owner_scope(request: Request) -> tuple[str, str]:
    return (
        str(getattr(request.state, "tenant_id", "local")),
        str(getattr(request.state, "owner_id", "admin")),
    )


@router.get("/agent/financial-conclusions")
def list_financial_conclusions(
    request: Request,
    symbol: str | None = Query(None, max_length=16),
    lifecycle_status: str | None = Query(None, max_length=24),
    limit: int = Query(100, ge=1, le=1000),
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    tenant_id, owner_id = _owner_scope(request)
    return {
        "items": db_manager.list_financial_conclusions(
            tenant_id=tenant_id,
            owner_id=owner_id,
            symbol=symbol,
            lifecycle_status=lifecycle_status,
            limit=limit,
        )
    }


@router.post("/agent/financial-conclusions/refresh")
def refresh_financial_conclusion_outcomes(
    request: Request,
    limit: int = Query(1000, ge=1, le=5000),
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    tenant_id, owner_id = _owner_scope(request)
    return db_manager.refresh_financial_conclusion_outcomes(
        tenant_id=tenant_id,
        owner_id=owner_id,
        limit=limit,
    )


@router.get("/agent/financial-conclusions/calibration")
def get_financial_conclusion_calibration(
    request: Request,
    horizon_trading_days: int = Query(20, ge=1, le=252),
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    tenant_id, owner_id = _owner_scope(request)
    return db_manager.financial_conclusion_calibration(
        tenant_id=tenant_id,
        owner_id=owner_id,
        horizon_trading_days=horizon_trading_days,
    )
