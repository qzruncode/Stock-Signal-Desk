"""Atomic reads over one persisted analysis record."""

from __future__ import annotations

from typing import Any

from src.services.history_service import HistoryService
from src.storage import DatabaseManager
from src.tools._workflow import envelope, model_dump
from src.tools.base import ToolSpec, object_schema


def _resolve_report_detail(record_id: str) -> Any:
    service = HistoryService()
    detail = service.resolve_and_get_detail(record_id)
    if detail is None:
        raise ValueError(f"分析报告不存在: {record_id}")
    return detail


def read_analysis_report_summary(record_id: str) -> dict[str, Any]:
    from api.v1.endpoints._detail import _build_analysis_report

    detail = _resolve_report_detail(record_id)
    report = model_dump(_build_analysis_report(detail, DatabaseManager.get_instance()))
    return envelope(
        record_id=record_id,
        report=report,
        source_scope="persisted_analysis_report_summary",
    )


def read_analysis_report_markdown(record_id: str) -> dict[str, Any]:
    _resolve_report_detail(record_id)
    markdown = HistoryService().get_markdown_report(record_id)
    return envelope(
        record_id=record_id,
        markdown=markdown,
        markdown_length=len(markdown or ""),
        source_scope="persisted_analysis_report_markdown",
    )


def read_analysis_report_linked_news(record_id: str, limit: int = 20) -> dict[str, Any]:
    _resolve_report_detail(record_id)
    limit = int(limit)
    if not 1 <= limit <= 100:
        raise ValueError("limit 必须在 1 到 100 之间")
    news = HistoryService().resolve_and_get_news(record_id, limit=limit)
    return envelope(
        record_id=record_id,
        linked_news=news,
        linked_news_count=len(news),
        source_scope="persisted_analysis_report_linked_news",
    )


TOOLS = (
    ToolSpec(
        name="read_analysis_report_summary",
        description="读取一份历史分析报告的已持久化结构化内容。record_id 来自历史搜索或任务结果。",
        parameters=object_schema(
            {"record_id": {"type": "string", "description": "历史记录主键 ID 或 query/task ID"}},
            required=("record_id",),
        ),
        executor=read_analysis_report_summary,
        category="analysis",
        web_fallback=False,
    ),
    ToolSpec(
        name="read_analysis_report_markdown",
        description="读取一份历史分析报告的 Markdown 正文，不加载关联资讯。",
        parameters=object_schema(
            {"record_id": {"type": "string", "description": "历史记录主键 ID 或 query/task ID"}},
            required=("record_id",),
        ),
        executor=read_analysis_report_markdown,
        category="analysis",
        web_fallback=False,
    ),
    ToolSpec(
        name="read_analysis_report_linked_news",
        description="读取一份历史分析报告已关联的资讯记录，不读取报告正文。",
        parameters=object_schema(
            {
                "record_id": {"type": "string", "description": "历史记录主键 ID 或 query/task ID"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
            required=("record_id",),
        ),
        executor=read_analysis_report_linked_news,
        category="analysis",
        web_fallback=False,
    ),
)


__all__ = [
    "TOOLS",
    "read_analysis_report_linked_news",
    "read_analysis_report_markdown",
    "read_analysis_report_summary",
]
