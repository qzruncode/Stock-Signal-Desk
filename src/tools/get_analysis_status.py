"""Read persisted analysis task state."""

from __future__ import annotations

from typing import Any

from src.services.task_queue import get_task_queue
from src.tools._workflow import envelope, model_dump
from src.tools.base import ToolSpec, object_schema


def get_analysis_status(task_id: str = "", status: str = "", limit: int = 20) -> dict[str, Any]:
    if task_id.strip():
        from api.v1.endpoints.analysis.task import get_analysis_status as _get_status

        result = model_dump(_get_status(task_id.strip()))
        return envelope(mode="detail", task=result)
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


TOOL = ToolSpec(
    name="get_analysis_status",
    description="查询一个分析任务，或查看运行中/最近的分析任务及进度。不会启动新分析。",
    parameters=object_schema(
        {
            "task_id": {"type": "string", "description": "任务 ID；留空时返回任务列表"},
            "status": {"type": "string", "description": "列表状态过滤，逗号分隔，如 pending,processing"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
        }
    ),
    executor=get_analysis_status,
    category="analysis",
)


__all__ = ["TOOL", "get_analysis_status"]
