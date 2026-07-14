# -*- coding: utf-8 -*-
"""独立 LLM 工具集合。

每个注册 tool 对应一个同名 Python 模块，例如 ``get_market_status`` 对应
``get_market_status.py``。以下划线开头的模块仅承载多个 tool 共用的内部实现。
FastAPI 路由层只做 HTTP 参数适配，真正逻辑从这里调用。
"""

from src.tools._kline import fetch_and_persist_kline
from src.tools.get_history_data import KLINE_HISTORY_DESCRIPTION, get_history_data
from src.tools.get_kline import KLINE_DESCRIPTION, get_kline
from src.tools.get_realtime_quotes import REALTIME_QUOTES_DESCRIPTION, get_realtime_quotes
from src.tools.registry import ToolDef, ToolRegistry
from src.tools.webfetch import WEBFETCH_DESCRIPTION, fetch_url
from src.tools.websearch import WEBSEARCH_DESCRIPTION, websearch

__all__ = [
    "KLINE_DESCRIPTION",
    "KLINE_HISTORY_DESCRIPTION",
    "REALTIME_QUOTES_DESCRIPTION",
    "ToolDef",
    "ToolRegistry",
    "WEBFETCH_DESCRIPTION",
    "WEBSEARCH_DESCRIPTION",
    "fetch_and_persist_kline",
    "fetch_url",
    "get_history_data",
    "get_kline",
    "get_realtime_quotes",
    "websearch",
]
