"""Read persisted analysis task state."""

from __future__ import annotations

from typing import Any

from src.services.task_queue import get_task_queue
from src.tools._workflow import envelope, model_dump
from src.tools.base import ToolSpec, object_schema


def read_analysis_task(task_id: str) -> dict[str, Any]:
    """Read one persisted task by ID without falling back to a task list."""
    clean_task_id = str(task_id or "").strip()
    if not clean_task_id:
        raise ValueError("读取分析任务必须提供 task_id")
    from api.v1.endpoints.analysis.task import get_analysis_status as _get_status

    result = model_dump(_get_status(clean_task_id))
    return envelope(mode="detail", task=result)


def list_analysis_tasks(status: str = "", limit: int = 20) -> dict[str, Any]:
    """Read a bounded persisted task list without resolving a single task."""
    tasks = get_task_queue().list_all_tasks(limit=max(1, min(int(limit), 100)))
    statuses = {item.strip().lower() for item in status.split(",") if item.strip()}
    if statuses:
        tasks = [task for task in tasks if task.status.value in statuses]
    return envelope(
        mode="list",
        item_count=len(tasks),
        items=[model_dump(task) for task in tasks],
        stats=get_task_queue().get_task_stats(),
    )


def get_analysis_status(task_id: str = "", status: str = "", limit: int = 20) -> dict[str, Any]:
    """Legacy convenience adapter retained for non-Agent callers only."""
    if str(task_id or "").strip():
        return read_analysis_task(task_id)
    return list_analysis_tasks(status=status, limit=limit)


TOOLS = (
    ToolSpec(
        name="read_analysis_task",
        description="读取一个指定分析任务的持久化状态和进度；不会列出其他任务或启动新分析。",
        parameters=object_schema(
            {"task_id": {"type": "string", "minLength": 1, "description": "任务 ID"}},
            required=("task_id",),
        ),
        executor=read_analysis_task,
        category="analysis",
    ),
    ToolSpec(
        name="list_analysis_tasks",
        description="读取运行中或最近的分析任务列表；不会按 ID 加载单个任务或启动新分析。",
        parameters=object_schema(
            {
                "status": {"type": "string", "description": "可选状态过滤，逗号分隔，如 pending,processing"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            }
        ),
        executor=list_analysis_tasks,
        category="analysis",
    ),
)


__all__ = [
    "TOOLS",
    "get_analysis_status",
    "list_analysis_tasks",
    "read_analysis_task",
]
