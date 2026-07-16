# -*- coding: utf-8 -*-
"""Tests for tool-registry defaults and model-facing symbol resolution."""

from __future__ import annotations

import unittest
import inspect
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from src.tools.registry import ToolRegistry


class ToolRegistryModelFitnessTestCase(unittest.TestCase):
    def test_registry_has_exactly_33_unique_module_owned_tools(self) -> None:
        registry = ToolRegistry()
        names = registry.get_tool_names()

        self.assertEqual(len(names), 33)
        self.assertEqual(len(names), len(set(names)))

    def test_each_registered_tool_has_same_named_module(self) -> None:
        tools_dir = Path(__file__).parents[1] / "src" / "tools"
        missing = [
            name
            for name in ToolRegistry().get_tool_names()
            if not (tools_dir / f"{name}.py").is_file()
        ]
        self.assertEqual(missing, [])

    def test_every_schema_property_is_accepted_by_its_executor(self) -> None:
        """Prevent model-visible camel/snake-case drift from failing at runtime."""
        registry = ToolRegistry()
        for tool in registry._tools.values():
            signature = inspect.signature(tool.executor)
            accepts_kwargs = any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()
            )
            if accepts_kwargs:
                continue
            unsupported = set(tool.parameters["properties"]) - set(signature.parameters)
            self.assertEqual(unsupported, set(), msg=f"{tool.name}: {sorted(unsupported)}")

    def test_all_tool_schemas_are_closed_and_described(self) -> None:
        for schema in ToolRegistry().get_all_schemas():
            function = schema["function"]
            parameters = function["parameters"]
            self.assertTrue(function["description"].strip(), msg=function["name"])
            self.assertEqual(parameters["type"], "object", msg=function["name"])
            self.assertIs(parameters["additionalProperties"], False, msg=function["name"])

    def test_get_stock_info_resolves_name_before_data_calls(self) -> None:
        registry = ToolRegistry()

        with patch("src.services.name_to_code_resolver.resolve_name_to_code", return_value="600519"), \
             patch("src.tools.get_stock_info._fetch_cninfo", return_value={"A股简称": "贵州茅台"}), \
             patch("src.tools.get_stock_info._fetch_eastmoney_capital", return_value={"symbol": "600519", "market_code": "sh"}):
            result = registry.execute("get_stock_info", {"symbol": "贵州茅台"})

        self.assertEqual(result["symbol"], "600519")
        self.assertEqual(result["short_name"], "贵州茅台")

    def test_realtime_quotes_resolves_each_symbol_in_csv(self) -> None:
        calls: list[list[str]] = []

        def fake_get_realtime_quotes(symbols: list[str]):
            calls.append(list(symbols))
            return {"symbols": list(symbols)}

        registry = ToolRegistry()

        def resolver(value: str) -> str:
            return {"贵州茅台": "600519", "宁德时代": "300750"}.get(value, value)

        with patch("src.tools.get_realtime_quotes.get_realtime_quotes", side_effect=fake_get_realtime_quotes), \
             patch("src.services.name_to_code_resolver.resolve_name_to_code", side_effect=resolver):
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

    def test_rss_is_exposed_as_direct_business_tools_without_catalog_hops(self) -> None:
        names = set(ToolRegistry().get_tool_names())

        self.assertIn("search_financial_news", names)
        self.assertIn("search_research_library", names)
        self.assertIn("get_regulatory_updates", names)
        self.assertIn("get_monetary_policy_operations", names)
        self.assertNotIn("list_rss_sources", names)
        self.assertNotIn("read_rss_feed", names)
        self.assertNotIn("read_rss_item", names)

    def test_professional_stock_tools_are_registered(self) -> None:
        names = set(ToolRegistry().get_tool_names())
        expected = {
            "get_business_segments", "get_consensus_estimates", "get_peer_comparison",
            "get_stock_capital_flow", "get_technical_indicators",
        }
        self.assertTrue(expected.issubset(names))

    def test_core_market_tools_return_explicit_success_contract(self) -> None:
        """The Agent must never infer acquisition success from an arbitrary payload shape."""
        registry = ToolRegistry()

        with patch(
            "src.tools.get_realtime_quotes.get_realtime_quotes",
            return_value={"success": True, "partial": False, "items": [{"code": "600519"}]},
        ):
            quote = registry.execute("get_realtime_quotes", {"symbols": "600519"})
        with patch(
            "src.tools._kline.fetch_and_persist_kline",
            return_value=([{"date": "2026-07-16", "close": 1500.0}], "eastmoney"),
        ), patch("src.tools._kline._expected_latest_kline_date", return_value=datetime(2026, 7, 16).date()):
            kline = registry.execute("get_kline", {"symbol": "600519", "count": 20})
        with patch(
            "api.v1.endpoints.sectors.get_sector_list",
            return_value={"items": [{"name": "白酒"}], "errors": []},
        ):
            sectors = registry.execute("get_sector_list", {"type": "industry"})

        for result in (quote, kline, sectors):
            self.assertIs(result["success"], True)
            self.assertIs(result["partial"], False)


if __name__ == "__main__":
    unittest.main()
