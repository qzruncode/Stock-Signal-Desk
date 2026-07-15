"""``get_index_data`` tool."""

from typing import Any
from src.tools.base import ToolSpec, object_schema


def get_index_data(index_code: str = "000001", days: int = 20) -> Any:
    from api.v1.endpoints.macro import get_index_data as endpoint
    return endpoint(index_code=index_code, days=days)


TOOL = ToolSpec(
    name="get_index_data",
    description="获取上证、深证、创业板或科创 50 的日线行情与最近表现。",
    parameters=object_schema({
        "index_code": {"type": "string", "enum": ["000001", "399001", "399006", "000688"], "default": "000001", "description": "指数代码"},
        "days": {"type": "integer", "minimum": 5, "maximum": 250, "default": 20, "description": "最近交易日数量"},
    }),
    executor=get_index_data,
    category="macro",
)
