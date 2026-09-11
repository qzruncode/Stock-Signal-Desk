"""Business tool contracts over the independent data service."""

from __future__ import annotations
from typing import Any
from src.services.market_data_client import read_source
from src.market_index_catalog import A_SHARE_INDEX_MAP
from src.tools.base import ToolSpec, object_schema


def read_index_daily_history_sina(
    index_code: str = "000001", days: int = 20, *, use_cache: bool = True
) -> dict[str, Any]:
    return read_source("get_index_data.read_index_daily_history_sina", locals())


def read_index_quote_sina(
    index_code: str = "000001", *, use_cache: bool = True
) -> dict[str, Any]:
    return read_source("get_index_data.read_index_quote_sina", locals())


INDEX_MAP = A_SHARE_INDEX_MAP
TOOLS = (
    ToolSpec(
        name="read_index_daily_history_sina",
        description="从新浪（AKShare）读取指定中国主要指数的日线历史；返回日线 OHLC、成交量和成交额，不合并当前盘中快照或写入本地宏观缓存。",
        parameters=object_schema(
            {
                "index_code": {
                    "type": "string",
                    "enum": list(INDEX_MAP),
                    "default": "000001",
                },
                "days": {
                    "type": "integer",
                    "minimum": 5,
                    "maximum": 250,
                    "default": 20,
                },
            }
        ),
        executor=read_index_daily_history_sina,
        category="macro",
    ),
    ToolSpec(
        name="read_index_quote_sina",
        description="从新浪（AKShare）读取指定中国主要指数的当前行情快照；不获取日线，也不把快照拼接进历史序列。",
        parameters=object_schema(
            {
                "index_code": {
                    "type": "string",
                    "enum": list(INDEX_MAP),
                    "default": "000001",
                }
            }
        ),
        executor=read_index_quote_sina,
        category="macro",
    ),
)
__all__ = [
    "TOOLS",
    "read_index_daily_history_sina",
    "read_index_quote_sina",
]
