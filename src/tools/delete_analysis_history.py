"""Delete persisted analysis history after explicit confirmation."""

from __future__ import annotations

from typing import Any

from src.services.history_service import HistoryService
from src.tools._workflow import envelope, require_confirmation
from src.tools.base import ToolSpec, object_schema


def delete_analysis_history(record_ids: str, confirmed: bool = False) -> dict[str, Any]:
    ids = sorted({int(part.strip()) for part in record_ids.split(",") if part.strip()})
    if not ids:
        raise ValueError("record_ids 不能为空")
    require_confirmation(confirmed, f"删除 {len(ids)} 条分析历史")
    deleted = HistoryService().delete_history_records(ids)
    return envelope(action="delete", requested_ids=ids, deleted_count=deleted)


TOOL = ToolSpec(
    name="delete_analysis_history",
    description=(
        "永久删除分析历史。只有用户明确指定要删除的记录并确认不可恢复时才能调用；"
        "confirmed 必须反映用户本轮的明确确认，不得由推荐、清理建议或模糊表达推断。"
    ),
    parameters=object_schema(
        {
            "record_ids": {"type": "string", "description": "逗号分隔的历史记录主键 ID"},
            "confirmed": {"type": "boolean", "description": "用户是否已经明确确认永久删除", "default": False},
        },
        required=("record_ids", "confirmed"),
    ),
    executor=delete_analysis_history,
    category="action",
)


__all__ = ["TOOL", "delete_analysis_history"]
