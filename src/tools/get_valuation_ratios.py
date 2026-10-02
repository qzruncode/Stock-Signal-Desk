"""Business tool contracts over the independent data service."""

from __future__ import annotations
from typing import Any
from src.services.market_data_client import read_source
from src.tools.base import ToolSpec, object_schema


def read_valuation_history_eastmoney(
    symbol: str, days: int = 250, *, use_cache: bool = True
) -> dict[str, Any]:
    return read_source(
        "get_valuation_ratios.read_valuation_history_eastmoney", locals()
    )


def read_valuation_quote_eastmoney(
    symbol: str, *, use_cache: bool = True
) -> dict[str, Any]:
    return read_source("get_valuation_ratios.read_valuation_quote_eastmoney", locals())


def read_peer_valuation_eastmoney(
    symbol: str, *, use_cache: bool = True
) -> dict[str, Any]:
    return read_source("get_valuation_ratios.read_peer_valuation_eastmoney", locals())


def read_dividend_history_eastmoney(
    symbol: str, limit: int = 100, *, use_cache: bool = True
) -> dict[str, Any]:
    return read_source("get_valuation_ratios.read_dividend_history_eastmoney", locals())


TOOLS = (
    ToolSpec(
        name="read_valuation_history_eastmoney",
        description="从东方财富（AKShare）读取一只 A 股的日度估值历史，包括价格、PE、PB、PS、PCF、PEG、市值和股本；不混合实时快照、同行比较或分红，也不计算估值分位。",
        parameters=object_schema(
            {
                "symbol": {
                    "type": "string",
                    "description": "A股股票代码或可解析的股票名称",
                },
                "days": {
                    "type": "integer",
                    "minimum": 5,
                    "maximum": 1825,
                    "default": 250,
                },
            },
            ["symbol"],
        ),
        executor=read_valuation_history_eastmoney,
        category="financials",
    ),
    ToolSpec(
        name="read_valuation_quote_eastmoney",
        description="从东方财富读取一只 A 股的当前价格、PE（TTM/静态/动态）、PB（年报）和市值快照；正常不查询历史估值、同行比较或分红；若实时快照缺少 quote_time 或请求失败，按结果合同降级到最近一条带日期估值历史，并明确标注为有日期快照而非实时行情。",
        parameters=object_schema(
            {
                "symbol": {
                    "type": "string",
                    "description": "A股股票代码或可解析的股票名称",
                }
            },
            ["symbol"],
        ),
        executor=read_valuation_quote_eastmoney,
        category="financials",
    ),
    ToolSpec(
        name="read_peer_valuation_eastmoney",
        description="从东方财富读取一只 A 股所在同行估值比较表的原始指标、样本数、排名和行业均值/中值；不把排名或均值自动解释成高估、低估或投资结论。",
        parameters=object_schema(
            {
                "symbol": {
                    "type": "string",
                    "description": "A股股票代码或可解析的股票名称",
                }
            },
            ["symbol"],
        ),
        executor=read_peer_valuation_eastmoney,
        category="financials",
    ),
    ToolSpec(
        name="read_dividend_history_eastmoney",
        description="从东方财富（AKShare）读取一只 A 股的分红方案和实施记录；返回报告期、除权除息日和每10股现金分红，不计算股息率或 TTM 汇总。",
        parameters=object_schema(
            {
                "symbol": {
                    "type": "string",
                    "description": "A股股票代码或可解析的股票名称",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "default": 100,
                },
            },
            ["symbol"],
        ),
        executor=read_dividend_history_eastmoney,
        category="financials",
    ),
)
__all__ = [
    "TOOLS",
    "read_dividend_history_eastmoney",
    "read_peer_valuation_eastmoney",
    "read_valuation_history_eastmoney",
    "read_valuation_quote_eastmoney",
]
