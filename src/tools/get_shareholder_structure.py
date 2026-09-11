"""Business tool contracts over the independent data service."""

from __future__ import annotations
from typing import Any
from src.services.market_data_client import read_source
from src.tools.base import ToolSpec, object_schema


def read_shareholder_f10_profile_eastmoney(
    symbol: str, *, use_cache: bool = True
) -> dict[str, Any]:
    return read_source(
        "get_shareholder_structure.read_shareholder_f10_profile_eastmoney", locals()
    )


def read_institutional_holdings_eastmoney(
    symbol: str, report_date: str, *, use_cache: bool = True
) -> dict[str, Any]:
    return read_source(
        "get_shareholder_structure.read_institutional_holdings_eastmoney", locals()
    )


def read_major_shareholder_changes_ths(
    symbol: str, *, use_cache: bool = True
) -> dict[str, Any]:
    return read_source(
        "get_shareholder_structure.read_major_shareholder_changes_ths", locals()
    )


TOOLS = (
    ToolSpec(
        name="read_shareholder_f10_profile_eastmoney",
        description="从东方财富 F10 的单次股东档案响应读取股东户数、前十大股东和实际控制人字段；不请求机构持仓明细、增减持或其他股权数据。",
        parameters=object_schema(
            {"symbol": {"type": "string", "description": "A股/北交所股票代码或名称"}},
            ["symbol"],
        ),
        executor=read_shareholder_f10_profile_eastmoney,
        category="financials",
    ),
    ToolSpec(
        name="read_institutional_holdings_eastmoney",
        description="从东方财富读取一只股票在一个明确披露期的机构持仓汇总。report_date 必须来自已取得的披露期信息；工具不会自动查找或选择报告期。",
        parameters=object_schema(
            {
                "symbol": {"type": "string", "description": "A股/北交所股票代码或名称"},
                "report_date": {
                    "type": "string",
                    "description": "已披露报告期，YYYY-MM-DD",
                },
            },
            ["symbol", "report_date"],
        ),
        executor=read_institutional_holdings_eastmoney,
        category="financials",
    ),
    ToolSpec(
        name="read_major_shareholder_changes_ths",
        description="从同花顺/AKShare读取一只股票的重要股东增减持记录，不加载 F10 股东档案或机构持仓。",
        parameters=object_schema(
            {"symbol": {"type": "string", "description": "A股/北交所股票代码或名称"}},
            ["symbol"],
        ),
        executor=read_major_shareholder_changes_ths,
        category="financials",
    ),
)
__all__ = [
    "TOOLS",
    "read_institutional_holdings_eastmoney",
    "read_major_shareholder_changes_ths",
    "read_shareholder_f10_profile_eastmoney",
]
