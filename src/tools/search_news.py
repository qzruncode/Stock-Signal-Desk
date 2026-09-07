"""Business tool contracts over the independent data service."""

from __future__ import annotations
from typing import Any
from src.services.market_data_client import read_source
from src.tools.base import ToolSpec, object_schema


def search_news(
    symbol: str, days: int = 30, limit: int = 20, use_cache: bool = True
) -> dict[str, Any]:
    return read_source("news.search_news", locals())


def read_company_news_akshare(
    symbol: str, days: int = 30, limit: int = 20, use_cache: bool = True
) -> dict[str, Any]:
    return read_source("news.read_company_news_akshare", locals())


DESCRIPTION = "从 AKShare 的 stock_news_em 单一来源读取一只 A 股/北交所公司的相关新闻，按公司名称或代码校验主体、去重并限制返回量；不会调用 RSSHub 或其他新闻来源。这是 reference-only 来源索引，不包含新闻正文；若要用新闻内容支撑实质性结论，必须继续调用 read_web_source 读取对应 URL。"
TOOLS = (
    ToolSpec(
        name="read_company_news_akshare",
        description=DESCRIPTION,
        parameters=object_schema(
            {
                "symbol": {
                    "type": "string",
                    "description": "A股/北交所股票代码或公司名称",
                },
                "days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 365,
                    "default": 30,
                    "description": "最近天数",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 20,
                    "description": "最多返回条数",
                },
                "use_cache": {
                    "type": "boolean",
                    "default": True,
                    "description": "是否使用半小时缓存；需要强制刷新时设为 false",
                },
            },
            ["symbol"],
        ),
        executor=read_company_news_akshare,
        category="news_source",
    ),
)
__all__ = ["TOOLS", "read_company_news_akshare", "search_news"]
