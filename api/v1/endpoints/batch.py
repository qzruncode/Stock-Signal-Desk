# -*- coding: utf-8 -*-
"""Batch run and schedule API."""

import logging
import json
import threading
from pathlib import Path
from typing import Optional
from datetime import datetime

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
    stock_codes_json: Optional[str] = None


class BatchRunListResponse(BaseModel):
    runs: list[BatchRunItem]


class BatchRunResumeRequest(BaseModel):
    stock_codes: list[str] = Field(default_factory=list, description="股票代码列表，旧批次缺少持久化列表时用于续跑")


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
_running_lock = threading.Lock()


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

    if _is_batch_running():
        raise HTTPException(status_code=409, detail="已有跑批正在执行，请等待完成")

    runner = BatchRunner()

    try:
        _start_batch_thread(
            lambda on_progress: runner.run(
                stock_codes=stock_codes,
                system_prompt=template["content"],
                template_name=template["name"],
                template_id=template["id"],
                triggered_by="manual",
                on_progress=on_progress,
            )
        )
    except Exception as exc:
        _mark_batch_stopped()
        raise HTTPException(status_code=500, detail=f"启动跑批失败: {exc}")

    return {"message": "跑批已启动", "stock_count": len(stock_codes), "template_name": template["name"]}


@router.get("/runs", response_model=BatchRunListResponse)
async def list_batch_runs(limit: int = 20):
    """获取跑批记录列表。"""
    db = DatabaseManager.get_instance()
    runs = db.get_batch_runs(limit=limit)
    return BatchRunListResponse(runs=[BatchRunItem(**r) for r in runs])


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


@router.get("/runs/{run_id}")
async def get_batch_run_detail(run_id: str):
    """获取单次跑批详情。"""
    db = DatabaseManager.get_instance()
    run = db.get_batch_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="跑批记录不存在")
    return BatchRunItem(**run)


@router.post("/runs/{run_id}/resume", status_code=202)
async def resume_batch_run(run_id: str, request: BatchRunResumeRequest):
    """Resume an interrupted batch run and skip stocks that already have persisted results."""
    if _is_batch_running():
        raise HTTPException(status_code=409, detail="已有跑批正在执行，请等待完成")

    db = DatabaseManager.get_instance()
    run = db.get_batch_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="跑批记录不存在")
    if run.get("completed_at"):
        raise HTTPException(status_code=400, detail="跑批已完成，无需续跑")

    store = get_prompt_template_store()
    template_id = run.get("template_id") or ""
    template = store.get(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="模板不存在，无法续跑")

    stock_codes = _resolve_resume_stock_codes(run, request.stock_codes)
    if not stock_codes:
        raise HTTPException(status_code=400, detail="无法确定续跑股票列表")

    existing_results = _filter_results_for_stock_codes(
        _parse_results_json(run.get("results_json")),
        stock_codes,
    )
    pending_count = len([code for code in stock_codes if code not in existing_results])
    runner = BatchRunner()

    try:
        _start_batch_thread(
            lambda on_progress: runner.resume(
                run_id=run_id,
                stock_codes=stock_codes,
                system_prompt=template["content"],
                template_name=template["name"],
                started_at=_parse_started_at(run.get("started_at")),
                existing_results=existing_results,
                on_progress=on_progress,
            )
        )
    except Exception as exc:
        _mark_batch_stopped()
        raise HTTPException(status_code=500, detail=f"启动续跑失败: {exc}")

    return {
        "message": "续跑已启动",
        "stock_count": len(stock_codes),
        "pending_count": pending_count,
        "template_name": template["name"],
    }


@router.get("/runs/{run_id}/report.md", response_class=PlainTextResponse)
async def get_batch_run_report(run_id: str):
    """获取跑批汇总 MD 报告内容。"""
    db = DatabaseManager.get_instance()
    run = db.get_batch_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="跑批记录不存在")

    report_path = run.get("report_path")
    if not report_path:
        partial_report = _build_partial_report_from_run(run)
        if partial_report:
            return partial_report
        raise HTTPException(status_code=404, detail="报告尚未生成")

    path = Path(report_path)
    if not path.exists():
        # Try batch reports dir
        path = BATCH_REPORTS_DIR / path.name

    if not path.exists():
        raise HTTPException(status_code=404, detail="报告文件不存在")

    return path.read_text(encoding="utf-8")


