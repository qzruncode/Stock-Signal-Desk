# -*- coding: utf-8 -*-
"""Market mainline / theme analysis endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from src.services.market_theme_service import MarketThemeService

router = APIRouter()


@router.get("/mainline/report", summary="获取市场主线模型报告")
def get_market_mainline_report():
    service = MarketThemeService()
    # This endpoint lives in the persistent API process and is the only
    # read-path allowed to lazily start the daily report task.
    return service.get_model_report(trigger_generation=True)


@router.post("/mainline/report/tasks", summary="提交市场主线模型报告生成任务")
def create_market_mainline_report_task(
    force: bool = Query(True, description="是否强制拉取最新证据并重新生成"),
):
    service = MarketThemeService()
    force_value = force if isinstance(force, bool) else True
    task = service.submit_model_report_task(force=force_value)
    return JSONResponse(
        status_code=202,
        content={
            "task_id": task.task_id,
            "status": task.status.value,
            "message": task.message or "市场主线模型研判任务已提交",
        },
    )


@router.get("/mainline/report/tasks", summary="提交市场主线模型报告生成任务（GET 兼容）")
def create_market_mainline_report_task_via_get(
    force: bool = Query(True, description="是否强制拉取最新证据并重新生成"),
):
    force_value = force if isinstance(force, bool) else True
    return create_market_mainline_report_task(force=force_value)


@router.get("/mainline/summary", summary="获取市场主线摘要")
def get_market_mainline_summary(
    force: bool = Query(False, description="强制刷新，跳过缓存"),
):
    service = MarketThemeService()
    force_value = force if isinstance(force, bool) else False
    return service.get_summary(force=force_value)


@router.get("/mainline/evidence", summary="获取市场主线证据层")
def get_market_mainline_evidence(
    force: bool = Query(False, description="强制刷新，跳过缓存"),
):
    service = MarketThemeService()
    force_value = force if isinstance(force, bool) else False
    return service.get_evidence(force=force_value)


@router.get("/mainline/insight", summary="获取市场主线深度研判")
def get_market_mainline_insight(
    force: bool = Query(False, description="强制刷新，跳过缓存"),
    use_llm: bool = Query(False, description="是否尝试使用 LLM 做深度研判"),
):
    service = MarketThemeService()
    force_value = force if isinstance(force, bool) else False
    use_llm_value = use_llm if isinstance(use_llm, bool) else False
    return service.get_insight(force=force_value, use_llm=use_llm_value)


@router.get("/mainline", summary="获取市场主线研判（兼容旧接口）")
def get_market_mainline(
    force: bool = Query(False, description="强制刷新，跳过缓存"),
    use_llm: bool = Query(False, description="是否尝试使用 LLM 做深度研判"),
):
    service = MarketThemeService()
    force_value = force if isinstance(force, bool) else False
    use_llm_value = use_llm if isinstance(use_llm, bool) else False
    return service.analyze(force=force_value, use_llm=use_llm_value)
