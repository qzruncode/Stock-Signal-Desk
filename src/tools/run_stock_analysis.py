"""Start a persisted asynchronous stock-analysis task."""

from __future__ import annotations

from typing import Any

from src.services.task_queue import get_task_queue
from src.tools._workflow import envelope
from src.tools.base import ToolSpec, object_schema


def run_stock_analysis(
    symbol: str,
    force_refresh: bool = False,
    notify_on_complete: bool = False,
    prompt_template_id: str = "",
) -> dict[str, Any]:
    from api.v1.endpoints.analysis.trigger import _resolve_and_normalize_input

    code = _resolve_and_normalize_input(symbol)
    accepted, duplicates = get_task_queue().submit_tasks_batch(
        [code],
        original_query=symbol,
        selection_source="manual",
        report_type="detailed",
        force_refresh=bool(force_refresh),
        notify=bool(notify_on_complete),
        prompt_template_id=prompt_template_id or None,
    )
    if duplicates:
        duplicate = duplicates[0]
        return envelope(
            accepted=False,
            duplicate=True,
            stock_code=duplicate.stock_code,
            task_id=duplicate.existing_task_id,
            status="running",
            message=str(duplicate),
        )
    task = accepted[0]
    return envelope(
        accepted=True,
        duplicate=False,
        task_id=task.task_id,
        stock_code=task.stock_code,
        status=task.status.value,
        progress=task.progress,
        message=task.message,
        prompt_template_id=prompt_template_id or None,
        notify_on_complete=bool(notify_on_complete),
    )


TOOL = ToolSpec(
    name="run_stock_analysis",
    description=(
        "启动会持久化报告的完整单股分析任务。只有用户明确要求分析、重新分析或生成正式报告时调用；"
        "普通行情或研究问题继续使用数据工具。notify_on_complete 仅在用户明确要求完成后通知时设为 true。"
    ),
    parameters=object_schema(
        {
            "symbol": {"type": "string", "description": "股票代码或精确名称"},
            "force_refresh": {"type": "boolean", "default": False},
            "notify_on_complete": {"type": "boolean", "default": False},
            "prompt_template_id": {"type": "string", "description": "分析模板 ID，可留空使用默认模板"},
        },
        required=("symbol",),
    ),
    executor=run_stock_analysis,
    category="action",
)


__all__ = ["TOOL", "run_stock_analysis"]
