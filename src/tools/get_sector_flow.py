"""Business tool contracts over the independent data service."""

from __future__ import annotations
from typing import Any
from src.services.market_data_client import read_source
from src.tools.base import ToolSpec, object_schema


def read_sector_flow_eastmoney(
    type: str = "industry", period: str = "today", max_items: int = 50
) -> dict[str, Any]:
    return read_source("get_sector_flow.read_sector_flow_eastmoney", locals())


TOOLS = (
    ToolSpec(
        name="read_sector_flow_eastmoney",
        description="从东方财富读取 A 股行业或概念板块在指定周期的单页资金流原始记录，包含主力、超大单、大单、中单和小单净流入及占比、板块涨跌幅和来源提供的领涨股字段。保持数据源响应顺序，不计算板块排名、流入榜、流出榜或市场结论。",
        parameters=object_schema(
            {
                "type": {
                    "type": "string",
                    "enum": ["industry", "concept"],
                    "default": "industry",
                    "description": "板块类型",
                },
                "period": {
                    "type": "string",
                    "enum": ["today", "5d", "10d"],
                    "default": "today",
                    "description": "资金流统计周期",
                },
                "max_items": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 50,
                    "description": "返回的来源记录上限；保持来源响应顺序",
                },
            }
        ),
        executor=read_sector_flow_eastmoney,
        category="market",
    ),
)
__all__ = ["TOOLS", "read_sector_flow_eastmoney"]
