# -*- coding: utf-8 -*-
"""Governance endpoints for built-in Agent capabilities."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Query, Request

from api.deps import get_database_manager
from api.v1.endpoints.agent import router
from api.v1.schemas.agent_governance import (
    AgentCapabilityGrantRequest,
    AgentUserMemoryRequest,
    CreateCapabilityReleaseRequest,
)
from src.agent.capability_release import (
    build_capability_release_manifest,
)
from src.agent.task_workflows import StandardTaskKind
from src.storage import DatabaseManager


def _owner_scope(request: Request) -> tuple[str, str]:
    return (
        str(getattr(request.state, "tenant_id", "local")),
        str(getattr(request.state, "owner_id", "admin")),
    )


@router.get("/agent/governance/capability-grants")
def list_capability_grants(
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    tenant_id, owner_id = _owner_scope(request)
    return {
        "items": db_manager.list_agent_capability_grants(
            tenant_id=tenant_id,
            owner_id=owner_id,
        )
    }


@router.put("/agent/governance/capability-grants/{capability}")
def upsert_capability_grant(
    capability: str,
    payload: AgentCapabilityGrantRequest,
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    try:
        StandardTaskKind(capability)
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail="内置能力不存在",
        ) from exc
    tenant_id, owner_id = _owner_scope(request)
    return db_manager.upsert_agent_capability_grant(
        tenant_id=tenant_id,
        owner_id=owner_id,
        capability=capability,
        decision=payload.decision,
        reason=payload.reason,
    )


@router.get("/agent/governance/approval-receipts")
def list_approval_receipts(
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    tenant_id, owner_id = _owner_scope(request)
    return {
        "items": db_manager.list_agent_approval_receipts(
            tenant_id=tenant_id,
            owner_id=owner_id,
            limit=limit,
        )
    }


@router.get("/agent/governance/audit-events")
def list_governance_audit_events(
    request: Request,
    event_type: str | None = Query(None, max_length=64),
    limit: int = Query(100, ge=1, le=1000),
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    tenant_id, owner_id = _owner_scope(request)
    return {
        "items": db_manager.list_agent_audit_events(
            tenant_id=tenant_id,
            owner_id=owner_id,
            event_type=event_type,
            limit=limit,
        )
    }


@router.get("/agent/governance/capability-releases")
def list_capability_releases(
    limit: int = Query(100, ge=1, le=500),
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    manifest = build_capability_release_manifest()
    return {
        "runtime_manifest": manifest,
        "items": db_manager.list_agent_capability_releases(
            limit=limit
        ),
    }


@router.post("/agent/governance/capability-releases")
def create_capability_release(
    payload: CreateCapabilityReleaseRequest,
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    _tenant_id, owner_id = _owner_scope(request)
    try:
        return db_manager.create_agent_capability_release(
            owner_id=owner_id,
            release_version=payload.release_version,
            manifest=build_capability_release_manifest(),
            evaluation_suite=payload.evaluation_suite,
            minimum_pass_rate=payload.minimum_pass_rate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/agent/governance/capability-releases/{release_id}/activate"
)
def activate_capability_release(
    release_id: str,
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    tenant_id, owner_id = _owner_scope(request)
    manifest = build_capability_release_manifest()
    try:
        return db_manager.activate_agent_capability_release(
            release_id=release_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            runtime_fingerprint=manifest[
                "registry_fingerprint"
            ],
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="能力发布版本不存在",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/agent/memories")
def list_agent_user_memories(
    request: Request,
    conversation_id: str | None = Query(None, max_length=64),
    enabled_only: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    tenant_id, owner_id = _owner_scope(request)
    return {
        "items": db_manager.list_agent_user_memories(
            tenant_id=tenant_id,
            owner_id=owner_id,
            conversation_id=conversation_id,
            enabled_only=enabled_only,
            limit=limit,
        )
    }


@router.post("/agent/memories")
def create_agent_user_memory(
    payload: AgentUserMemoryRequest,
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    tenant_id, owner_id = _owner_scope(request)
    try:
        return db_manager.upsert_agent_user_memory(
            tenant_id=tenant_id,
            owner_id=owner_id,
            memory_id=None,
            **payload.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/agent/memories/{memory_id}")
def update_agent_user_memory(
    memory_id: str,
    payload: AgentUserMemoryRequest,
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    tenant_id, owner_id = _owner_scope(request)
    try:
        return db_manager.upsert_agent_user_memory(
            tenant_id=tenant_id,
            owner_id=owner_id,
            memory_id=memory_id,
            **payload.model_dump(),
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="记忆不存在",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/agent/memories/{memory_id}")
def delete_agent_user_memory(
    memory_id: str,
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    tenant_id, owner_id = _owner_scope(request)
    deleted = db_manager.delete_agent_user_memory(
        tenant_id=tenant_id,
        owner_id=owner_id,
        memory_id=memory_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return {"deleted": memory_id}
