# -*- coding: utf-8 -*-
"""Industry cycle analysis endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from src.services.industry_cycle_service import IndustryCycleService

router = APIRouter()


@router.get("/industry-cycle", summary="获取个股行业周期分析")
def get_industry_cycle(
    symbol: str = Query(..., description="股票代码"),
    force: bool = Query(False, description="强制刷新，跳过缓存"),
):
    service = IndustryCycleService()
    force_value = force if isinstance(force, bool) else False
    return service.get_report(symbol=symbol, force=force_value)


@router.get("/industry-cycle/report", summary="获取个股行业周期模型报告")
def get_industry_cycle_report(
    symbol: str = Query(..., description="股票代码"),
    force: bool = Query(False, description="强制刷新，跳过缓存"),
):
    service = IndustryCycleService()
    force_value = force if isinstance(force, bool) else False
    return service.get_report(symbol=symbol, force=force_value)


@router.post("/industry-cycle/report/tasks", summary="提交个股行业周期模型报告任务")
def create_industry_cycle_report_task(
    symbol: str = Query(..., description="股票代码"),
    force: bool = Query(True, description="是否强制拉取最新证据并重新生成"),
):
    service = IndustryCycleService()
    force_value = force if isinstance(force, bool) else True
    task = service.submit_report_task(symbol=symbol, force=force_value)
    return JSONResponse(
        status_code=202,
        content={
            "task_id": task.task_id,
            "status": task.status.value,
            "message": task.message or "行业周期模型研判任务已提交",
        },
    )


@router.get("/industry-cycle/report/tasks", summary="提交个股行业周期模型报告任务（GET兼容）")
def create_industry_cycle_report_task_via_get(
    symbol: str = Query(..., description="股票代码"),
    force: bool = Query(True, description="是否强制拉取最新证据并重新生成"),
):
    force_value = force if isinstance(force, bool) else True
    return create_industry_cycle_report_task(symbol=symbol, force=force_value)
