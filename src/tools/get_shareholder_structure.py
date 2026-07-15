"""``get_shareholder_structure`` tool."""

from typing import Any
from src.tools.symbols import resolve_symbol
from src.tools.base import ToolSpec, object_schema


def get_shareholder_structure(symbol: str) -> Any:
    from api.v1.endpoints.financials import get_shareholder_structure as endpoint
    return endpoint(symbol=resolve_symbol(symbol))


TOOL = ToolSpec(
    name="get_shareholder_structure",
    description="获取股东人数变化、前十大股东、机构持股、重要增减持和实际控制人。",
    parameters=object_schema({"symbol": {"type": "string", "description": "股票代码或名称"}}, ["symbol"]),
    executor=get_shareholder_structure,
    category="financials",
)
