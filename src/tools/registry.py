# -*- coding: utf-8 -*-
"""Thin loader for module-owned Stock Agent tools.

There are deliberately no schemas, data calls, symbol rules or fallback rules
in this file.  A registered name ``foo`` must be defined by
``src.tools.foo.TOOL``.
"""

from __future__ import annotations

import importlib
import re
from collections import OrderedDict
from typing import Any

from src.tools.base import ToolSpec, enforce_result_contract

ToolDef = ToolSpec  # compatibility for existing API metadata imports


_ENUM_VALUE_ALIASES: dict[tuple[str, str], dict[str, Any]] = {
    ("get_sector_flow", "period"): {
        "今日": "today", "当天": "today", "1日": "today",
        "5日": "5d", "近5日": "5d", "五日": "5d",
        "10日": "10d", "近10日": "10d", "十日": "10d",
    },
    ("get_sector_flow", "type"): {
        "行业": "industry", "行业板块": "industry",
        "概念": "concept", "概念板块": "concept",
    },
}


def _snake_case(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()


def _coerce_schema_scalar(value: Any, schema: dict[str, Any]) -> Any:
    expected = schema.get("type")
    if expected == "boolean" and isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
    if expected == "integer" and isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        value = int(value)
    if expected == "number" and isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            pass
    if expected in {"integer", "number"} and isinstance(value, (int, float)) and not isinstance(value, bool):
        if schema.get("minimum") is not None:
            value = max(value, schema["minimum"])
        if schema.get("maximum") is not None:
            value = min(value, schema["maximum"])
        if expected == "integer":
            value = int(value)
    if expected == "string" and isinstance(value, str) and schema.get("enum"):
        by_lower = {str(item).lower(): item for item in schema["enum"]}
        value = by_lower.get(value.strip().lower(), value)
    if (
        expected == "string"
        and isinstance(value, str)
        and schema.get("pattern") == "^[0-9]{8}$"
    ):
        compact_date = re.sub(r"[^0-9]", "", value)
        if len(compact_date) == 8:
            value = compact_date
    return value


def normalize_tool_arguments(tool: ToolSpec, arguments: dict[str, Any]) -> dict[str, Any]:
    """Repair transport camelCase and scalar-string drift against the tool schema."""
    properties = tool.parameters.get("properties") or {}
    normalized: dict[str, Any] = {}
    for raw_key, value in arguments.items():
        key = raw_key if raw_key in properties else _snake_case(raw_key)
        # Only alias camelCase when it resolves to a declared parameter. Keep
        # unknown keys unchanged so the executor still raises a useful error.
        if key not in properties:
            key = raw_key
        value = _coerce_schema_scalar(value, properties.get(key) or {})
        aliases = _ENUM_VALUE_ALIASES.get((tool.name, key)) or {}
        if isinstance(value, str):
            value = aliases.get(value.strip(), aliases.get(value.strip().lower(), value))
        normalized[key] = value
    return normalized

TOOL_MODULES: tuple[str, ...] = (
    # Quotes and price history
    "get_realtime_quotes",
    "get_kline",
    "get_history_data",
    "get_technical_indicators",
    "get_multi_stock_snapshot",
    "get_multi_stock_decision_evidence",
    "get_theme_stock_candidates",
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
    "search_research_library",
    "get_regulatory_updates",
    "get_monetary_policy_operations",
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

    def normalize_arguments(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None:
            return dict(arguments)
        return normalize_tool_arguments(tool, arguments)

    def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Tool not found: {name}")
        if not isinstance(arguments, dict):
            raise TypeError("tool arguments must be an object")
        normalized = self.normalize_arguments(name, arguments)
        return enforce_result_contract(name, tool.executor(**normalized))


__all__ = ["TOOL_MODULES", "ToolDef", "ToolRegistry", "normalize_tool_arguments"]
