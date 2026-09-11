"""Business tool contracts over the independent data service."""

from __future__ import annotations
from typing import Any
from src.services.market_data_client import read_source
from src.tools.base import ToolSpec, object_schema


def read_consensus_metric_ths(symbol: str, metric: str) -> dict[str, Any]:
    return read_source("get_consensus_estimates.read_consensus_metric_ths", locals())


def read_consensus_institution_forecasts_ths(symbol: str) -> dict[str, Any]:
    return read_source(
        "get_consensus_estimates.read_consensus_institution_forecasts_ths", locals()
    )


def read_consensus_financial_estimates_ths(symbol: str) -> dict[str, Any]:
    return read_source(
        "get_consensus_estimates.read_consensus_financial_estimates_ths", locals()
    )


TOOLS = (
    ToolSpec(
        name="read_consensus_metric_ths",
        description="从同花顺/AKShare读取一只 A 股的一个一致预期指标。metric 必须明确选择 EPS 或净利润；不同时读取机构明细或其他指标。",
        parameters=object_schema(
            {
                "symbol": {"type": "string", "description": "股票代码或股票名称"},
                "metric": {
                    "type": "string",
                    "enum": ["eps", "net_profit"],
                    "description": "一致预期指标：eps 每股收益，net_profit 净利润",
                },
            },
            ["symbol", "metric"],
        ),
        executor=read_consensus_metric_ths,
        category="financials",
    ),
    ToolSpec(
        name="read_consensus_institution_forecasts_ths",
        description="从同花顺/AKShare读取一只 A 股的机构预测明细，不读取汇总指标或财务预测表。",
        parameters=object_schema(
            {"symbol": {"type": "string", "description": "股票代码或股票名称"}},
            ["symbol"],
        ),
        executor=read_consensus_institution_forecasts_ths,
        category="financials",
    ),
    ToolSpec(
        name="read_consensus_financial_estimates_ths",
        description="从同花顺/AKShare读取一只 A 股的财务预测明细，不读取机构列表或其他一致预期指标。",
        parameters=object_schema(
            {"symbol": {"type": "string", "description": "股票代码或股票名称"}},
            ["symbol"],
        ),
        executor=read_consensus_financial_estimates_ths,
        category="financials",
    ),
)
__all__ = [
    "TOOLS",
    "read_consensus_financial_estimates_ths",
    "read_consensus_institution_forecasts_ths",
    "read_consensus_metric_ths",
]
