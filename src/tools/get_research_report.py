"""Business tool contracts over the independent data service."""

from __future__ import annotations
from typing import Any
from src.services.market_data_client import read_source
from src.tools.base import ToolSpec, object_schema


def get_research_report(
    symbol: str, days: int = 365, limit: int = 20, use_cache: bool = True
) -> dict[str, Any]:
    return read_source("research_reports.get_research_report", locals())


def read_company_research_reports_akshare(
    symbol: str, days: int = 365, limit: int = 20, *, use_cache: bool = True
) -> dict[str, Any]:
    return read_source(
        "research_reports.read_company_research_reports_akshare", locals()
    )


TOOLS = (
    ToolSpec(
        name="read_company_research_reports_akshare",
        description="从 AKShare/东方财富的单一券商个股研报源读取一只 A 股的研报；返回日期、机构、评级、PDF 链接和原始预测字段，不搜索 RSS 或混合其他研究来源。这是 reference-only 来源索引，不包含 PDF 正文；若要用研报内容支撑实质性结论，必须继续调用 read_web_source 读取对应 URL。",
        parameters=object_schema(
            {
                "symbol": {"type": "string", "description": "A 股代码或名称"},
                "days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1825,
                    "default": 365,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 20,
                },
            },
            ["symbol"],
        ),
        executor=read_company_research_reports_akshare,
        category="research",
    ),
)
__all__ = ["TOOLS", "get_research_report", "read_company_research_reports_akshare"]
