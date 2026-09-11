# -*- coding: utf-8 -*-
"""Tests for tool-registry defaults and model-facing symbol resolution."""

from __future__ import annotations

import unittest
import inspect
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from src.tools.registry import TOOL_MODULES, ToolRegistry
from src.tools.base import ToolSpec, enforce_result_contract, object_schema


class ToolRegistryModelFitnessTestCase(unittest.TestCase):
    def test_registry_contains_every_declared_unique_module_owned_tool(self) -> None:
        registry = ToolRegistry()
        names = registry.get_tool_names()

        self.assertGreaterEqual(len(names), len(TOOL_MODULES))
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(
            set(TOOL_MODULES),
            {registry.get_tool_owner_module(name) for name in names},
        )

    def test_each_registered_tool_has_an_existing_owner_module(self) -> None:
        tools_dir = Path(__file__).parents[1] / "src" / "tools"
        registry = ToolRegistry()
        missing = [
            name
            for name in registry.get_tool_names()
            if not (tools_dir / f"{registry.get_tool_owner_module(name)}.py").is_file()
        ]
        self.assertEqual(missing, [])

    def test_each_registered_tool_executor_is_owned_by_its_tool_module(self) -> None:
        registry = ToolRegistry()
        for name in registry.get_tool_names():
            spec = registry.get_tool(name)
            self.assertIsNotNone(spec)
            self.assertEqual(
                getattr(spec.executor, "__module__", None),
                f"src.tools.{registry.get_tool_owner_module(name)}",
                msg=name,
            )

    def test_every_schema_property_is_accepted_by_its_executor(self) -> None:
        """Prevent model-visible camel/snake-case drift from failing at runtime."""
        registry = ToolRegistry()
        for tool in registry._tools.values():
            signature = inspect.signature(tool.executor)
            accepts_kwargs = any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()
            )
            if accepts_kwargs:
                continue
            unsupported = set(tool.parameters["properties"]) - set(signature.parameters)
            self.assertEqual(unsupported, set(), msg=f"{tool.name}: {sorted(unsupported)}")

    def test_registry_repairs_camel_case_and_string_boolean_arguments(self) -> None:
        registry = ToolRegistry()
        captured = {}

        def fake_company_news(**kwargs):
            captured.update(kwargs)
            return {"success": True, "errors": []}

        original = registry._tools["read_company_news_akshare"]
        registry._tools["read_company_news_akshare"] = replace(
            original,
            executor=fake_company_news,
        )

        registry.execute(
            "read_company_news_akshare",
            {
                "symbol": "600519",
                "useCache": "false",
                "days": "30",
            },
        )

        self.assertIs(captured["use_cache"], False)
        self.assertEqual(captured["days"], 30)
        self.assertNotIn("useCache", captured)

    def test_registry_rejects_numeric_arguments_outside_declared_schema_bounds(self) -> None:
        registry = ToolRegistry()
        arguments = {
            "symbol": "600519",
            "days": 365,
            "limit": "100",
        }

        normalized = registry.normalize_arguments("read_company_news_akshare", arguments)
        self.assertEqual(normalized["days"], 365)
        self.assertEqual(normalized["limit"], 100)
        with self.assertRaises(ValueError):
            registry.validate_arguments("read_company_news_akshare", arguments)

    def test_registry_repairs_iso_dates_for_compact_date_schema(self) -> None:
        registry = ToolRegistry()
        normalized = registry.normalize_arguments(
            "read_kline_range",
            {
                "source_id": "eastmoney",
                "symbol": "600519",
                "startDate": "2026-01-01",
                "endDate": "2026/07/17",
            },
        )

        self.assertEqual(normalized["start_date"], "20260101")
        self.assertEqual(normalized["end_date"], "20260717")

    def test_registry_does_not_guess_human_readable_sector_flow_enums(self) -> None:
        registry = ToolRegistry()
        normalized = registry.normalize_arguments(
            "read_sector_flow_eastmoney",
            {
                "type": "行业板块",
                "period": "近5日",
                "max_items": "10",
            },
        )

        self.assertEqual(normalized["type"], "行业板块")
        self.assertEqual(normalized["period"], "近5日")
        self.assertEqual(normalized["max_items"], 10)

    def test_all_tool_schemas_are_closed_and_described(self) -> None:
        for schema in ToolRegistry().get_all_schemas():
            function = schema["function"]
            parameters = function["parameters"]
            self.assertTrue(function["description"].strip(), msg=function["name"])
            self.assertEqual(parameters["type"], "object", msg=function["name"])
            self.assertIs(parameters["additionalProperties"], False, msg=function["name"])

    def test_atomic_tool_schema_allows_only_declared_source_catalog_selection(self) -> None:
        for spec in ToolRegistry()._tools.values():
            fields = set(spec.model_parameters().get("properties") or {})
            self.assertFalse(
                fields
                & {
                    "action",
                    "operation",
                    "workflow",
                    "capability",
                    "tool",
                    "tool_name",
                    "provider",
                    "source",
                    "route_path",
                    "namespace",
                    "params",
                    "fallback",
                },
                msg=spec.name,
            )
            if "source_id" in fields:
                self.assertTrue(spec.source_catalog, msg=spec.name)
            else:
                self.assertFalse(spec.source_catalog, msg=spec.name)

        with self.assertRaisesRegex(ValueError, "operation or source selector"):
            ToolSpec(
                name="invalid_source_switcher",
                description="不应允许模型选择来源",
                parameters=object_schema({"provider": {"type": "string"}}),
                executor=lambda **_kwargs: {"success": True},
            )
        with self.assertRaisesRegex(ValueError, "source_id without a declared source_catalog"):
            ToolSpec(
                name="undeclared_source_id",
                description="不应允许未声明目录的来源选择",
                parameters=object_schema({"source_id": {"type": "string"}}),
                executor=lambda **_kwargs: {"success": True},
            )

    def test_atomic_company_profile_read_uses_the_declared_source_operation(self) -> None:
        registry = ToolRegistry()

        with patch(
            "src.tools.get_stock_info.read_source",
            return_value={
                "symbol": "600519",
                "short_name": "贵州茅台",
                "success": True,
            },
        ) as read:
            result = registry.execute(
                "read_company_profile_cninfo",
                {"symbol": "600519"},
            )

        read.assert_called_once_with(
            "get_stock_info.read_company_profile_cninfo",
            {"symbol": "600519", "use_cache": True},
        )
        self.assertEqual(result["symbol"], "600519")
        self.assertEqual(result["short_name"], "贵州茅台")

    def test_direct_realtime_quote_resolves_one_symbol_without_fallback(self) -> None:
        registry = ToolRegistry()

        def resolver(value: str) -> str:
            return {"贵州茅台": "600519", "宁德时代": "300750"}.get(value, value)

        with (
            patch(
                "src.services.name_to_code_resolver.resolve_local_name_to_code",
                side_effect=resolver,
            ) as local_resolver,
            patch(
                "src.services.name_to_code_resolver.resolve_name_to_code",
                side_effect=AssertionError("atomic source reads must not call the legacy resolver"),
            ),
            patch(
                "src.tools.realtime_quote_source_tools.read_source",
                return_value={
                    "success": True,
                    "items": [{"code": "600519"}],
                    "fallback_used": False,
                },
            ) as read,
        ):
            result = registry.execute(
                "read_realtime_quote",
                {"source_id": "eastmoney_push", "symbol": "贵州茅台"},
            )

        read.assert_called_once_with(
            "quotes", {"symbol": "600519", "source_id": "eastmoney_push"}
        )
        self.assertEqual(result["items"][0]["code"], "600519")
        self.assertFalse(result["fallback_used"])
        local_resolver.assert_called_once_with("贵州茅台")

    def test_schema_defaults_are_tightened_for_heavy_tools(self) -> None:
        registry = ToolRegistry()
        schemas = {item["function"]["name"]: item["function"]["parameters"] for item in registry.get_all_schemas()}

        self.assertEqual(schemas["read_recent_kline"]["properties"]["count"]["default"], 60)
        self.assertTrue(schemas["read_recent_kline"]["properties"]["allow_fallback"]["default"])
        self.assertTrue(schemas["calculate_technical_indicator"]["properties"]["allow_fallback"]["default"])
        self.assertEqual(schemas["read_core_financial_indicators_ths"]["properties"]["periods"]["default"], 6)
        self.assertEqual(schemas["get_balance_sheet"]["properties"]["periods"]["default"], 4)
        self.assertEqual(schemas["read_company_news_akshare"]["properties"]["days"]["default"], 30)
        self.assertEqual(schemas["read_company_research_reports_akshare"]["properties"]["days"]["default"], 365)

    def test_web_fallback_capability_is_declared_for_external_legacy_categories(self) -> None:
        registry = ToolRegistry()

        self.assertIs(registry.get_tool("read_company_profile_cninfo").web_fallback, True)
        self.assertIs(registry.get_tool("read_stock_capital_snapshot_eastmoney").web_fallback, True)
        self.assertIs(registry.get_tool("read_peer_comparison_dimension_eastmoney").web_fallback, True)
        self.assertIs(registry.get_tool("read_rss_item").web_fallback, True)
        self.assertIsNone(registry.get_tool("read_text_document").web_fallback)
        self.assertIsNone(registry.get_tool("search_analysis_history").web_fallback)

    def test_removed_search_fallback_tools_are_not_registered(self) -> None:
        registry = ToolRegistry()
        names = set(registry.get_tool_names())

        self.assertNotIn("search_web_news", names)
        self.assertNotIn("search_web_price_fallback", names)
        self.assertNotIn("fetch_web_content", names)

    def test_redundant_derived_tools_are_not_registered(self) -> None:
        registry = ToolRegistry()
        names = set(registry.get_tool_names())

        self.assertNotIn("get_price_overdraft_signal", names)
        self.assertNotIn("get_sentiment", names)

    def test_composite_risk_event_tool_is_not_registered(self) -> None:
        registry = ToolRegistry()
        names = set(registry.get_tool_names())

        self.assertNotIn("get_risk_events", names)
        self.assertNotIn("get_announcements", names)
        self.assertIn("read_company_news_akshare", names)
        self.assertIn("read_company_announcements_akshare", names)

    def test_model_catalog_excludes_removed_task_status_adapters(self) -> None:
        names = set(ToolRegistry().get_tool_names())

        self.assertIn("send_custom_notification", names)
        self.assertTrue(
            {
                "read_analysis_task",
                "list_analysis_tasks",
                "get_analysis_status",
                "send_notification",
                "send_batch_run_notification",
            }.isdisjoint(names)
        )

    def test_single_operation_schemas_do_not_hide_report_lookup_or_schedule_fallback(self) -> None:
        registry = ToolRegistry()
        notification = registry.get_tool("send_custom_notification")
        schedule = registry.get_tool("update_analysis_schedule")

        self.assertIsNotNone(notification)
        self.assertIsNotNone(schedule)
        notification_fields = set(notification.model_parameters()["properties"])
        self.assertEqual(notification_fields, {"message", "title"})
        self.assertNotIn("confirmed", notification_fields)
        schedule_schema = schedule.model_parameters()
        self.assertIn("prompt_template_id", schedule_schema["required"])

    def test_rss_exposes_generic_operations_and_complete_source_catalog(self) -> None:
        names = set(ToolRegistry().get_tool_names())

        self.assertNotIn("search_financial_news", names)
        self.assertNotIn("search_research_library", names)
        self.assertNotIn("get_regulatory_updates", names)
        self.assertNotIn("get_monetary_policy_operations", names)
        self.assertNotIn("list_rss_sources", names)
        self.assertNotIn("discover_rss_sources", names)
        self.assertNotIn("inspect_rss_source", names)
        self.assertNotIn("read_rss_feed", names)
        self.assertIn("read_rss_source", names)
        self.assertIn("list_rss_source_catalog", names)
        self.assertIn("read_rss_item", names)
        self.assertIn("read_text_document", names)
        self.assertNotIn("export_rss_feed", names)
        source_spec = ToolRegistry().get_tool("read_rss_source")
        self.assertIsNotNone(source_spec)
        assert source_spec is not None
        self.assertEqual(len(source_spec.source_catalog), 47)
        self.assertIn(
            "cls_telegraph",
            {str(item["id"]) for item in source_spec.source_catalog},
        )

    def test_professional_stock_tools_are_source_level_reads(self) -> None:
        names = set(ToolRegistry().get_tool_names())
        expected = {
            "read_business_segments_eastmoney",
            "read_sector_flow_eastmoney",
            "read_bond_yield_eastmoney",
            "read_macro_indicator_akshare",
            "read_consensus_metric_ths",
            "read_peer_comparison_dimension_eastmoney",
            "read_stock_capital_flow_history_eastmoney",
            "calculate_technical_indicator",
        }
        self.assertTrue(expected.issubset(names))
        self.assertTrue(
            {
                "get_financials",
                "get_valuation_ratios",
                "get_stock_capital_flow",
                "get_market_status",
                "get_market_breadth",
                "get_index_data",
                "get_realtime_quotes",
                "get_kline",
                "get_history_data",
                "get_technical_indicators",
            }.isdisjoint(names)
        )

    def test_composite_quantitative_screen_is_model_callable(self) -> None:
        registry = ToolRegistry()
        self.assertIn("screen_atr_volatility_stocks", registry.get_tool_names())
        tool = registry.get_tool("screen_atr_volatility_stocks")
        self.assertIsNotNone(tool)
        assert tool is not None
        self.assertEqual(tool.effect_for({"screen_spec": {}}), "read")
        self.assertEqual(
            tool.effect_for({"screen_spec": {}, "save_group_name": "高波动股"}),
            "side_effect",
        )
        with self.assertRaises(ValueError):
            registry.validate_arguments("screen_atr_volatility_stocks", {})

    def test_result_contract_completes_nullable_freshness_fields(self) -> None:
        result = enforce_result_contract("demo", {"success": True, "errors": []})

        self.assertIs(result["partial"], False)
        self.assertIsNone(result["data_time"])
        self.assertIsNone(result["is_stale"])
        self.assertIs(result["freshness_unknown"], True)
        self.assertEqual(result["warnings"], [])

    def test_result_contract_rejects_impossible_partial_failure(self) -> None:
        with self.assertRaisesRegex(ValueError, "partial cannot be true"):
            enforce_result_contract(
                "demo",
                {"success": False, "partial": True, "errors": ["failed"]},
            )

    def test_result_contract_marks_success_with_errors_as_partial(self) -> None:
        result = enforce_result_contract(
            "demo",
            {"success": True, "partial": False, "errors": ["primary source failed"]},
        )

        self.assertIs(result["partial"], True)

    def test_result_contract_rejects_claimed_freshness_without_data_time(self) -> None:
        with self.assertRaisesRegex(ValueError, "is_stale must be null"):
            enforce_result_contract(
                "demo",
                {"success": False, "errors": ["failed"], "data_time": None, "is_stale": True},
            )

    def test_core_market_tools_return_explicit_success_contract(self) -> None:
        """The Agent must never infer acquisition success from an arbitrary payload shape."""
        registry = ToolRegistry()

        with patch(
            "src.tools.realtime_quote_source_tools.read_source",
            return_value={
                "success": True,
                "items": [{"code": "600519"}],
                "fallback_used": False,
            },
        ):
            quote = registry.execute(
                "read_realtime_quote",
                {"source_id": "eastmoney_push", "symbol": "600519"},
            )
        with patch(
            "src.tools.source_operations._read_kline_source",
            return_value={
                "success": True,
                "partial": False,
                "symbol": "600519",
                "data": [{"date": "2026-07-16", "close": 1500.0}],
                "errors": [],
                "warnings": [],
            },
        ):
            kline = registry.execute(
                "read_recent_kline",
                {"source_id": "eastmoney", "symbol": "600519", "count": 20},
            )
        original_flow = registry._tools["read_sector_flow_eastmoney"]
        registry._tools["read_sector_flow_eastmoney"] = replace(
            original_flow,
            executor=lambda **_kwargs: {
                "success": True,
                "partial": False,
                "items": [{"name": "白酒", "sector_code": "BK0477"}],
                "errors": [],
                "warnings": [],
                "freshness_unknown": True,
            },
        )
        sectors = registry.execute(
            "read_sector_flow_eastmoney",
            {"type": "industry", "period": "today", "max_items": 10},
        )

        for result in (quote, kline, sectors):
            self.assertIs(result["success"], True)
            self.assertIs(result["partial"], False)


if __name__ == "__main__":
    unittest.main()
