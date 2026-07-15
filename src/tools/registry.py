# -*- coding: utf-8 -*-
"""Thin loader for module-owned Stock Agent tools.

There are deliberately no schemas, data calls, symbol rules or fallback rules
in this file.  A registered name ``foo`` must be defined by
``src.tools.foo.TOOL``.
"""

from __future__ import annotations

import importlib
from collections import OrderedDict
from typing import Any

from src.tools.base import ToolSpec

ToolDef = ToolSpec  # compatibility for existing API metadata imports

TOOL_MODULES: tuple[str, ...] = (
    # Quotes and price history
    "get_realtime_quotes",
    "get_kline",
    "get_history_data",
    "get_technical_indicators",
    # Market and sector state
    "get_market_status",
    "get_market_breadth",
    "get_sector_list",
    "get_sector_flow",
    "get_stock_capital_flow",
    # Company and financial fundamentals
    "get_stock_info",
    "get_financials",
    "get_balance_sheet",
    "get_income_statement",
    "get_cashflow",
    "get_business_segments",
    "get_valuation_ratios",
    "get_consensus_estimates",
    "get_peer_comparison",
    "get_shareholder_structure",
    # Information and event evidence
    "search_news",
    "search_financial_news",
    "get_announcements",
    "get_risk_events",
    "get_research_report",
    "get_social_sentiment",
    # Market context
    "get_index_data",
    "get_bond_yield",
    "get_macro_indicator",
    # Last-resort public web access
    "websearch",
    "webfetch",
)


class ToolRegistry:
    """Discover tool definitions and execute them by registered name."""

    def __init__(self) -> None:
        self._tools: OrderedDict[str, ToolSpec] = OrderedDict()
        for module_name in TOOL_MODULES:
            module = importlib.import_module(f"src.tools.{module_name}")
            tool = getattr(module, "TOOL", None)
            if not isinstance(tool, ToolSpec):
                raise TypeError(f"src.tools.{module_name} must export TOOL: ToolSpec")
            if tool.name != module_name:
                raise ValueError(
                    f"tool/module name mismatch: module={module_name}, tool={tool.name}"
                )
            if tool.name in self._tools:
                raise ValueError(f"duplicate tool: {tool.name}")
            self._tools[tool.name] = tool

    def get_all_schemas(self) -> list[dict[str, Any]]:
        return [tool.to_openai_schema() for tool in self._tools.values()]

    def get_tool_names(self) -> list[str]:
        return list(self._tools)

    def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Tool not found: {name}")
        if not isinstance(arguments, dict):
            raise TypeError("tool arguments must be an object")
        return tool.executor(**arguments)


__all__ = ["TOOL_MODULES", "ToolDef", "ToolRegistry"]
