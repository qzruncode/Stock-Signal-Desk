"""``get_income_statement`` tool."""

from src.tools._financial_statements import get_statement
from src.tools.base import ToolSpec, object_schema


def get_income_statement(symbol: str, periods: int = 4):
    return get_statement(symbol, "income_statement", periods, local_identity=True)


TOOL = ToolSpec(
    name="get_income_statement",
    description=(
        "获取单季度利润表，包含收入、成本、营业利润、归母及扣非净利润、EPS、费用和利润率；"
        "银行返回净利息收入等专属科目，不把累计值冒充单季度值。"
    ),
    parameters=object_schema(
        {
            "symbol": {"type": "string", "description": "股票代码或名称"},
            "periods": {"type": "integer", "minimum": 2, "maximum": 20, "default": 4, "description": "最近单季度数量"},
        },
        ["symbol"],
    ),
    executor=get_income_statement,
    category="financials",
)
