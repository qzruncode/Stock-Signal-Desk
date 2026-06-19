# -*- coding: utf-8 -*-
"""Buy-decision workbench endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Query

from src.services.buy_decision_workbench_service import BuyDecisionWorkbenchService

router = APIRouter()


def _service() -> BuyDecisionWorkbenchService:
    return BuyDecisionWorkbenchService()


@router.post("/buy-decision/workbench", summary="初始化买入判断工作台")
def create_buy_decision_workbench(
    payload: dict = Body(...),
):
    symbol = str(payload.get("symbol") or "").strip()
    force_reset = bool(payload.get("force_reset", False))
    if not symbol:
        raise HTTPException(status_code=400, detail={"error": "validation_error", "message": "symbol 不能为空"})
    return _service().create_session(symbol=symbol, force_reset=force_reset)


@router.get("/buy-decision/workbench/{session_id}", summary="获取买入判断工作台状态")
def get_buy_decision_workbench(session_id: str):
    try:
        return _service().get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "workbench session 不存在"}) from None


@router.post("/buy-decision/workbench/{session_id}/steps/{step_key}/run", summary="执行买入判断步骤")
def run_buy_decision_step(
    session_id: str,
    step_key: str,
    payload: dict = Body(default_factory=dict),
):
    force = bool(payload.get("force", False))
    try:
        return _service().run_step(session_id, step_key, force=force)
    except KeyError:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "workbench session 不存在"}) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_step", "message": str(exc)}) from None


@router.get("/buy-decision/workbench/{session_id}/report", summary="获取买入判断最终报告")
def get_buy_decision_report(session_id: str):
    try:
        return _service().get_report(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "workbench session 不存在"}) from None
    except ValueError:
        raise HTTPException(status_code=409, detail={"error": "report_not_ready", "message": "最终买入结论尚未生成"}) from None


@router.post(
    "/buy-decision/workbench/{session_id}/steps/industry_beta/run-streaming",
    summary="流式执行行业beta分析",
)
def run_industry_beta_streaming(session_id: str):
    try:
        service = _service()
        session = service.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "workbench session 不存在"}) from None

    symbol = session["symbol"]
    task_info = service.run_industry_beta_analysis_streaming(
        symbol=symbol,
        session_id=session_id,
    )

    return {"task_id": task_info.task_id, "status": task_info.status.value}


# ── Criteria Analysis (new) ─────────────────────────────────────────────


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
