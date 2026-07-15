"""``get_bond_yield`` tool."""

from typing import Any
from src.tools.base import ToolSpec, object_schema


def get_bond_yield(country: str = "cn", term: str = "10y") -> Any:
    from api.v1.endpoints.macro import get_bond_yield as endpoint
    return endpoint(country=country, term=term)


TOOL = ToolSpec(
    name="get_bond_yield",
    description="获取中美国债收益率、近月走势和期限利差，用于无风险利率与流动性环境判断。",
    parameters=object_schema({
        "country": {"type": "string", "enum": ["cn", "us"], "default": "cn", "description": "国家"},
        "term": {"type": "string", "enum": ["2y", "5y", "10y", "30y"], "default": "10y", "description": "期限"},
    }),
    executor=get_bond_yield,
    category="macro",
)
