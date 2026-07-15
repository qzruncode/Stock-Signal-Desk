"""``get_sector_flow`` tool."""

from typing import Any
from src.tools.base import ToolSpec, object_schema


def get_sector_flow(type: str = "industry", top_n: int = 10) -> Any:
    from api.v1.endpoints.macro import get_sector_flow as endpoint
    return endpoint(type=type, top_n=top_n)


TOOL = ToolSpec(
    name="get_sector_flow",
    description="获取行业或概念板块主力资金流入流出排名、涨跌幅、成交额和领涨股。",
    parameters=object_schema({
        "type": {"type": "string", "enum": ["industry", "concept"], "default": "industry", "description": "板块类型"},
        "top_n": {"type": "integer", "minimum": 3, "maximum": 30, "default": 10, "description": "流入和流出各返回数量"},
    }),
    executor=get_sector_flow,
    category="market",
)
