"""``get_balance_sheet`` tool."""

from src.tools._financial_statements import get_statement
from src.tools.base import ToolSpec, object_schema


def get_balance_sheet(symbol: str, periods: int = 4):
    return get_statement(symbol, "balance_sheet", periods)


TOOL = ToolSpec(
    name="get_balance_sheet",
    description=(
        "获取报告期末资产负债表，包含资产、负债、权益、现金、应收、存货、借款、杠杆与流动性；"
        "对银行额外返回存贷款和同业科目。金额单位统一为元。"
    ),
    parameters=object_schema(
        {
            "symbol": {"type": "string", "description": "股票代码或名称"},
            "periods": {"type": "integer", "minimum": 2, "maximum": 20, "default": 4, "description": "最近报告期数量"},
        },
        ["symbol"],
    ),
    executor=get_balance_sheet,
    category="financials",
)
