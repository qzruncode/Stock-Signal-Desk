# -*- coding: utf-8 -*-
"""Batch run endpoints — trigger, pause, resume, stop, query runs."""

import logging
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from api.v1.endpoints.batches import router
from api.v1.endpoints.batches.helpers import (
    _is_batch_running,
    _mark_batch_stopped,
    _get_running_control,
    _get_running_batch_snapshot,
    _build_batch_state_from_record,
    _is_persisted_batch_active,
    _claim_batch_execution,
    _persist_batch_lease,
    _set_running_status,
    _persist_current_status,
    _start_batch_thread,
    _parse_results_json,
    _parse_stock_codes_json,
    _resolve_resume_stock_codes,
    _resolve_auto_resume_stock_codes,
    _filter_results_for_stock_codes,
    _parse_started_at,
    _delete_batch_report_file,
    _regenerate_batch_report_for_run,
    _build_partial_report_from_run,
)
from src.batch_runner import (
    BatchRunControl,
    BatchRunner,
    BatchRunState,
    BATCH_REPORTS_DIR,
    _build_batch_notification_content,
    _write_aggregated_report,
)
from src.config import get_config
from src.prompt_templates import get_prompt_template_store
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)


class BatchRunTriggerRequest(BaseModel):
    stock_codes: list[str] = Field(..., description="股票代码列表")
    template_id: str = Field("", description="提示词模板 ID（模板分析模式必填）")
    analysis_mode: str = Field("template", description="分析模式：template / buy_criteria")
    force_refresh: bool = Field(False, description="买入判断模式下是否绕过当日缓存重新分析")
    triggered_by: Literal["manual", "scheduled"] = Field("manual", description="跑批触发来源")


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
    status: str = "completed"
    analysis_mode: Optional[str] = "template"


class BatchRunListResponse(BaseModel):
    runs: list[BatchRunItem]


class BatchRunResumeRequest(BaseModel):
    stock_codes: list[str] = Field(default_factory=list, description="股票代码列表，旧批次缺少持久化列表时用于续跑")


class BatchRunActionResponse(BaseModel):
    message: str
    report_path: Optional[str] = None


@router.post("/run", status_code=202)
async def trigger_batch_run(request: BatchRunTriggerRequest):
    """手动触发跑批。模板模式每股一次 AI 调用；买入判断模式每股跑 8 步硬筛。"""
    analysis_mode = request.analysis_mode if request.analysis_mode in ("template", "buy_criteria") else "template"

    config = get_config()
    if not request.stock_codes:
        stock_codes = config.stock_list
    else:
        stock_codes = request.stock_codes

    if not stock_codes:
        raise HTTPException(status_code=400, detail="股票列表为空")

    if _is_batch_running():
        raise HTTPException(status_code=409, detail="已有跑批正在执行，请等待完成")

    if analysis_mode == "buy_criteria":
        template_id = ""
        template_name = "买入判断筛选"
        system_prompt = ""
    else:
        store = get_prompt_template_store()
        template = store.get(request.template_id)
        if template is None:
            raise HTTPException(status_code=404, detail="模板不存在")
        template_id = template["id"]
        template_name = template["name"]
        system_prompt = template["content"]

    runner = BatchRunner(max_concurrent=1 if analysis_mode == "buy_criteria" else None)
    control = BatchRunControl()
    lease_id, claimed = _claim_batch_execution(
        trigger=request.triggered_by,
        total=len(stock_codes),
    )
    if not claimed:
        raise HTTPException(status_code=409, detail="已有跑批正在执行，请等待完成")

    try:
        _start_batch_thread(
            lambda on_progress: runner.run(
                stock_codes=stock_codes,
                system_prompt=system_prompt,
                template_name=template_name,
                template_id=template_id,
                triggered_by=request.triggered_by,
                analysis_mode=analysis_mode,
                force_refresh=request.force_refresh,
                control=control,
                on_progress=on_progress,
            ),
            control=control,
            lease_id=lease_id,
        )
    except Exception as exc:
        _mark_batch_stopped()
        _persist_batch_lease(lease_id, "failed", message="启动跑批失败", error=str(exc))
        raise HTTPException(status_code=500, detail=f"启动跑批失败: {exc}")

    return {"message": "跑批已启动", "stock_count": len(stock_codes), "template_name": template_name}


