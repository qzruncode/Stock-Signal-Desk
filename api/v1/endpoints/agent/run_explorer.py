# -*- coding: utf-8 -*-
"""Read-only Run Explorer endpoints."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Query, Request

from api.deps import get_database_manager
from api.v1.endpoints.agent import router
from src.agent.evaluation import score_agent_run_snapshot
from src.storage import DatabaseManager


def _summary_snapshot(snapshot: dict) -> dict:
    """Keep the default explorer response small without losing audit metadata.

    The terminal projection intentionally keeps a compact display projection,
    but historical runs may also contain the canonical tool result beside it.
    Do not send those raw request/response bodies until the user asks to
    inspect a tool call.
    """
    projection = snapshot.get("quality_projection")
    if not isinstance(projection, dict):
        return snapshot
    projected = dict(projection)
    for collection_name in ("tool_results", "evidence"):
        collection = projected.get(collection_name)
        if not isinstance(collection, list):
            continue
        compact_collection = []
        for item in collection:
            if not isinstance(item, dict):
                compact_collection.append(item)
                continue
            compact = dict(item)
            if collection_name == "tool_results":
                display_arguments = compact.get("display_arguments")
                if isinstance(display_arguments, dict):
                    compact["arguments"] = display_arguments
                else:
                    compact.pop("arguments", None)
            compact.pop("result", None)
            compact.pop("payload", None)
            compact_collection.append(compact)
        projected[collection_name] = compact_collection
    return {**snapshot, "quality_projection": projected}


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
    include_payloads: bool = Query(
        False,
        description="按需返回工具实际请求参数、规范化返回和证据载荷。",
    ),
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    tenant_id, owner_id = _owner_scope(request)
    snapshot = db_manager.get_agent_run_quality_snapshot(
        run_id,
        tenant_id=tenant_id,
        owner_id=owner_id,
        include_evidence_payloads=include_payloads,
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Agent 运行不存在")
    if not include_payloads:
        snapshot = _summary_snapshot(snapshot)
    return {
        "snapshot": snapshot,
        "score": score_agent_run_snapshot(snapshot),
    }
