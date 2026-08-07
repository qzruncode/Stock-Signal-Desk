"""Read or update the persistent batch-analysis schedule."""

from __future__ import annotations

from typing import Any

from src.tools._workflow import envelope, model_dump, require_confirmation, run_async
from src.tools.base import ToolSpec, effect_by_argument, object_schema


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


TOOL = ToolSpec(
    name="manage_analysis_schedule",
    description=(
        "查看或修改自动批量分析计划。update 会改变后台自动执行行为，只有用户明确确认计划时间、启停状态和模板后"
        "才能传 confirmed=true。通知由计划关联的分析/通知配置负责。"
    ),
    parameters=object_schema(
        {
            "action": {"type": "string", "enum": ["get", "update"]},
            "enabled": {"type": "boolean", "default": False},
            "times": {"type": "string", "description": "逗号分隔的 HH:MM，如 09:00,15:10"},
            "prompt_template_id": {"type": "string"},
            "confirmed": {"type": "boolean", "default": False},
        },
        required=("action",),
    ),
    executor=manage_analysis_schedule,
    category="action",
    effect_resolver=effect_by_argument("action", {"update"}),
)


__all__ = ["TOOL", "manage_analysis_schedule"]
