# -*- coding: utf-8 -*-
"""Batch run and schedule API."""

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from src.batch_runner import BatchRunner, BATCH_REPORTS_DIR
from src.config import get_config
from src.prompt_templates import get_prompt_template_store
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["batch"])


class BatchRunTriggerRequest(BaseModel):
    stock_codes: list[str] = Field(..., description="股票代码列表")
    template_id: str = Field(..., description="提示词模板 ID")


class BatchRunItem(BaseModel):
    id: int
    run_id: str
    triggered_by: str
    template_id: Optional[str] = None
    template_name: Optional[str] = None
    stock_count: int
    success_count: int
    fail_count: int
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    report_path: Optional[str] = None
    results_json: Optional[str] = None


class BatchRunListResponse(BaseModel):
    runs: list[BatchRunItem]


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


# In-memory progress tracker for running batches
_running_batch: Optional[dict] = None


@router.post("/run", status_code=202)
async def trigger_batch_run(request: BatchRunTriggerRequest):
    """手动触发跑批。每只股票一次 AI 调用，3 只并发。"""
    store = get_prompt_template_store()
    template = store.get(request.template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="模板不存在")

    config = get_config()
    if not request.stock_codes:
        stock_codes = config.stock_list
    else:
        stock_codes = request.stock_codes

    if not stock_codes:
        raise HTTPException(status_code=400, detail="股票列表为空")

    global _running_batch
    if _running_batch and _running_batch.get("running"):
        raise HTTPException(status_code=409, detail="已有跑批正在执行，请等待完成")

    runner = BatchRunner()
    _running_batch = {"running": True}

    def on_progress(state):
        _running_batch["state"] = state.to_dict()

    try:
        # Run in a thread so the request doesn't block (for 202 response)
        import threading

        def _run():
            try:
                state = runner.run(
                    stock_codes=stock_codes,
                    system_prompt=template["content"],
                    template_name=template["name"],
                    template_id=template["id"],
                    triggered_by="manual",
                    on_progress=on_progress,
                )
                _running_batch["state"] = state.to_dict()
            finally:
                _running_batch["running"] = False
                _running_batch.pop("state", None)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
    except Exception as exc:
        _running_batch["running"] = False
        raise HTTPException(status_code=500, detail=f"启动跑批失败: {exc}")

    return {"message": "跑批已启动", "stock_count": len(stock_codes), "template_name": template["name"]}


@router.get("/runs", response_model=BatchRunListResponse)
async def list_batch_runs(limit: int = 20):
    """获取跑批记录列表。"""
    db = DatabaseManager.get_instance()
    runs = db.get_batch_runs(limit=limit)
    return BatchRunListResponse(runs=[BatchRunItem(**r) for r in runs])


@router.get("/runs/{run_id}")
async def get_batch_run_detail(run_id: str):
    """获取单次跑批详情。"""
    db = DatabaseManager.get_instance()
    run = db.get_batch_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="跑批记录不存在")
    return BatchRunItem(**run)


@router.get("/runs/{run_id}/report.md", response_class=PlainTextResponse)
async def get_batch_run_report(run_id: str):
    """获取跑批汇总 MD 报告内容。"""
    db = DatabaseManager.get_instance()
    run = db.get_batch_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="跑批记录不存在")

    report_path = run.get("report_path")
    if not report_path:
        raise HTTPException(status_code=404, detail="报告尚未生成")

    path = Path(report_path)
    if not path.exists():
        # Try batch reports dir
        path = BATCH_REPORTS_DIR / path.name

    if not path.exists():
        raise HTTPException(status_code=404, detail="报告文件不存在")

    return path.read_text(encoding="utf-8")


@router.get("/runs/current")
async def get_current_batch_status():
    """获取当前正在执行的跑批进度。"""
    global _running_batch
    if _running_batch is None:
        return {"running": False, "state": None}
    if _running_batch.get("running"):
        state = _running_batch.get("state", {})
        return {"running": True, "state": state}
    return {"running": False, "state": _running_batch.get("state")}


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

    # Validate time format
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
