"""Business tool contracts over the independent data service."""

from __future__ import annotations
from typing import Any
from src.services.market_data_client import read_source
from src.tools.base import ToolSpec, object_schema


def read_bond_yield_eastmoney(
    country: str = "cn", term: str = "10y", days: int = 30, *, use_cache: bool = True
) -> dict[str, Any]:
    return read_source("get_bond_yield.read_bond_yield_eastmoney", locals())


def get_bond_yield(
    country: str = "cn", term: str = "10y", days: int = 30
) -> dict[str, Any]:
    return read_source("get_bond_yield.get_bond_yield", locals())


COUNTRIES = {"cn": "中国", "us": "美国"}
TERMS = {"2y": "2年", "5y": "5年", "10y": "10年", "30y": "30年"}
TOOLS = (
    ToolSpec(
        name="read_bond_yield_eastmoney",
        description="从东方财富读取中国或美国某一期限的国债收益率历史记录。只返回该期限的来源序列，不计算期限利差、不写本地库、也不在来源失败时读取本地缓存。",
        parameters=object_schema(
            {
                "country": {"type": "string", "enum": list(COUNTRIES), "default": "cn"},
                "term": {"type": "string", "enum": list(TERMS), "default": "10y"},
                "days": {
                    "type": "integer",
                    "minimum": 5,
                    "maximum": 250,
                    "default": 30,
                },
            }
        ),
        executor=read_bond_yield_eastmoney,
        category="macro",
    ),
)
__all__ = [
    "TOOLS",
    "get_bond_yield",
    "read_bond_yield_eastmoney",
]
