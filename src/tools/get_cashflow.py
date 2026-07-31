"""``get_cashflow`` tool."""

from src.tools._financial_statements import get_statement
from src.tools.base import ToolSpec, object_schema


def get_cashflow(symbol: str, periods: int = 4):
    return get_statement(symbol, "cashflow", periods)


TOOL = ToolSpec(
    name="get_cashflow",
    description=(
        "获取单季度现金流量表，包括经营、投资、筹资现金流、资本开支、自由现金流和现金含金量；" "金额单位统一为元。"
    ),
    parameters=object_schema(
        {
            "symbol": {"type": "string", "description": "股票代码或名称"},
            "periods": {"type": "integer", "minimum": 2, "maximum": 20, "default": 4, "description": "最近单季度数量"},
        },
        ["symbol"],
    ),
    executor=get_cashflow,
    category="financials",
)