def _build_partial_report_from_run(run: dict) -> str:
    try:
        results = json.loads(run.get("results_json") or "{}")
    except Exception:
        return ""
    if not isinstance(results, dict) or not results:
        return ""

    lines = [
        "# 批量分析报告（部分结果）",
        "",
        f"- **触发时间**: {run.get('started_at') or '-'}",
        f"- **分析模板**: {run.get('template_name') or '-'}",
        f"- **股票数量**: {run.get('stock_count') or 0}",
        f"- **成功**: {run.get('success_count') or 0} / **失败**: {run.get('fail_count') or 0}",
        "",
        "---",
        "",
    ]

    for code, result in results.items():
        if code == "__all__" or not isinstance(result, dict):
            continue
        lines.append(f"## {code}")
        lines.append("")
        if result.get("success"):
            lines.append(f"> 模型: {result.get('model') or '-'}")
            lines.append("")
            lines.append(result.get("text") or "")
        else:
            lines.append(f"> 分析失败: {result.get('text') or '未知错误'}")
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def resume_incomplete_batches_on_startup() -> bool:
    """Resume the latest interrupted batch that has a persisted stock list."""
    if _is_batch_running():
        return False

    db = DatabaseManager.get_instance()
    runs = db.get_incomplete_batch_runs(limit=5)
    for run in runs:
        stock_codes = _resolve_auto_resume_stock_codes(run)
        if not stock_codes:
            continue
        existing_results = _filter_results_for_stock_codes(
            _parse_results_json(run.get("results_json")),
            stock_codes,
        )
        if len(existing_results) == 0 or len(existing_results) >= len(stock_codes):
            continue

        store = get_prompt_template_store()
        template = store.get(run.get("template_id") or "")
        if template is None:
            logger.warning("Cannot auto-resume batch %s: template missing", run.get("run_id"))
            continue

        runner = BatchRunner()
        run_id = run["run_id"]
        _start_batch_thread(
            lambda on_progress, run_id=run_id, stock_codes=stock_codes, existing_results=existing_results, template=template, run=run: runner.resume(
                run_id=run_id,
                stock_codes=stock_codes,
                system_prompt=template["content"],
                template_name=template["name"],
                started_at=_parse_started_at(run.get("started_at")),
                existing_results=existing_results,
                on_progress=on_progress,
            )
        )
        logger.info("Auto-resumed interrupted batch run: run_id=%s", run_id)
        return True
    return False


def _is_batch_running() -> bool:
    global _running_batch
    return bool(_running_batch and _running_batch.get("running"))


def _mark_batch_stopped():
    global _running_batch
    with _running_lock:
        if _running_batch is not None:
            _running_batch["running"] = False


def _start_batch_thread(run_factory):
    global _running_batch
    with _running_lock:
        if _running_batch and _running_batch.get("running"):
            raise HTTPException(status_code=409, detail="已有跑批正在执行，请等待完成")
        _running_batch = {"running": True, "state": None}

    def on_progress(state):
        if _running_batch is not None:
            _running_batch["state"] = state.to_dict()

    def _run():
        global _running_batch
        try:
            state = run_factory(on_progress)
            if _running_batch is not None:
                _running_batch["state"] = state.to_dict()
        finally:
            _mark_batch_stopped()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()


def _parse_results_json(raw: Optional[str]) -> dict:
    try:
        parsed = json.loads(raw or "{}")
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(code): result
        for code, result in parsed.items()
        if code != "__all__" and isinstance(result, dict)
    }


def _parse_stock_codes_json(raw: Optional[str]) -> list[str]:
    try:
        parsed = json.loads(raw or "[]")
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(code).strip() for code in parsed if str(code).strip()]


def _resolve_resume_stock_codes(run: dict, fallback_stock_codes: list[str]) -> list[str]:
    stored_codes = _parse_stock_codes_json(run.get("stock_codes_json"))
    if stored_codes:
        return stored_codes
    return [code.strip() for code in fallback_stock_codes if code.strip()]


def _resolve_auto_resume_stock_codes(run: dict) -> list[str]:
    existing_result_count = len(_parse_results_json(run.get("results_json")))
    stock_count = int(run.get("stock_count") or 0)
    if existing_result_count <= 0 or existing_result_count >= stock_count:
        return []

    stored_codes = _parse_stock_codes_json(run.get("stock_codes_json"))
    if stored_codes:
        return stored_codes

    config_codes = [code.strip() for code in get_config().stock_list if code.strip()]
    if config_codes and len(config_codes) == stock_count:
        return config_codes
    return []


def _filter_results_for_stock_codes(results: dict, stock_codes: list[str]) -> dict:
    stock_code_set = set(stock_codes)
    return {code: result for code, result in results.items() if code in stock_code_set}


def _parse_started_at(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


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
