"""``get_valuation_ratios`` tool."""

from typing import Any
from src.tools.symbols import resolve_symbol
from src.tools.base import ToolSpec, object_schema


def get_valuation_ratios(symbol: str, with_history: bool = True) -> Any:
    from api.v1.endpoints.financials import get_valuation_ratios as endpoint
    return endpoint(symbol=resolve_symbol(symbol), with_history=with_history)


TOOL = ToolSpec(
    name="get_valuation_ratios",
    description="获取 PE/PB/PS/PCF/PEG、股息率、历史分位、行业均值及预期校准后的估值透支信号。",
    parameters=object_schema({
        "symbol": {"type": "string", "description": "股票代码或名称"},
        "with_history": {"type": "boolean", "default": True, "description": "是否包含历史估值分位"},
    }, ["symbol"]),
    executor=get_valuation_ratios,
    category="financials",
)
