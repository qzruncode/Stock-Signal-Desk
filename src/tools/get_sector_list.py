# -*- coding: utf-8 -*-
"""``get_sector_list`` tool."""

from typing import Any
from src.tools.base import ToolSpec, object_schema


def get_sector_list(type: str = "industry") -> Any:
    from api.v1.endpoints.sectors import get_sector_list as endpoint
    result = endpoint(type=type)
    if not isinstance(result, dict):
        return result
    normalized = dict(result)
    normalized["success"] = bool(normalized.get("items"))
    normalized["partial"] = normalized["success"] and bool(normalized.get("errors"))
    normalized.setdefault("warnings", [])
    return normalized


TOOL = ToolSpec(
    name="get_sector_list",
    description="获取行业或概念板块列表及涨跌、领涨股和上涨下跌家数，用于板块强弱比较。",
    parameters=object_schema({
        "type": {"type": "string", "enum": ["industry", "concept"], "default": "industry", "description": "板块类型"},
    }),
    executor=get_sector_list,
    category="market",
)
