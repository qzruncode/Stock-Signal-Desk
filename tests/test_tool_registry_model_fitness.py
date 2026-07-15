# -*- coding: utf-8 -*-
"""Tests for tool-registry defaults and model-facing symbol resolution."""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from src.tools.registry import ToolRegistry


def _install_stub_module(module_name: str, **functions):
    module = types.ModuleType(module_name)
    for name, fn in functions.items():
        setattr(module, name, fn)
    sys.modules[module_name] = module
    return module


class ToolRegistryModelFitnessTestCase(unittest.TestCase):
    def test_each_registered_tool_has_same_named_module(self) -> None:
        tools_dir = Path(__file__).parents[1] / "src" / "tools"
        missing = [
            name
            for name in ToolRegistry().get_tool_names()
            if not (tools_dir / f"{name}.py").is_file()
        ]
        self.assertEqual(missing, [])

    def test_get_stock_info_resolves_name_before_endpoint_call(self) -> None:
        calls: list[str] = []

        def fake_get_stock_info(symbol: str):
            calls.append(symbol)
            return {"symbol": symbol}

        _install_stub_module("api.v1.endpoints.stock_info", get_stock_info=fake_get_stock_info)
        registry = ToolRegistry()

        with patch("src.services.name_to_code_resolver.resolve_name_to_code", return_value="600519"):
            result = registry.execute("get_stock_info", {"symbol": "贵州茅台"})

        self.assertEqual(calls, ["600519"])
        self.assertEqual(result["symbol"], "600519")

    def test_realtime_quotes_resolves_each_symbol_in_csv(self) -> None:
        calls: list[list[str]] = []

        def fake_get_realtime_quotes(symbols: list[str]):
            calls.append(list(symbols))
            return {"symbols": list(symbols)}

        _install_stub_module(
            "src.tools.get_realtime_quotes",
            get_realtime_quotes=fake_get_realtime_quotes,
            REALTIME_QUOTES_DESCRIPTION="stub",
        )
        registry = ToolRegistry()

        def resolver(value: str) -> str:
            return {"贵州茅台": "600519", "宁德时代": "300750"}.get(value, value)

        with patch("src.services.name_to_code_resolver.resolve_name_to_code", side_effect=resolver):
            result = registry.execute("get_realtime_quotes", {"symbols": "贵州茅台,宁德时代"})

        self.assertEqual(calls, [["600519", "300750"]])
        self.assertEqual(result["symbols"], ["600519", "300750"])

    def test_schema_defaults_are_tightened_for_heavy_tools(self) -> None:
        registry = ToolRegistry()
        schemas = {
            item["function"]["name"]: item["function"]["parameters"]
            for item in registry.get_all_schemas()
        }

        self.assertEqual(schemas["get_kline"]["properties"]["count"]["default"], 60)
        self.assertEqual(schemas["get_financials"]["properties"]["periods"]["default"], 6)
        self.assertEqual(schemas["get_balance_sheet"]["properties"]["periods"]["default"], 4)
        self.assertEqual(schemas["search_news"]["properties"]["days"]["default"], 30)
        self.assertEqual(schemas["get_research_report"]["properties"]["days"]["default"], 365)

    def test_removed_search_fallback_tools_are_not_registered(self) -> None:
        registry = ToolRegistry()
        names = set(registry.get_tool_names())

        self.assertNotIn("search_web_news", names)
        self.assertNotIn("search_web_price_fallback", names)
        self.assertNotIn("fetch_web_content", names)

    def test_llm_dependent_tools_are_not_registered(self) -> None:
        registry = ToolRegistry()
        names = set(registry.get_tool_names())

        self.assertNotIn("get_market_mainline_report", names)
        self.assertNotIn("get_stock_business", names)
        self.assertNotIn("get_buy_criteria_analysis", names)

    def test_redundant_derived_tools_are_not_registered(self) -> None:
        registry = ToolRegistry()
        names = set(registry.get_tool_names())

        self.assertNotIn("get_price_overdraft_signal", names)
        self.assertNotIn("get_sentiment", names)

    def test_risk_events_tool_is_registered(self) -> None:
        registry = ToolRegistry()
        names = set(registry.get_tool_names())

        self.assertIn("get_risk_events", names)


if __name__ == "__main__":
    unittest.main()
