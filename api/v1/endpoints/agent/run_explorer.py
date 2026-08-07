# -*- coding: utf-8 -*-
"""Read-only Run Explorer endpoints."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Query, Request

from api.deps import get_database_manager
from api.v1.endpoints.agent import router
from src.agent.evaluation import score_agent_run_snapshot
from src.storage import DatabaseManager


def _owner_scope(request: Request) -> tuple[str, str]:
    return (
        str(getattr(request.state, "tenant_id", "local")),
        str(getattr(request.state, "owner_id", "admin")),
    )


@router.get("/agent/runs")
def list_agent_runs(
    request: Request,
    status: str | None = Query(None, max_length=24),
    tool: str | None = Query(None, max_length=128),
    page: int = Query(1, ge=1),
    limit: int = Query(30, ge=1, le=100),
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    tenant_id, owner_id = _owner_scope(request)
    return db_manager.list_agent_runs_for_owner(
        tenant_id=tenant_id,
        owner_id=owner_id,
        status=status,
        tool=tool,
        page=page,
        limit=limit,
    )


@router.get("/agent/runs/{run_id}")
def get_agent_run_explorer_detail(
    run_id: str,
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    tenant_id, owner_id = _owner_scope(request)
    snapshot = db_manager.get_agent_run_quality_snapshot(
        run_id,
        tenant_id=tenant_id,
        owner_id=owner_id,
        include_evidence_payloads=False,
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Agent 运行不存在")
    return {
        "snapshot": snapshot,
        "score": score_agent_run_snapshot(snapshot),
    }
