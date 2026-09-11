"""Business tool contracts over the independent data service."""

from __future__ import annotations
from typing import Any
from src.services.market_data_client import read_source
from src.tools.base import ToolSpec, object_schema


def read_company_profile_cninfo(
    symbol: str, *, use_cache: bool = True
) -> dict[str, Any]:
    return read_source("get_stock_info.read_company_profile_cninfo", locals())


def read_stock_capital_snapshot_eastmoney(
    symbol: str, *, use_cache: bool = True
) -> dict[str, Any]:
    return read_source("get_stock_info.read_stock_capital_snapshot_eastmoney", locals())


TOOLS = (
    ToolSpec(
        name="read_company_profile_cninfo",
        description="从巨潮资讯读取一只 A 股的公司基础档案：公司名称、行业、成立和上市日期、主营业务、联系方式和注册地址；不读取实时价格、市值或股本。",
        parameters=object_schema(
            {
                "symbol": {
                    "type": "string",
                    "description": "A股股票代码或可解析的股票名称",
                }
            },
            ["symbol"],
        ),
        executor=read_company_profile_cninfo,
        category="data",
        web_fallback=True,
    ),
    ToolSpec(
        name="read_stock_capital_snapshot_eastmoney",
        description="读取一只 A 股的当前价格、总/流通股本和总/流通市值快照；优先使用东方财富证券快照，连接失败时只切换到其他实时行情接口，不把旧缓存作为失败时的备用结果，并明确返回实际来源和缺失字段；不读取公司档案或估值历史。",
        parameters=object_schema(
            {
                "symbol": {
                    "type": "string",
                    "description": "A股股票代码或可解析的股票名称",
                }
            },
            ["symbol"],
        ),
        executor=read_stock_capital_snapshot_eastmoney,
        category="data",
        web_fallback=True,
    ),
)
__all__ = [
    "TOOLS",
    "read_company_profile_cninfo",
    "read_stock_capital_snapshot_eastmoney",
]
