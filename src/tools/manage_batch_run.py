"""Inspect and control persistent batch-analysis runs."""

from __future__ import annotations

from typing import Any

from src.tools._workflow import envelope, model_dump, require_confirmation, run_async
from src.tools.base import ToolSpec, object_schema


def manage_batch_run(
    action: str, run_id: str = "", symbols: str = "", limit: int = 20, confirmed: bool = False
) -> dict[str, Any]:
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


def list_batch_runs(limit: int = 20) -> dict[str, Any]:
    from api.v1.endpoints.batches.run import list_batch_runs as _list_batch_runs

    result = run_async(_list_batch_runs(limit=max(1, min(int(limit), 100))))
    items = model_dump(result).get("runs") or []
    return envelope(action="list", item_count=len(items), items=items)


def get_current_batch_run_status() -> dict[str, Any]:
    from api.v1.endpoints.batches.run import get_current_batch_status

    return envelope(action="status", **run_async(get_current_batch_status()))


def read_batch_run(run_id: str) -> dict[str, Any]:
    from api.v1.endpoints.batches.run import get_batch_run_detail

    return envelope(action="detail", run=model_dump(run_async(get_batch_run_detail(run_id))))


def read_batch_run_report(run_id: str) -> dict[str, Any]:
    from api.v1.endpoints.batches.run import get_batch_run_report

    content = run_async(get_batch_run_report(run_id))
    return envelope(
        action="report",
        run_id=run_id,
        markdown=str(content),
        markdown_length=len(str(content)),
    )


def pause_batch_run() -> dict[str, Any]:
    from api.v1.endpoints.batches.run import pause_current_batch_run

    return envelope(action="pause", **run_async(pause_current_batch_run()))


def continue_batch_run() -> dict[str, Any]:
    from api.v1.endpoints.batches.run import resume_current_batch_run

    return envelope(action="continue", **run_async(resume_current_batch_run()))


def resume_failed_batch_run(run_id: str, symbols: str = "") -> dict[str, Any]:
    from api.v1.endpoints.batches.run import BatchRunResumeRequest, resume_batch_run

    codes = [part.strip() for part in symbols.split(",") if part.strip()]
    return envelope(
        action="resume_failed",
        **run_async(
            resume_batch_run(run_id, BatchRunResumeRequest(stock_codes=codes))
        ),
    )


def regenerate_batch_run_report(run_id: str) -> dict[str, Any]:
    from api.v1.endpoints.batches.run import regenerate_batch_run_report as _regenerate

    return envelope(
        action="regenerate_report",
        **model_dump(run_async(_regenerate(run_id))),
    )


def send_batch_run_notification(run_id: str) -> dict[str, Any]:
    return manage_batch_run("notify", run_id=run_id, confirmed=True)


def stop_batch_run() -> dict[str, Any]:
    from api.v1.endpoints.batches.run import stop_current_batch_run

    return envelope(action="stop", **run_async(stop_current_batch_run()))


def delete_batch_run(run_id: str) -> dict[str, Any]:
    from api.v1.endpoints.batches.run import delete_batch_run as _delete_batch_run

    run_async(_delete_batch_run(run_id))
    return envelope(action="delete", run_id=run_id, deleted=True)


TOOLS = (
    ToolSpec(
        name="list_batch_runs",
        description="读取近期批量分析任务列表；不修改任务。",
        parameters=object_schema(
            {"limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}},
        ),
        executor=list_batch_runs,
        category="action",
    ),
    ToolSpec(
        name="get_current_batch_run_status",
        description="读取当前批量分析任务的状态；不修改任务。",
        parameters=object_schema(),
        executor=get_current_batch_run_status,
        category="action",
    ),
    ToolSpec(
        name="read_batch_run",
        description="读取一个批量分析任务的详情；不修改任务。",
        parameters=object_schema(
            {"run_id": {"type": "string", "description": "批量任务 ID"}},
            required=("run_id",),
        ),
        executor=read_batch_run,
        category="action",
    ),
    ToolSpec(
        name="read_batch_run_report",
        description="读取一个批量分析任务已生成的报告；不修改任务。",
        parameters=object_schema(
            {"run_id": {"type": "string", "description": "批量任务 ID"}},
            required=("run_id",),
        ),
        executor=read_batch_run_report,
        category="action",
    ),
    ToolSpec(
        name="pause_batch_run",
        description="暂停当前批量分析任务；这是一次外部操作，必须经过用户审批。",
        parameters=object_schema(),
        executor=pause_batch_run,
        category="action",
        effect="side_effect",
    ),
    ToolSpec(
        name="continue_batch_run",
        description="继续当前已暂停的批量分析任务；这是一次外部操作，必须经过用户审批。",
        parameters=object_schema(),
        executor=continue_batch_run,
        category="action",
        effect="side_effect",
    ),
    ToolSpec(
        name="resume_failed_batch_run",
        description="仅重新执行一个批量任务中失败的条目；这是一次外部操作，必须经过用户审批。",
        parameters=object_schema(
            {
                "run_id": {"type": "string", "description": "批量任务 ID"},
                "symbols": {"type": "string", "description": "可选的逗号分隔股票范围"},
            },
            required=("run_id",),
        ),
        executor=resume_failed_batch_run,
        category="action",
        effect="side_effect",
    ),
    ToolSpec(
        name="regenerate_batch_run_report",
        description="重新生成一个批量任务的报告；这是一次外部操作，必须经过用户审批。",
        parameters=object_schema(
            {"run_id": {"type": "string", "description": "批量任务 ID"}},
            required=("run_id",),
        ),
        executor=regenerate_batch_run_report,
        category="action",
        effect="side_effect",
    ),
    ToolSpec(
        name="stop_batch_run",
        description="停止当前批量分析任务；这是一次外部操作，必须经过用户审批。",
        parameters=object_schema(),
        executor=stop_batch_run,
        category="action",
        effect="side_effect",
    ),
    ToolSpec(
        name="delete_batch_run",
        description="删除一个批量分析任务记录；这是一次持久化修改，必须经过用户审批。",
        parameters=object_schema(
            {"run_id": {"type": "string", "description": "批量任务 ID"}},
            required=("run_id",),
        ),
        executor=delete_batch_run,
        category="action",
        effect="side_effect",
    ),
)


__all__ = [
    "TOOLS",
    "continue_batch_run",
    "delete_batch_run",
    "get_current_batch_run_status",
    "list_batch_runs",
    "manage_batch_run",
    "pause_batch_run",
    "read_batch_run",
    "read_batch_run_report",
    "regenerate_batch_run_report",
    "resume_failed_batch_run",
    "send_batch_run_notification",
    "stop_batch_run",
]
