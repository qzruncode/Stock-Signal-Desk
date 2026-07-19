"""Inspect and control persistent batch-analysis runs."""

from __future__ import annotations

from typing import Any

from src.tools._workflow import envelope, model_dump, require_confirmation, run_async
from src.tools.base import ToolSpec, object_schema


def manage_batch_run(action: str, run_id: str = "", symbols: str = "", limit: int = 20, confirmed: bool = False) -> dict[str, Any]:
    from api.v1.endpoints.batches.run import (
        BatchRunResumeRequest,
        delete_batch_run,
        get_batch_run_detail,
        get_batch_run_report,
        get_current_batch_status,
        list_batch_runs,
        notify_batch_run,
        pause_current_batch_run,
        regenerate_batch_run_report,
        resume_batch_run,
        resume_current_batch_run,
        stop_current_batch_run,
    )

    if action == "list":
        result = run_async(list_batch_runs(limit=max(1, min(int(limit), 100))))
        items = model_dump(result).get("runs") or []
        return envelope(action=action, item_count=len(items), items=items)
    if action == "status":
        return envelope(action=action, **run_async(get_current_batch_status()))
    if action == "detail":
        return envelope(action=action, run=model_dump(run_async(get_batch_run_detail(run_id))))
    if action == "report":
        content = run_async(get_batch_run_report(run_id))
        return envelope(action=action, run_id=run_id, markdown=str(content), markdown_length=len(str(content)))
    if action == "pause":
        return envelope(action=action, **run_async(pause_current_batch_run()))
    if action == "continue":
        return envelope(action=action, **run_async(resume_current_batch_run()))
    if action == "resume_failed":
        codes = [part.strip() for part in symbols.split(",") if part.strip()]
        return envelope(action=action, **run_async(resume_batch_run(run_id, BatchRunResumeRequest(stock_codes=codes))))
    if action == "regenerate_report":
        return envelope(action=action, **model_dump(run_async(regenerate_batch_run_report(run_id))))
    if action == "notify":
        require_confirmation(confirmed, "发送批量分析通知")
        return envelope(action=action, **model_dump(run_async(notify_batch_run(run_id))))
    if action == "stop":
        require_confirmation(confirmed, "停止当前批量分析")
        return envelope(action=action, **run_async(stop_current_batch_run()))
    if action == "delete":
        require_confirmation(confirmed, "删除批量分析记录")
        run_async(delete_batch_run(run_id))
        return envelope(action=action, run_id=run_id, deleted=True)
    raise ValueError(f"不支持的 action: {action}")


TOOL = ToolSpec(
    name="manage_batch_run",
    description=(
        "查看和控制批量分析任务：列表、进度、详情、报告、暂停、继续、失败续跑、重建报告、通知、停止或删除。"
        "notify/stop/delete 只有用户明确确认时才可传 confirmed=true。"
    ),
    parameters=object_schema({
        "action": {"type": "string", "enum": ["list", "status", "detail", "report", "pause", "continue", "resume_failed", "regenerate_report", "notify", "stop", "delete"]},
        "run_id": {"type": "string"},
        "symbols": {"type": "string", "description": "失败续跑时可选的股票范围"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
        "confirmed": {"type": "boolean", "default": False},
    }, required=("action",)),
    executor=manage_batch_run,
    category="action",
)


__all__ = ["TOOL", "manage_batch_run"]
