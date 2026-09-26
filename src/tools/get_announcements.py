"""Business tool contracts over the independent data service."""

from __future__ import annotations
from typing import Any
from src.services.market_data_client import read_source
from src.tools.base import ToolSpec, object_schema


def read_company_announcements_akshare(
    symbol: str, days: int = 30, limit: int = 30, *, use_cache: bool = True
) -> dict[str, Any]:
    return read_source("announcements.read_company_announcements_akshare", locals())


TOOLS = (
    ToolSpec(
        name="read_company_announcements_akshare",
        description="从 AKShare/东方财富的单一二手公告源读取指定时间窗内的公告。该工具不是交易所/巨潮正式披露入口，也不提供财报 PDF 原件；用户要下载或入库财报 PDF 时必须改用 search_company_financial_reports 与 import_company_financial_report。",
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
    "read_company_announcements_akshare",
]
