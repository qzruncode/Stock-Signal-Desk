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

from jsonschema import Draft202012Validator
from pydantic import BaseModel

from src.tools.base import (
    ToolSpec,
    current_tool_effect_approval,
    enforce_result_contract,
)

ToolDef = ToolSpec  # compatibility for existing API metadata imports


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
    if expected == "string" and isinstance(value, str) and schema.get("enum"):
        by_lower = {str(item).lower(): item for item in schema["enum"]}
        value = by_lower.get(value.strip().lower(), value)
    if expected == "string" and isinstance(value, str) and schema.get("pattern") == "^[0-9]{8}$":
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
        normalized[key] = value
    return normalized


TOOL_MODULES: tuple[str, ...] = (
    # Local universe and user portfolio operations
    "search_stocks",
    "manage_watchlist",
    "manage_watchlist_groups",
    "get_data_health",
    # Persisted records and single external operations
    "get_analysis_status",
    "search_analysis_history",
    "read_analysis_report",
    "delete_analysis_history",
    "manage_analysis_templates",
    "manage_batch_run",
    "manage_analysis_schedule",
    "get_notification_status",
    "send_notification",
    # Quotes and price history
    "get_realtime_quotes",
    "get_kline",
    "get_history_data",
    "get_technical_indicators",
    "get_multi_stock_financials",
    # Market and sector state
    "get_market_status",
    "get_market_breadth",
    "get_domain_board_catalog",
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
    "discover_rss_sources",
    "inspect_rss_source",
    "read_rss_feed",
    "read_rss_item",
    "read_text_document",
    "export_rss_feed",
    "transform_webpage_to_feed",
    "get_announcements",
    "get_research_report",
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
                raise ValueError(f"tool/module name mismatch: module={module_name}, tool={tool.name}")
            if tool.name in self._tools:
                raise ValueError(f"duplicate tool: {tool.name}")
            self._tools[tool.name] = tool

    def get_all_schemas(
        self,
        *,
        include_server_controlled: bool = False,
    ) -> list[dict[str, Any]]:
        return [
            tool.to_openai_schema(
                include_server_controlled=include_server_controlled,
            )
            for tool in self._tools.values()
        ]

    def get_tool_names(self) -> list[str]:
        return list(self._tools)

    def get_tool(self, name: str) -> ToolSpec | None:
        """Return one immutable tool definition for runtime policy validation."""
        return self._tools.get(name)

    def normalize_arguments(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None:
            return dict(arguments)
        return normalize_tool_arguments(tool, arguments)

    def validate_arguments(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Normalize and validate arguments before a tool enters the executor."""
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Tool not found: {name}")
        if not isinstance(arguments, dict):
            raise TypeError("tool arguments must be an object")
        normalized = normalize_tool_arguments(tool, arguments)
        if tool.args_model is not None:
            return tool.args_model.model_validate(normalized).model_dump(
                mode="python",
                exclude_unset=True,
            )
        errors = sorted(
            Draft202012Validator(tool.parameters).iter_errors(normalized),
            key=lambda error: list(error.absolute_path),
        )
        if errors:
            details = "; ".join(
                f"{'.'.join(str(item) for item in error.absolute_path) or '<root>'}: {error.message}"
                for error in errors[:5]
            )
            raise ValueError(f"invalid arguments for {name}: {details}")
        return normalized

    def validate_model_arguments(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        approved: bool = False,
    ) -> dict[str, Any]:
        """Validate model-authored arguments under server-owned controls."""
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Tool not found: {name}")
        if not isinstance(arguments, dict):
            raise TypeError("tool arguments must be an object")
        controlled = set(tool.server_controlled_fields)
        supplied = sorted(controlled.intersection(arguments))
        if supplied:
            raise ValueError(
                f"model cannot set server-controlled fields for {name}: "
                + ", ".join(supplied)
            )
        prepared = dict(arguments)
        declared = set((tool.parameters or {}).get("properties") or {})
        for field_name in controlled.intersection(declared):
            prepared[field_name] = bool(approved)
        return self.validate_arguments(name, prepared)

    def effect_for(self, name: str, arguments: dict[str, Any]) -> str:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Tool not found: {name}")
        uncontrolled = {
            key: value
            for key, value in arguments.items()
            if key not in set(tool.server_controlled_fields)
        }
        return tool.effect_for(uncontrolled)

    def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Tool not found: {name}")
        # The graph validates model-authored fields before dispatch; this
        # boundary repeats the module-owned schema and result checks.
        if not isinstance(arguments, dict):
            raise TypeError("tool arguments must be an object")
        effect_arguments = {
            key: value
            for key, value in arguments.items()
            if key not in set(tool.server_controlled_fields)
        }
        if (
            tool.effect_for(effect_arguments) == "side_effect"
            and not current_tool_effect_approval()
        ):
            raise PermissionError(
                f"side-effect tool {name} can only run after a server-approved interrupt"
            )
        normalized = (
            self.validate_arguments(name, arguments)
            if tool.args_model is not None
            else self.normalize_arguments(name, arguments)
        )
        payload = enforce_result_contract(name, tool.executor(**normalized))
        if tool.result_model is not None:
            return tool.result_model.model_validate(payload).model_dump(mode="python")
        return payload

__all__ = ["TOOL_MODULES", "ToolDef", "ToolRegistry", "normalize_tool_arguments"]
