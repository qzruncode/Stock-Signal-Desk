"""Read or update the persistent batch-analysis schedule."""

from __future__ import annotations

from typing import Any

from src.tools._workflow import envelope, model_dump, require_confirmation, run_async
from src.tools.base import ToolSpec, object_schema


def manage_analysis_schedule(
    action: str,
    enabled: bool = False,
    times: str = "",
    prompt_template_id: str = "",
    confirmed: bool = False,
) -> dict[str, Any]:
    from api.v1.endpoints.batches.schedule import (
        BatchScheduleRequest,
        get_batch_schedule,
        update_batch_schedule,
    )

    if action == "get":
        return envelope(action=action, schedule=model_dump(run_async(get_batch_schedule())))
    if action == "update":
        require_confirmation(confirmed, "修改自动分析计划")
        parsed_times = sorted({part.strip() for part in times.split(",") if part.strip()})
        template_id = prompt_template_id.strip()
        if not template_id:
            current = model_dump(run_async(get_batch_schedule()))
            template_id = str(current.get("template_id") or "")
        if not template_id:
            raise ValueError("修改自动分析计划必须指定分析模板")
        result = run_async(
            update_batch_schedule(
                BatchScheduleRequest(
                    enabled=bool(enabled),
                    times=parsed_times,
                    template_id=template_id,
                )
            )
        )
        return envelope(action=action, schedule=model_dump(result))
    raise ValueError("action 必须是 get 或 update")


def get_analysis_schedule() -> dict[str, Any]:
    from api.v1.endpoints.batches.schedule import get_batch_schedule

    return envelope(action="get", schedule=model_dump(run_async(get_batch_schedule())))


def update_analysis_schedule(
    enabled: bool,
    times: str,
    prompt_template_id: str,
) -> dict[str, Any]:
    if not str(prompt_template_id or "").strip():
        raise ValueError(
            "更新自动分析计划必须提供 prompt_template_id；请先读取当前计划或模板列表后再选择。"
        )
    from api.v1.endpoints.batches.schedule import (
        BatchScheduleRequest,
        update_batch_schedule,
    )

    parsed_times = sorted({part.strip() for part in str(times or "").split(",") if part.strip()})
    result = run_async(
        update_batch_schedule(
            BatchScheduleRequest(
                enabled=bool(enabled),
                times=parsed_times,
                template_id=str(prompt_template_id).strip(),
            )
        )
    )
    return envelope(action="update", schedule=model_dump(result))


TOOLS = (
    ToolSpec(
        name="get_analysis_schedule",
        description="读取当前自动分析计划；不修改后台执行配置。",
        parameters=object_schema(),
        executor=get_analysis_schedule,
        category="action",
    ),
    ToolSpec(
        name="update_analysis_schedule",
        description="更新自动分析计划的启停状态、时间和模板；这是一次持久化修改，必须经过用户审批。",
        parameters=object_schema(
            {
                "enabled": {"type": "boolean", "description": "是否启用自动分析"},
                "times": {"type": "string", "description": "逗号分隔的 HH:MM，如 09:00,15:10"},
                "prompt_template_id": {"type": "string", "minLength": 1, "description": "要使用的模板 ID；需先明确读取或选择"},
            },
            required=("enabled", "times", "prompt_template_id"),
        ),
        executor=update_analysis_schedule,
        category="action",
        effect="side_effect",
    ),
)


__all__ = [
    "TOOLS",
    "get_analysis_schedule",
    "manage_analysis_schedule",
    "update_analysis_schedule",
]
