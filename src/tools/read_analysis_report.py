"""Read one persisted report, its Markdown, and its linked news."""

from __future__ import annotations

from typing import Any

from src.services.history_service import HistoryService
from src.storage import DatabaseManager
from src.tools._workflow import envelope, model_dump
from src.tools.base import ToolSpec, object_schema


def read_analysis_report(record_id: str, include_markdown: bool = True, include_news: bool = True) -> dict[str, Any]:
    from api.v1.endpoints._detail import _build_analysis_report

    service = HistoryService()
    detail = service.resolve_and_get_detail(record_id)
    if detail is None:
        raise ValueError(f"分析报告不存在: {record_id}")
    report = model_dump(_build_analysis_report(detail, DatabaseManager.get_instance()))
    markdown = service.get_markdown_report(record_id) if include_markdown else None
    news = service.resolve_and_get_news(record_id, limit=20) if include_news else []
    return envelope(
        record_id=record_id,
        report=report,
        markdown=markdown,
        markdown_length=len(markdown or ""),
        linked_news=news,
        linked_news_count=len(news),
    )


TOOL = ToolSpec(
    name="read_analysis_report",
    description="读取指定历史分析报告的结构化结论、完整 Markdown 和关联资讯。record_id 来自历史搜索或任务结果。",
    parameters=object_schema({
        "record_id": {"type": "string", "description": "历史记录主键 ID 或 query/task ID"},
        "include_markdown": {"type": "boolean", "default": True},
        "include_news": {"type": "boolean", "default": True},
    }, required=("record_id",)),
    executor=read_analysis_report,
    category="analysis",
)


__all__ = ["TOOL", "read_analysis_report"]
