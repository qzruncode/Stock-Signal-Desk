# -*- coding: utf-8 -*-
"""Run-level quality, release evaluation and explicit feedback endpoints."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Query, Request

from api.deps import get_database_manager
from api.v1.endpoints.agent import router
from api.v1.schemas.agent_quality import (
    AgentRunFeedbackRequest,
    CreateAgentEvaluationCaseRequest,
    EvaluateAgentCaseRequest,
)
from src.agent.evaluation import score_agent_run_snapshot
from src.storage import DatabaseManager


def _owner_scope(request: Request) -> tuple[str, str]:
    return (
        str(getattr(request.state, "tenant_id", "local")),
        str(getattr(request.state, "owner_id", "admin")),
    )


@router.get("/agent/quality/summary")
def get_agent_quality_summary(
    request: Request,
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(1000, ge=1, le=5000),
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    tenant_id, owner_id = _owner_scope(request)
    return db_manager.agent_quality_summary(
        tenant_id=tenant_id,
        owner_id=owner_id,
        days=days,
        limit=limit,
    )


@router.get("/agent/runs/{run_id}/quality")
def get_agent_run_quality(
    run_id: str,
    request: Request,
    include_evidence: bool = Query(False),
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    tenant_id, owner_id = _owner_scope(request)
    snapshot = db_manager.get_agent_run_quality_snapshot(
        run_id,
        tenant_id=tenant_id,
        owner_id=owner_id,
        include_evidence_payloads=include_evidence,
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Agent 运行不存在")
    return {
        "snapshot": snapshot,
        "score": score_agent_run_snapshot(snapshot),
    }


@router.put("/agent/runs/{run_id}/feedback")
def upsert_agent_run_feedback(
    run_id: str,
    payload: AgentRunFeedbackRequest,
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    tenant_id, owner_id = _owner_scope(request)
    try:
        return db_manager.upsert_agent_run_feedback(
            tenant_id=tenant_id,
            owner_id=owner_id,
            run_id=run_id,
            rating=payload.rating,
            category=payload.category,
            comment=payload.comment,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Agent 运行不存在",
        ) from exc


@router.post("/agent/evaluation-cases")
def create_agent_evaluation_case(
    payload: CreateAgentEvaluationCaseRequest,
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    tenant_id, owner_id = _owner_scope(request)
    try:
        return db_manager.create_agent_evaluation_case(
            tenant_id=tenant_id,
            owner_id=owner_id,
            source_run_id=payload.source_run_id,
            suite=payload.suite,
            name=payload.name,
            description=payload.description,
            expectations=payload.expectations.model_dump(
                exclude_none=True
            ),
            tags=payload.tags,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="来源 Agent 运行不存在",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/agent/evaluation-cases")
def list_agent_evaluation_cases(
    request: Request,
    suite: str | None = Query(None, max_length=96),
    status: str | None = Query("active", max_length=24),
    limit: int = Query(100, ge=1, le=500),
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    tenant_id, owner_id = _owner_scope(request)
    return {
        "items": db_manager.list_agent_evaluation_cases(
            tenant_id=tenant_id,
            owner_id=owner_id,
            suite=suite,
            status=status,
            limit=limit,
        )
    }


@router.post("/agent/evaluation-cases/{case_id}/evaluate")
def evaluate_agent_case(
    case_id: str,
    payload: EvaluateAgentCaseRequest,
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    tenant_id, owner_id = _owner_scope(request)
    try:
        return db_manager.evaluate_agent_run_against_case(
            tenant_id=tenant_id,
            owner_id=owner_id,
            case_id=case_id,
            candidate_run_id=payload.candidate_run_id,
        )
    except KeyError as exc:
        detail = (
            "评测用例不存在"
            if exc.args and exc.args[0] == "evaluation_case_not_found"
            else "候选 Agent 运行不存在"
        )
        raise HTTPException(status_code=404, detail=detail) from exc


@router.get("/agent/evaluation-results")
def list_agent_evaluation_results(
    request: Request,
    case_id: str | None = Query(None, max_length=64),
    limit: int = Query(100, ge=1, le=500),
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    tenant_id, owner_id = _owner_scope(request)
    return {
        "items": db_manager.list_agent_evaluation_results(
            tenant_id=tenant_id,
            owner_id=owner_id,
            case_id=case_id,
            limit=limit,
        )
    }