@router.get("/runs", response_model=BatchRunListResponse)
async def list_batch_runs(limit: int = 20):
    """获取跑批记录列表。"""
    db = DatabaseManager.get_instance()
    runs = db.get_batch_runs(limit=limit)
    return BatchRunListResponse(runs=[BatchRunItem(**r) for r in runs])


@router.get("/runs/current")
async def get_current_batch_status():
    """获取当前正在执行的跑批进度。"""
    local_state = _get_running_batch_snapshot()
    local_running = bool(local_state and local_state.get("running"))
    if local_state is not None:
        if local_running:
            state = local_state.get("state", {})
            if isinstance(state, dict) and state:
                return {"running": True, "state": state}
        else:
            return {"running": False, "state": local_state.get("state")}

    # The worker may live in another process, or the API process may have
    # restarted after progress was persisted.  Surface that durable state
    # instead of reporting an idle run while work is still recoverable.
    try:
        runs = DatabaseManager.get_instance().get_batch_runs(limit=20)
    except Exception:
        logger.exception("Failed to read persisted current batch status")
        return {"running": local_running, "state": None}
    if not runs:
        return {"running": local_running, "state": None}
    # A stale-worker takeover can leave an older active row beside a newer
    # terminal row. Prefer recoverable work over recency in that case.
    run = next((candidate for candidate in runs if _is_persisted_batch_active(candidate)), runs[0])
    state = _build_batch_state_from_record(run)
    if _is_persisted_batch_active(run):
        return {"running": True, "state": state}
    if local_running:
        # The worker thread can be visible locally before it has created the
        # durable BatchRun row. Do not let an older terminal row hide it.
        return {"running": True, "state": local_state.get("state")}
    return {"running": False, "state": state}


def _find_persisted_active_batch_run() -> Optional[dict]:
    try:
        runs = DatabaseManager.get_instance().get_batch_runs(limit=20)
    except Exception as exc:
        logger.exception("Failed to inspect persisted active batch runs")
        raise HTTPException(status_code=503, detail="无法读取跑批状态，请稍后重试") from exc
    return next((run for run in runs if _is_persisted_batch_active(run)), None)


def _update_persisted_current_batch_status(status: str) -> bool:
    try:
        run = _find_persisted_active_batch_run()
        if run is None:
            return False
        return bool(DatabaseManager.get_instance().update_batch_run_status(run["run_id"], status))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to persist remote batch control: %s", status)
        raise HTTPException(status_code=503, detail="无法写入跑批控制状态，请稍后重试") from exc


@router.post("/runs/current/resume")
async def resume_current_batch_run():
    control = _get_running_control()
    if control is None:
        if not _update_persisted_current_batch_status("running"):
            raise HTTPException(status_code=404, detail="当前没有正在执行的跑批")
        return {"message": "跑批已继续，控制指令已写入持久化状态"}
    control.resume()
    _set_running_status("running", "继续跑批中...")
    _persist_current_status("running")
    return {"message": "跑批已继续"}


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

    analysis_mode = run.get("analysis_mode") or "template"
    if analysis_mode == "buy_criteria":
        template_name = run.get("template_name") or "买入判断筛选"
        system_prompt = ""
    else:
        store = get_prompt_template_store()
        template_id = run.get("template_id") or ""
        template = store.get(template_id)
        if template is None:
            raise HTTPException(status_code=404, detail="模板不存在，无法续跑")
        template_name = template["name"]
        system_prompt = template["content"]

    stock_codes = _resolve_resume_stock_codes(run, request.stock_codes)
    if not stock_codes:
        raise HTTPException(status_code=400, detail="无法确定续跑股票列表")

    existing_results = _filter_results_for_stock_codes(
        _parse_results_json(run.get("results_json")),
        stock_codes,
    )
    pending_count = len([code for code in stock_codes if code not in existing_results])
    runner = BatchRunner(max_concurrent=1 if analysis_mode == "buy_criteria" else None)
    control = BatchRunControl()
    lease_id, claimed = _claim_batch_execution(
        trigger="resume",
        total=len(stock_codes),
    )
    if not claimed:
        raise HTTPException(status_code=409, detail="已有跑批正在执行，请等待完成")

    try:
        _start_batch_thread(
            lambda on_progress: runner.resume(
                run_id=run_id,
                stock_codes=stock_codes,
                system_prompt=system_prompt,
                template_name=template_name,
                analysis_mode=analysis_mode,
                started_at=_parse_started_at(run.get("started_at")),
                existing_results=existing_results,
                control=control,
                on_progress=on_progress,
            ),
            control=control,
            lease_id=lease_id,
        )
    except Exception as exc:
        _mark_batch_stopped()
        _persist_batch_lease(lease_id, "failed", message="启动续跑失败", error=str(exc))
        raise HTTPException(status_code=500, detail=f"启动续跑失败: {exc}")

    return {
        "message": "续跑已启动",
        "stock_count": len(stock_codes),
        "pending_count": pending_count,
        "template_name": template_name,
    }


