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
from typing import Any, get_args

from jsonschema import Draft202012Validator
from pydantic import BaseModel, TypeAdapter

from src.tools.base import ToolSpec, enforce_result_contract

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
        normalized[key] = value
    return normalized


def _bound_model_type(annotation: Any) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for candidate in get_args(annotation):
        model = _bound_model_type(candidate)
        if model is not None:
            return model
    return None

TOOL_MODULES: tuple[str, ...] = (
    # Local universe and user portfolio operations
    "search_stocks",
    "manage_watchlist",
    "manage_watchlist_groups",
    "filter_watchlist_by_theme",
    "get_data_health",
    # Persisted analysis workflows formerly exposed by Dashboard
    "run_stock_analysis",
    "get_analysis_status",
    "search_analysis_history",
    "read_analysis_report",
    "delete_analysis_history",
    "manage_analysis_templates",
    "run_batch_analysis",
    "manage_batch_run",
    "manage_analysis_schedule",
    "get_notification_status",
    "send_notification",
    # Quotes and price history
    "get_realtime_quotes",
    "get_kline",
    "get_history_data",
    "get_technical_indicators",
    "get_multi_stock_snapshot",
    "get_multi_stock_financials",
    "get_multi_stock_decision_evidence",
    "prepare_market_mainline_snapshot",
    "evaluate_market_mainline_gate",
    "evaluate_multi_stock_buy_criteria",
    "analyze_stock_catalysts",
    "get_domain_stock_candidates",
    "get_company_theme_evidence",
    "screen_atr_volatility_stocks",
    # Market and sector state
    "get_market_status",
    "get_market_breadth",
    "get_domain_board_catalog",
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
    "list_financial_sources",
    "inspect_financial_source",
    "read_financial_feed",
    "read_financial_article",
    "transform_webpage_to_feed",
    "export_financial_feed",
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

    def project_bound_argument(
        self,
        name: str,
        parameter: str,
        value: Any,
    ) -> Any:
        """Project a predecessor result into the consumer's typed field.

        Tool cards may add presentation metadata and producer result models may
        contain fields that the consuming resource intentionally does not
        accept. Only fields declared by the consumer model cross a Workflow
        binding boundary.
        """
        tool = self._tools.get(name)
        if tool is None or tool.args_model is None:
            raise KeyError(f"Tool not found: {name}")
        field = tool.args_model.model_fields.get(parameter)
        if field is None:
            raise ValueError(
                f"{name} has no bindable parameter {parameter}"
            )
        projected = value
        model_type = _bound_model_type(field.annotation)
        if isinstance(value, dict) and parameter in value:
            projected = value[parameter]
        elif model_type is not None and isinstance(value, dict):
            projected = {
                key: value[key]
                for key in model_type.model_fields
                if key in value
            }
        validated = TypeAdapter(field.annotation).validate_python(projected)
        if isinstance(validated, BaseModel):
            return validated.model_dump(
                mode="python",
                exclude_unset=True,
            )
        return validated

    def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Tool not found: {name}")
        # Direct registry callers keep the module-owned validation/result
        # contract.  The standard-task executor calls validate_arguments()
        # explicitly before execution so policy rejection happens before the
        # UI exposes a tool call.
        if not isinstance(arguments, dict):
            raise TypeError("tool arguments must be an object")
        normalized = (
            self.validate_arguments(name, arguments)
            if tool.args_model is not None
            else self.normalize_arguments(name, arguments)
        )
        payload = enforce_result_contract(name, tool.executor(**normalized))
        if tool.result_model is not None:
            return tool.result_model.model_validate(payload).model_dump(mode="python")
        return payload

    def project_guard_blocked_result(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Build a typed terminal result without launching the tool runner."""
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Tool not found: {name}")
        if tool.guard_blocked_result is None:
            raise ValueError(
                f"{name} has no result projector for a blocked execution guard"
            )
        normalized = self.validate_arguments(name, arguments)
        payload = enforce_result_contract(
            name,
            tool.guard_blocked_result(normalized),
        )
        if tool.result_model is not None:
            return tool.result_model.model_validate(payload).model_dump(mode="python")
        return payload


__all__ = ["TOOL_MODULES", "ToolDef", "ToolRegistry", "normalize_tool_arguments"]
