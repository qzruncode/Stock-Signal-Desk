"""Search persisted analysis-report history."""

from __future__ import annotations

from typing import Any

from src.services.history_service import HistoryService
from src.tools._workflow import envelope
from src.tools.base import ToolSpec, object_schema


def search_analysis_history(
    symbol: str = "",
    start_date: str = "",
    end_date: str = "",
    page: int = 1,
    limit: int = 20,
) -> dict[str, Any]:
    result = HistoryService().get_history_list(
        stock_code=symbol.strip() or None,
        start_date=start_date.strip() or None,
        end_date=end_date.strip() or None,
        page=max(1, int(page)),
        limit=max(1, min(int(limit), 100)),
    )
    items = result.get("items") or []
    return envelope(
        total=int(result.get("total") or 0),
        page=max(1, int(page)),
        returned_count=len(items),
        has_more=max(1, int(page)) * max(1, min(int(limit), 100)) < int(result.get("total") or 0),
        items=items,
    )


TOOL = ToolSpec(
    name="search_analysis_history",
    description="查询已保存的正式分析报告历史，可按股票和日期筛选。用户询问过去的分析结论时优先调用。",
    parameters=object_schema(
        {
            "symbol": {"type": "string", "description": "股票代码，可留空"},
            "start_date": {"type": "string", "description": "YYYY-MM-DD，可留空"},
            "end_date": {"type": "string", "description": "YYYY-MM-DD，可留空"},
            "page": {"type": "integer", "minimum": 1, "default": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
        }
    ),
    executor=search_analysis_history,
    category="analysis",
)


__all__ = ["TOOL", "search_analysis_history"]
