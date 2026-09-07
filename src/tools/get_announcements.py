"""Business tool contracts over the independent data service."""

from __future__ import annotations
from typing import Any
from src.services.market_data_client import read_source
from src.tools.base import ToolSpec, object_schema


def get_announcements(
    symbol: str,
    days: int = 30,
    type: str = "all",
    limit: int = 30,
    use_cache: bool = True,
) -> dict[str, Any]:
    return read_source("announcements.get_announcements", locals())


def read_company_announcements_akshare(
    symbol: str, days: int = 30, limit: int = 30, *, use_cache: bool = True
) -> dict[str, Any]:
    return read_source("announcements.read_company_announcements_akshare", locals())


TOOLS = (
    ToolSpec(
        name="read_company_announcements_akshare",
        description="从 AKShare/东方财富的单一公司公告源读取指定时间窗内的正式公告；返回原始公告类型、日期和链接，不使用 RSS 兜底或标题词典分类。",
        parameters=object_schema(
            {
                "symbol": {"type": "string", "description": "A 股代码或名称"},
                "days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 730,
                    "default": 30,
                    "description": "向前查询自然日数",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 30,
                },
            },
            ["symbol"],
        ),
        executor=read_company_announcements_akshare,
        category="events",
    ),
)
__all__ = [
    "TOOLS",
    "get_announcements",
    "read_company_announcements_akshare",
]