@router.post("/runs/current/pause")
async def pause_current_batch_run():
    control = _get_running_control()
    if control is None:
        if not _update_persisted_current_batch_status("paused"):
            raise HTTPException(status_code=404, detail="当前没有正在执行的跑批")
        return {"message": "跑批已暂停，控制指令已写入持久化状态"}
    control.pause()
    _set_running_status("paused", "已暂停：正在执行中的请求会先收尾")
    _persist_current_status("paused")
    return {"message": "跑批已暂停"}


@router.post("/runs/current/stop")
async def stop_current_batch_run():
    control = _get_running_control()
    if control is None:
        if not _update_persisted_current_batch_status("stopping"):
            raise HTTPException(status_code=404, detail="当前没有正在执行的跑批")
        return {"message": "跑批正在终止，控制指令已写入持久化状态"}
    control.stop()
    _set_running_status("stopping", "正在终止：已开始的请求会先收尾")
    _persist_current_status("stopping")
    return {"message": "跑批正在终止"}


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
        path = BATCH_REPORTS_DIR / path.name

    if not path.exists():
        raise HTTPException(status_code=404, detail="报告文件不存在")

    return path.read_text(encoding="utf-8")


@router.post("/runs/{run_id}/report/regenerate", response_model=BatchRunActionResponse)
async def regenerate_batch_run_report(run_id: str):
    """基于已保存的单股结果重新生成跑批汇总 MD。"""
    db = DatabaseManager.get_instance()
    run = db.get_batch_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="跑批记录不存在")

    report_path, _ = _regenerate_batch_report_for_run(run)
    if not db.update_batch_run_report_path(run_id, report_path):
        raise HTTPException(status_code=404, detail="跑批记录不存在")

    return BatchRunActionResponse(message="汇总 MD 已重新生成", report_path=report_path)


@router.post("/runs/{run_id}/notify", response_model=BatchRunActionResponse)
async def notify_batch_run(run_id: str):
    """手动发送跑批汇总通知。发送前会先用当前规则重建汇总报告。"""
    db = DatabaseManager.get_instance()
    run = db.get_batch_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="跑批记录不存在")

    report_path, state = _regenerate_batch_report_for_run(run)
    if not db.update_batch_run_report_path(run_id, report_path):
        raise HTTPException(status_code=404, detail="跑批记录不存在")

    try:
        from src.notification import get_notification_service

        content = _build_batch_notification_content(
            run_id,
            state,
            run.get("template_name") or "-",
            report_path,
            analysis_mode=run.get("analysis_mode") or "template",
        )
        get_notification_service().send(content)
    except Exception as exc:
        logger.exception("Failed to send manual batch notification: run_id=%s", run_id)
        raise HTTPException(status_code=500, detail=f"通知发送失败: {exc}") from exc

    return BatchRunActionResponse(message="跑批汇总通知已发送", report_path=report_path)


@router.delete("/runs/{run_id}", status_code=204)
async def delete_batch_run(run_id: str):
    running_batch = _get_running_batch_snapshot()
    if running_batch and running_batch.get("running"):
        state = running_batch.get("state") or {}
        if state.get("run_id") == run_id:
            raise HTTPException(status_code=409, detail="当前跑批正在执行，请先终止后再删除")
    # A process-local state can be empty during startup, or can describe a
    # different worker. Always consult the durable record before deleting.
    persisted = _find_persisted_active_batch_run()
    if persisted and persisted.get("run_id") == run_id:
        raise HTTPException(status_code=409, detail="当前跑批正在执行，请先终止后再删除")

    db = DatabaseManager.get_instance()
    run = db.get_batch_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="跑批记录不存在")
    if not db.delete_batch_run(run_id):
        raise HTTPException(status_code=404, detail="跑批记录不存在")
    _delete_batch_report_file(run.get("report_path"))
    return None
