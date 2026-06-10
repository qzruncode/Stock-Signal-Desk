# -*- coding: utf-8 -*-
"""Tests for LLM-facing tool result compaction in agent chat."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from api.v1.endpoints.agent import (
    _assess_tool_data_health,
    _compact_tool_result,
    _maybe_attach_search_fallback,
    _run_react_loop,
)


class AgentToolResultCompactionTestCase(unittest.TestCase):
    def test_search_news_keeps_analysis_and_limits_items(self) -> None:
        payload = {
            "symbol": "600519",
            "days": 30,
            "items": [
                {
                    "title": f"新闻{i}",
                    "publish_time": f"2026-06-{i + 1:02d}T08:00:00",
                    "source": "测试源",
                    "category": "新闻",
                    "event_type": "earnings",
                    "polarity": "positive",
                    "importance": "high",
                    "summary": "摘要",
                    "url": f"https://example.com/{i}",
                    "extra": "noise",
                }
                for i in range(20)
            ],
            "analysis": {"data_quality": {"item_count": 20}},
            "source_chain": ["RSSHub"],
            "errors": [],
        }

        compact = _compact_tool_result("search_news", payload)

        self.assertEqual(compact["item_count"], 20)
        self.assertEqual(len(compact["items"]), 8)
        self.assertIn("analysis", compact)
        self.assertNotIn("url", compact["items"][0])
        self.assertNotIn("extra", compact["items"][0])

    def test_sector_list_returns_top_and_bottom_movers(self) -> None:
        payload = {
            "type": "industry",
            "items": [
                {"name": "A", "change_pct": 5.0, "lead_stock": "a1"},
                {"name": "B", "change_pct": -3.0, "lead_stock": "b1"},
                {"name": "C", "change_pct": 2.0, "lead_stock": "c1"},
            ],
            "_cached": False,
        }

        compact = _compact_tool_result("get_sector_list", payload)

        self.assertEqual(compact["total"], 3)
        self.assertEqual(compact["top_movers"][0]["name"], "A")
        self.assertEqual(compact["bottom_movers"][0]["name"], "B")
        self.assertNotIn("items", compact)

    def test_kline_keeps_recent_window_not_full_series(self) -> None:
        payload = {
            "symbol": "600519",
            "source": "eastmoney",
            "data": [{"date": f"2026-05-{i + 1:02d}", "close": i} for i in range(60)],
            "_cached": False,
        }

        compact = _compact_tool_result("get_kline", payload)

        self.assertEqual(compact["count"], 60)
        self.assertEqual(len(compact["recent"]), 30)
        self.assertEqual(compact["latest"]["close"], 59)
        self.assertEqual(compact["range"]["start"], "2026-05-01")
        self.assertEqual(compact["range"]["end"], "2026-05-60")

    def test_sector_flow_drops_duplicate_records_bucket(self) -> None:
        payload = {
            "type": "industry",
            "top_n": 10,
            "inflow_top": [{"name": "算力", "main_net_inflow": 123.0, "noise": 1}],
            "outflow_top": [{"name": "消费", "main_net_inflow": -99.0, "noise": 1}],
            "records": [{"name": "算力"}],
            "source": "同花顺",
        }

        compact = _compact_tool_result("get_sector_flow", payload)

        self.assertIn("inflow_top", compact)
        self.assertIn("outflow_top", compact)
        self.assertNotIn("records", compact)
        self.assertNotIn("noise", compact["inflow_top"][0])

    def test_empty_quotes_attach_price_search_fallback(self) -> None:
        search_response = SimpleNamespace(
            query="贵州茅台 600519 股价走势",
            provider="TestSearch",
            success=True,
            error_message=None,
            search_time=0.12,
            results=[
                SimpleNamespace(
                    title="贵州茅台股价走势",
                    snippet="搜索摘要",
                    url="https://example.com/price",
                    source="example.com",
                    published_date="2026-06-08",
                )
            ],
        )
        search_service = SimpleNamespace(
            is_available=True,
            search_stock_price_fallback=lambda code, name, max_attempts=2, max_results=5: search_response,
        )

        with patch("src.services.name_to_code_resolver.resolve_name_to_code", return_value="600519"), \
             patch("src.search_service.get_search_service", return_value=search_service):
            enriched = _maybe_attach_search_fallback(
                "get_realtime_quotes",
                {"symbols": "贵州茅台"},
                {"items": [], "total": 0},
            )

        self.assertTrue(enriched["fallback_status"]["used"])
        self.assertEqual(enriched["fallback_status"]["reason"], "empty_quotes")
        self.assertEqual(enriched["search_fallback"]["type"], "price")
        self.assertTrue(enriched["search_fallback"]["success"])

    def test_stale_news_attach_news_search_fallback(self) -> None:
        search_response = SimpleNamespace(
            query="贵州茅台 600519 股票 最新消息",
            provider="TestSearch",
            success=True,
            error_message=None,
            search_time=0.08,
            results=[
                SimpleNamespace(
                    title="贵州茅台最新消息",
                    snippet="搜索摘要",
                    url="https://example.com/news",
                    source="example.com",
                    published_date="2026-06-08",
                )
            ],
        )
        search_service = SimpleNamespace(
            is_available=True,
            search_stock_news=lambda code, name, max_results=5: search_response,
        )

        with patch("src.services.name_to_code_resolver.resolve_name_to_code", return_value="600519"), \
             patch("src.search_service.get_search_service", return_value=search_service):
            enriched = _maybe_attach_search_fallback(
                "search_news",
                {"symbol": "贵州茅台"},
                {
                    "symbol": "600519",
                    "days": 30,
                    "items": [{"title": "旧闻", "publish_time": "2026-01-01T00:00:00"}],
                },
            )

        self.assertTrue(enriched["fallback_status"]["used"])
        self.assertEqual(enriched["fallback_status"]["reason"], "stale_news_family")
        self.assertEqual(enriched["search_fallback"]["type"], "news")

    def test_health_assessment_prefers_standard_stale_flag(self) -> None:
        health = _assess_tool_data_health(
            "search_news",
            {
                "symbol": "600519",
                "days": 30,
                "data_time": "2026-01-01T00:00:00",
                "is_stale": True,
                "items": [{"title": "旧闻"}],
            },
        )

        self.assertTrue(health["should_fallback"])
        self.assertEqual(health["reason"], "stale_search_news")
        self.assertEqual(health["latest_date"], "2026-01-01T00:00:00")

    def test_compact_quotes_keeps_freshness_fields(self) -> None:
        compact = _compact_tool_result(
            "get_realtime_quotes",
            {
                "items": [{"symbol": "600519", "price": 123.0}],
                "total": 1,
                "data_time": "2026-06-08T10:00:00",
                "is_stale": False,
                "fallback_used": False,
            },
        )

        self.assertEqual(compact["data_time"], "2026-06-08T10:00:00")
        self.assertFalse(compact["is_stale"])
        self.assertFalse(compact["fallback_used"])

    def test_compact_market_status_keeps_freshness_fields(self) -> None:
        compact = _compact_tool_result(
            "get_market_status",
            {
                "up_count": 3000,
                "data_time": "2026-06-08",
                "is_stale": False,
                "fallback_used": True,
            },
        )

        self.assertEqual(compact["data_time"], "2026-06-08")
        self.assertFalse(compact["is_stale"])
        self.assertTrue(compact["fallback_used"])

    def test_compact_macro_indicator_keeps_freshness_fields(self) -> None:
        compact = _compact_tool_result(
            "get_macro_indicator",
            {
                "indicator": "PMI",
                "history": [{"period": "2026-05-01", "value": 50.1}],
                "data_time": "2026-05-01",
                "is_stale": False,
                "fallback_used": True,
            },
        )

        self.assertEqual(compact["data_time"], "2026-05-01")
        self.assertFalse(compact["is_stale"])
        self.assertTrue(compact["fallback_used"])


if __name__ == "__main__":
    unittest.main()


class _FakeDelta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeChunk:
    def __init__(self, delta):
        self.choices = [SimpleNamespace(delta=delta)]


class _FakeToolCall:
    def __init__(self, name: str, arguments: str, idx: int = 0, call_id: str = "call_1"):
        self.index = idx
        self.id = call_id
        self.function = SimpleNamespace(name=name, arguments=arguments)


class _FakeResponse:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        self._iter = iter(self._chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _FakeToolStream:
    def append_args_text(self, _text):
        return None

    def set_response(self, _payload, is_error=False):
        return None


class _FakeController:
    def __init__(self):
        self.text_parts = []
        self._stream_tasks = []

    def append_text(self, text):
        self.text_parts.append(text)

    async def add_tool_call(self, _tool_name, tool_call_id=None):
        return _FakeToolStream()


class AgentReactLoopFallbackTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_forces_final_summary_when_iterations_are_exhausted(self) -> None:
        first_response = _FakeResponse([
            _FakeChunk(_FakeDelta(tool_calls=[
                _FakeToolCall("get_risk_events", "{\"symbol\": \"002284\"}"),
            ])),
        ])
        final_response = _FakeResponse([
            _FakeChunk(_FakeDelta(content="最终结论：亚太股份短线弹性更高，但风险也更大。")),
        ])
        controller = _FakeController()
        llm_cfg = {"model": "test-model"}

        class _FakeRegistry:
            def get_all_schemas(self):
                return [{"type": "function", "function": {"name": "get_risk_events"}}]

            def get_tool_names(self):
                return ["get_risk_events"]

            def execute(self, tool_name, args):
                return {"tool": tool_name, "args": args, "items": []}

        with patch("api.v1.endpoints.agent.MAX_REACT_ITERATIONS", 1), \
             patch("api.v1.endpoints.agent._registry", _FakeRegistry()), \
             patch("api.v1.endpoints.agent.litellm.acompletion", side_effect=[first_response, final_response]):
            await _run_react_loop(
                controller,
                [{"role": "user", "content": "分析A股广电计量和亚太股份谁更值得买"}],
                llm_cfg,
            )

        combined = "".join(controller.text_parts)
        self.assertIn("已完成多轮数据查询，正在生成最终总结", combined)
        self.assertIn("最终结论：亚太股份短线弹性更高，但风险也更大。", combined)
