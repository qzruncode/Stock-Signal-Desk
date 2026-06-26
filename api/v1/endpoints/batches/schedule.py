# -*- coding: utf-8 -*-
"""Batch schedule endpoints — get and update cron schedule."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from api.v1.endpoints.batches import router
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)


class BatchScheduleRequest(BaseModel):
    enabled: bool = Field(..., description="是否启用定时跑批")
    times: list[str] = Field(..., description="每日执行时间点，格式 HH:MM")
    template_id: str = Field(..., description="提示词模板 ID")


class BatchScheduleResponse(BaseModel):
    id: int
    enabled: bool
    times: list[str]
    template_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@router.get("/schedule", response_model=BatchScheduleResponse)
async def get_batch_schedule():
    """获取定时跑批配置。"""
    db = DatabaseManager.get_instance()
    schedule = db.get_batch_schedule()
    if schedule is None:
        return BatchScheduleResponse(
            id=0, enabled=False, times=[], template_id=None,
        )
    return BatchScheduleResponse(**schedule)


@router.put("/schedule", response_model=BatchScheduleResponse)
async def update_batch_schedule(request: BatchScheduleRequest):
    """更新定时跑批配置。"""
    if not request.template_id:
        raise HTTPException(status_code=400, detail="必须指定模板 ID")

    import re
    for t in request.times:
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", t):
            raise HTTPException(status_code=400, detail=f"无效的时间格式: {t}")

    db = DatabaseManager.get_instance()
    result = db.save_batch_schedule(
        enabled=request.enabled,
        times=request.times,
        template_id=request.template_id,
    )
    return BatchScheduleResponse(**result)