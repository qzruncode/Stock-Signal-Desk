# -*- coding: utf-8 -*-
"""Tests for LLM-facing tool result compaction in agent chat."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from api.v1.endpoints.agent import (
    _assess_tool_data_health,
    _compact_tool_result,
    _format_result,
    _maybe_attach_search_fallback,
)
from src.tools.registry import ToolRegistry


class AgentToolResultCompactionTestCase(unittest.TestCase):
    def test_multi_domain_candidates_keep_every_domain_identity_index(self) -> None:
        payload = {
            "success": True,
            "partial": False,
            "requested_domains": ["行星滚柱丝杠", "减速器"],
            "local_universe_count": 5879,
            "candidate_count": 2,
            "returned_count": 2,
            "domain_results": [
                {
                    "domain": "行星滚柱丝杠",
                    "lookup_themes": ["机器人执行器"],
                    "mapping_type": "proxy_board",
                    "mapping_rationale": "按执行机构语义映射",
                    "unresolved_parts": [],
                    "mapping_basis": "catalog_proxy_board",
                    "success": True,
                    "coverage_complete": True,
                    "candidate_count": 1,
                    "items": [{
                        "symbol": "300580", "name": "贝斯特",
                        "boards": ["机器人执行器"],
                        "matched_domains": ["行星滚柱丝杠"],
                    }],
                },
                {
                    "domain": "减速器",
                    "lookup_themes": ["减速器"],
                    "mapping_type": "exact_board",
                    "mapping_rationale": "同名板块",
                    "unresolved_parts": [],
                    "mapping_basis": "catalog_exact_board",
                    "success": True,
                    "coverage_complete": True,
                    "candidate_count": 1,
                    "items": [{
                        "symbol": "688017", "name": "绿的谐波",
                        "boards": ["减速器"],
                        "matched_domains": ["减速器"],
                    }],
                },
            ],
        }

        compact = _compact_tool_result("get_domain_stock_candidates", payload)

        self.assertEqual(len(compact["domain_results"]), 2)
        self.assertEqual(compact["domain_results"][0]["items"][0]["symbol"], "300580")
        self.assertEqual(compact["domain_results"][1]["items"][0]["symbol"], "688017")
        self.assertEqual(compact["domain_results"][0]["mapping_type"], "proxy_board")
        self.assertEqual(compact["domain_results"][0]["mapping_rationale"], "按执行机构语义映射")
        self.assertEqual(
            compact["_tool_payload_meta"]["compaction_reason"],
            "complete_multi_domain_candidate_indexes",
        )

    def test_theme_candidate_payload_keeps_bounded_local_verified_pool(self) -> None:
        payload = {
            "success": True,
            "theme": "人形机器人",
            "local_universe_count": 5534,
            "candidate_count": 45,
            "items": [
                {"symbol": f"0000{i:02d}", "name": f"公司{i}", "evidence_level": "L1"}
                for i in range(40)
            ],
            "errors": [],
        }

        compact = _compact_tool_result("get_theme_stock_candidates", payload)

        self.assertEqual(len(compact["items"]), 40)
        self.assertEqual(compact["local_universe_count"], 5534)
        self.assertTrue(compact["_tool_payload_meta"]["compacted"])
        self.assertEqual(
            compact["_tool_payload_meta"]["compaction_reason"],
            "all_candidate_identity_index_without_repeated_financial_fields",
        )

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
        # The Agent needs the URL to cite the source or call webfetch.
        self.assertEqual(compact["items"][0]["url"], "https://example.com/0")
        self.assertNotIn("extra", compact["items"][0])
        self.assertTrue(compact["_tool_payload_meta"]["compacted"])
        self.assertEqual(compact["_tool_payload_meta"]["compaction_reason"], "news_family_item_window")

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
        self.assertEqual(compact["_tool_payload_meta"]["payload_policy"], "compacted")

    def test_multi_stock_snapshot_labels_realtime_pe_as_dynamic_not_ttm(self) -> None:
        compact = _compact_tool_result("get_multi_stock_snapshot", {
            "success": True,
            "items": [{
                "symbol": "600519",
                "name": "贵州茅台",
                "quote": {"price": 100.0, "pe_ratio": 14.4, "pb_ratio": 6.7},
                "technical": {"success": True, "indicators": {}},
                "financial": {"report_date": "2026-03-31"},
            }],
            "total": 1,
        })

        quote = compact["items"][0]["quote"]
        self.assertEqual(quote["pe_dynamic"], 14.4)
        self.assertNotIn("pe_ratio", quote)
        self.assertIn("不是 PE(TTM)", compact["valuation_basis"]["pe_dynamic"])

    def test_professional_decision_payload_declares_compacted_seven_dimension_view(self) -> None:
        payload = {
            "success": True,
            "items": [{"symbol": "003021", "evidence_coverage": {"covered_count": 7}}],
            "evidence_standard": ["business_reality", "financial_quality"],
        }

        compact = _compact_tool_result("get_multi_stock_decision_evidence", payload)

        self.assertEqual(compact["items"], payload["items"])
        self.assertTrue(compact["_tool_payload_meta"]["compacted"])
        self.assertEqual(
            compact["_tool_payload_meta"]["compaction_reason"],
            "professional_decision_evidence_view",
        )
        self.assertEqual(
            compact["_tool_payload_meta"]["source_scope"],
            "seven_dimension_multi_stock_evidence",
        )

    def test_semantic_search_compacts_each_long_body_with_explicit_length(self) -> None:
        body = "证据" * 4000
        compact = _compact_tool_result("search_financial_news", {
            "success": True,
            "items": [{"title": "测试", "summary": body, "content_text": body}],
        })

        item = compact["items"][0]
        self.assertLessEqual(len(item["content_text"]), 1801)
        self.assertTrue(item["content_text_compacted"])
        self.assertEqual(item["content_text_characters"], len(body))
        self.assertTrue(item["summary_compacted"])
        self.assertEqual(
            compact["_tool_payload_meta"]["compaction_reason"],
            "semantic_rss_item_and_text_window",
        )

    def test_shareholder_compaction_keeps_normalized_tool_fields(self) -> None:
        payload = {
            "symbol": "600519",
            "institution_holding_ratio": 72.5,
            "institution_holding_ratio_basis": "percent_of_total_shares",
            "institution_holding": {"report_date": "2026-03-31", "percent_of_total_shares": 72.5},
            "top_holders": [{
                "rank": 1,
                "holder_name": "中国贵州茅台酒厂（集团）有限责任公司",
                "holding_shares": 680000000,
                "holding_ratio_pct": 54.0,
                "holder_type": "国有法人",
            }],
            "holder_changes": [{
                "holder_name": "测试股东",
                "change_direction": "增持",
                "change_shares": 10000,
                "announcement_date": "2026-07-01",
            }],
            "data_time": "2026-06-30",
            "is_stale": False,
        }

        compact = _compact_tool_result("get_shareholder_structure", payload)

        self.assertEqual(compact["top_holders"][0]["holder_name"], payload["top_holders"][0]["holder_name"])
        self.assertEqual(compact["holder_changes"][0]["change_direction"], "增持")
        self.assertEqual(compact["institution_holding_ratio"], 72.5)
        self.assertEqual(compact["institution_holding_ratio_basis"], "percent_of_total_shares")

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
        self.assertEqual(compact["_tool_payload_meta"]["compaction_reason"], "time_series_recent_window")

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
        self.assertTrue(compact["_tool_payload_meta"]["compacted"])

    def test_empty_quotes_attach_price_search_fallback(self) -> None:
        search_payload = {"query": "贵州茅台 600519 今日股价", "provider": "TestSearch", "success": True, "results": [{"title": "贵州茅台股价走势", "url": "https://example.com/price"}]}

        with patch("src.services.name_to_code_resolver.resolve_name_to_code", return_value="600519"), \
             patch("src.tools.websearch.websearch", return_value=search_payload):
            enriched = _maybe_attach_search_fallback(
                "get_realtime_quotes",
                {"symbols": "贵州茅台"},
                {"items": [], "total": 0},
            )

        self.assertTrue(enriched["fallback_status"]["used"])
        self.assertEqual(enriched["fallback_status"]["reason"], "empty_quotes")
        self.assertEqual(enriched["search_fallback"]["type"], "price")
        self.assertTrue(enriched["search_fallback"]["success"])

    def test_stale_news_is_left_for_agent_domain_fallback(self) -> None:
        original = {
            "symbol": "600519",
            "days": 30,
            "items": [{"title": "旧闻", "publish_time": "2026-01-01T00:00:00"}],
        }
        with patch("src.tools.websearch.websearch") as websearch:
            enriched = _maybe_attach_search_fallback(
                "search_news",
                {"symbol": "贵州茅台"},
                original,
            )

        self.assertIs(enriched, original)
        websearch.assert_not_called()

    def test_unavailable_consensus_uses_specific_search_fallback(self) -> None:
        search_payload = {"query": "贵州茅台 600519 最新 券商一致预期 EPS 净利润预测", "provider": "TestSearch", "success": True, "results": []}

        with patch("src.services.name_to_code_resolver.resolve_name_to_code", return_value="600519"), \
             patch("src.tools.websearch.websearch", return_value=search_payload):
            enriched = _maybe_attach_search_fallback(
                "get_consensus_estimates",
                {"symbol": "贵州茅台"},
                {"symbol": "600519", "success": False, "is_stale": True, "metrics": {}, "errors": ["upstream down"]},
            )

        self.assertTrue(enriched["fallback_status"]["used"])
        self.assertEqual(enriched["search_fallback"]["type"], "consensus")
        self.assertIn("一致预期", enriched["search_fallback"]["query"])

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
                "is_trading_session": False,
                "quote_mode": "latest_trading_day_snapshot",
                "quote_mode_label": "非交易时段的最近交易日快照，不是当前时刻实时成交",
            },
        )

        self.assertEqual(compact["data_time"], "2026-06-08T10:00:00")
        self.assertFalse(compact["is_stale"])
        self.assertFalse(compact["fallback_used"])
        self.assertFalse(compact["is_trading_session"])
        self.assertEqual(compact["quote_mode"], "latest_trading_day_snapshot")
        self.assertIn("不是当前时刻实时成交", compact["quote_mode_label"])
        self.assertEqual(compact["_tool_payload_meta"]["compaction_reason"], "quotes_item_window")

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
        self.assertEqual(compact["_tool_payload_meta"]["compaction_reason"], "market_status_key_fields")

    def test_monetary_operations_keep_complete_window_without_article_bodies(self) -> None:
        operations = [
            {
                "title": f"公开市场业务交易公告 [2026]第{index}号",
                "published": f"2026-07-{index:02d}",
                "link": f"https://www.pbc.gov.cn/{index}",
                "amount_yi": float(index),
                "content": "正文" * 1000,
            }
            for index in range(1, 22)
        ]
        compact = _compact_tool_result(
            "get_monetary_policy_operations",
            {
                "success": True,
                "item_count": 21,
                "available_item_count": 21,
                "result_truncated": False,
                "operations": operations,
            },
        )

        self.assertEqual(len(compact["operations"]), 21)
        self.assertEqual(compact["available_item_count"], 21)
        self.assertFalse(compact["result_truncated"])
        self.assertNotIn("content", compact["operations"][0])

    def test_format_result_does_not_silently_truncate(self) -> None:
        payload = {"text": "甲" * 6000}

        formatted = _format_result(payload)

        self.assertIn("甲" * 50, formatted)
        self.assertNotIn("...[数据已截断]", formatted)
        self.assertGreater(len(formatted), 4000)

    def test_websearch_keeps_opencode_provider_output(self) -> None:
        compact = _compact_tool_result(
            "websearch",
            {
                "query": "arbitrary topic",
                "success": True,
                "provider": "exa",
                "output": "Title: Result\nURL: https://example.com",
                "results": [{"title": "Result", "url": "https://example.com"}],
            },
        )

        self.assertIn("Title: Result", compact["output"])
        self.assertFalse(compact["_tool_payload_meta"]["compacted"])

    def test_webfetch_binary_attachment_omission_is_explicit(self) -> None:
        compact = _compact_tool_result(
            "webfetch",
            {
                "url": "https://example.com/image.png",
                "content": "Image fetched successfully",
                "attachments": [{"type": "file", "mime": "image/png", "url": "data:image/png;base64,AAAA"}],
            },
        )

        self.assertEqual(compact["attachment_count"], 1)
        self.assertNotIn("url", compact["attachments"][0])
        self.assertTrue(compact["_tool_payload_meta"]["compacted"])
        self.assertEqual(compact["_tool_payload_meta"]["compaction_reason"], "web_attachment_binary_omitted")

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
        self.assertEqual(compact["_tool_payload_meta"]["compaction_reason"], "macro_history_window")

    def test_compact_stock_info_flattens_nested_chinese_sources(self) -> None:
        payload = {
            "symbol": "301004",
            "company_name": "浙江嘉益保温科技股份有限公司",
            "short_name": "嘉益股份",
            "industry": "金属制品业",
            "market": "深交所创业板",
            "listing_date": "2021-06-25",
            "main_business": "饮品、食品容器的研发设计、生产与销售",
            "capital_snapshot": {
                "total_shares": 145806262,
                "circulating_shares": 90000000,
                "total_market_cap": 4.8e9,
                "circulating_market_cap": 3.0e9,
                "share_unit": "股",
                "market_cap_unit": "元",
            },
            "sources": ["巨潮资讯/AKShare", "东方财富"],
            "success": True,
        }

        compact = _compact_tool_result("get_stock_info", payload)

        self.assertEqual(compact["symbol"], "301004")
        self.assertEqual(compact["short_name"], "嘉益股份")
        self.assertEqual(compact["company_name"], "浙江嘉益保温科技股份有限公司")
        self.assertEqual(compact["industry"], "金属制品业")
        self.assertEqual(compact["market"], "深交所创业板")
        self.assertEqual(compact["listing_date"], "2021-06-25")
        self.assertIn("饮品", compact["main_business"])
        self.assertEqual(compact["capital_snapshot"]["total_shares"], 145806262)
        self.assertEqual(compact["capital_snapshot"]["market_cap_unit"], "元")
        self.assertEqual(
            compact["_tool_payload_meta"]["compaction_reason"], "stock_info_key_fields"
        )

    def test_every_registered_tool_preserves_explicit_success_when_compacted(self) -> None:
        for tool_name in ToolRegistry().get_tool_names():
            compact = _compact_tool_result(tool_name, {"success": True, "partial": False})
            self.assertIs(compact.get("success"), True, msg=tool_name)


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
