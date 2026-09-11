# -*- coding: utf-8 -*-
"""Thin loader for module-owned, model-callable atomic operations.

Source adapters remain ordinary modules but no longer become one tool each.
``source_operations`` exposes a small operation surface with a complete
source directory and explicit ``source_id`` values, so the model can select a
source without receiving dozens of near-identical function schemas.
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

def _snake_case(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()


def _coerce_schema_scalar(value: Any, schema: dict[str, Any]) -> Any:
    scalar_schema = schema
    if not scalar_schema.get("type"):
        choices = scalar_schema.get("anyOf") or scalar_schema.get("oneOf") or []
        non_null = [
            choice
            for choice in choices
            if isinstance(choice, dict) and choice.get("type") != "null"
        ]
        if len(non_null) == 1:
            scalar_schema = {**schema, **non_null[0]}
    expected = scalar_schema.get("type")
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
    if expected == "string" and isinstance(value, str) and scalar_schema.get("enum"):
        by_lower = {str(item).lower(): item for item in scalar_schema["enum"]}
        value = by_lower.get(value.strip().lower(), value)
    if (
        expected == "string"
        and isinstance(value, str)
        and scalar_schema.get("pattern") == "^[0-9]{8}$"
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
        normalized[key] = value
    return normalized


TOOL_MODULES: tuple[str, ...] = (
    # Local universe and user portfolio operations
    "search_stocks",
    "manage_watchlist",
    "manage_watchlist_groups",
    # Persisted records and single external operations
    "search_analysis_history",
    "read_analysis_report",
    "delete_analysis_history",
    "get_notification_status",
    "send_notification",
    # Quotes and price history
    "source_operations",
    "get_multi_stock_financials",
    # Market and sector state
    "market_snapshot_tools",
    "get_sector_flow",
    "get_stock_capital_flow",
    # Deterministic all-market screening
    "screen_atr_volatility_stocks",
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
    "read_rss_item",
    "read_text_document",
    "get_announcements",
    "get_research_report",
    # Market context
    "get_index_data",
    "get_bond_yield",
    "get_macro_indicator",
    # Explicit single-provider public web access
)


class ToolRegistry:
    """Discover tool definitions and execute them by registered name."""

    @classmethod
    def from_tools(cls, tools: list[ToolSpec]) -> "ToolRegistry":
        """Explicit dependency injection for offline experiments and embeddings.

        Injected operations cannot be reconstructed by isolated production
        workers; supports_isolated_execution intentionally returns False.
        """
        registry = cls.__new__(cls)
        registry._tools = OrderedDict()
        registry._owners = {}
        for tool in tools:
            if tool.name in registry._tools:
                raise ValueError(f"duplicate tool: {tool.name}")
            registry._tools[tool.name] = tool
            registry._owners[tool.name] = "injected"
        return registry

    def __init__(self) -> None:
        self._tools: OrderedDict[str, ToolSpec] = OrderedDict()
        self._owners: dict[str, str] = {}
        for module_name in TOOL_MODULES:
            module = importlib.import_module(f"src.tools.{module_name}")
            direct = getattr(module, "TOOL", None)
            collection = getattr(module, "TOOLS", None)
            if isinstance(direct, ToolSpec) and collection is not None:
                raise TypeError(
                    f"src.tools.{module_name} cannot export both TOOL and TOOLS"
                )
            if isinstance(direct, ToolSpec):
                tools = (direct,)
                if direct.name != module_name:
                    raise ValueError(
                        "tool/module name mismatch: "
                        f"module={module_name}, tool={direct.name}"
                    )
            elif isinstance(collection, (tuple, list)) and collection:
                tools = tuple(collection)
                if any(not isinstance(tool, ToolSpec) for tool in tools):
                    raise TypeError(
                        f"src.tools.{module_name}.TOOLS must contain only ToolSpec"
                    )
            else:
                raise TypeError(
                    f"src.tools.{module_name} must export TOOL or non-empty TOOLS"
                )
            for tool in tools:
                executor_module = str(getattr(tool.executor, "__module__", "") or "")
                if executor_module != module.__name__:
                    raise TypeError(
                        "tool executor must be defined by its owning module: "
                        f"tool={tool.name}, owner={module.__name__}, executor={executor_module or '<unknown>'}"
                    )
                if tool.name in self._tools:
                    raise ValueError(f"duplicate tool: {tool.name}")
                self._tools[tool.name] = tool
                self._owners[tool.name] = module_name

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

    def get_tool_owner_module(self, name: str) -> str | None:
        """Return the module that owns one direct or generated ToolSpec."""
        return getattr(self, "_owners", {}).get(name)

    def supports_isolated_execution(self, name: str) -> bool:
        """Whether the one-shot worker can reconstruct this tool by name.

        The isolated worker deliberately creates a fresh ``ToolRegistry`` in a
        separate process. It can therefore run only module-owned tools from
        this application's static registry, not an in-memory registry injected
        by a caller (for example, a test double or an embedding application).
        """
        return bool(
            name in self._tools
            and getattr(self, "_owners", {}).get(name) in TOOL_MODULES
        )

    def get_module_names(self) -> list[str]:
        """Return the deterministic module loading order."""
        return list(TOOL_MODULES)

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

__all__ = ["TOOL_MODULES", "ToolRegistry", "normalize_tool_arguments"]
