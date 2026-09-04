from __future__ import annotations

from src.agent.langgraph_runtime.presentation import (
    enrich_execution_trace_with_result_previews,
    project_arguments_for_timeline,
    project_tool_result_for_timeline,
)
from src.agent.terminal_publisher import _trace_tool_results


def test_timeline_projection_names_provider_and_each_returned_result() -> None:
    result = {
        "success": True,
        "provider": "firecrawl_searxng",
        "source": {"provider": "firecrawl_searxng", "operation": "web_search"},
        "result_count": 2,
        "results": [
            {
                "title": "人形机器人供应链进展",
                "url": "https://example.test/robotics-1",
                "source": "证券时报",
                "published_date": "2026-08-08",
                "snippet": "核心零部件进入放量阶段。",
            },
            {
                "title": "灵巧手产业观察",
                "url": "https://example.test/robotics-2",
                "source": "财联社",
                "published_date": "2026-08-07",
                "snippet": "产业链厂商持续扩产。",
            },
        ],
    }

    projected = project_tool_result_for_timeline(
        result,
        source_refs=[
            "firecrawl_searxng",
            "https://example.test/robotics-1",
            "example.test",
        ],
    )

    assert projected["result_count"] == 2
    assert projected["source_labels"] == ["firecrawl_searxng", "证券时报", "财联社"]
    assert [item["title"] for item in projected["result_items"]] == [
        "人形机器人供应链进展",
        "灵巧手产业观察",
    ]
    assert projected["result_items"][0]["url"] == "https://example.test/robotics-1"
    assert projected["result_items"][0]["summary"] == "核心零部件进入放量阶段。"


def test_timeline_projection_turns_named_mapping_records_into_result_rows() -> None:
    projected = project_tool_result_for_timeline(
        {
            "indices": {
                "shanghai_composite": {
                    "code": "sh000001",
                    "name": "上证指数",
                    "price": 3942.0879,
                    "change_pct": 0.018,
                },
                "shenzhen_component": {
                    "code": "sz399001",
                    "name": "深证成指",
                    "price": 13625.122,
                    "change_pct": 0.1,
                },
            },
            "total_amount": 17589.16,
            "total_amount_unit": "亿元",
            "turnover_scope": "沪深市场",
            "source": "新浪实时指数行情",
        }
    )

    assert projected["result_count"] == 2
    assert [item["title"] for item in projected["result_items"]] == [
        "sh000001 上证指数",
        "sz399001 深证成指",
    ]
    assert projected["result_items"][0]["attributes"] == [
        {"name": "price", "value": "3942.0879"},
        {"name": "change_pct", "value": "0.018"},
    ]


def test_historical_trace_enrichment_reuses_the_bounded_result_projection() -> None:
    enriched = enrich_execution_trace_with_result_previews(
        {
            "tool_results": [{
                "action_id": "call-market",
                "tool_name": "read_market_indices_sina",
                "result_items": [],
            }],
            "evidence": [{
                "evidence_id": "ev-market",
                "action_id": "call-market",
                "source_refs": ["新浪实时指数行情"],
            }],
        },
        evidence=[{
            "evidence_id": "ev-market",
            "action_id": "call-market",
            "result": {
                "indices": {
                    "shanghai_composite": {
                        "name": "上证指数",
                        "price": 3942.0879,
                    },
                },
            },
        }],
    )

    assert enriched["evidence"][0]["result_items"][0]["title"] == "上证指数"
    assert enriched["evidence"][0]["result_items"][0]["attributes"] == [
        {"name": "price", "value": "3942.0879"},
    ]
    assert enriched["tool_results"][0]["result_items"][0]["title"] == "上证指数"


def test_timeline_arguments_keep_query_but_redact_secrets_and_server_fields() -> None:
    projected = project_arguments_for_timeline(
        {
            "source_id": "exa",
            "query": "人形机器人产业链",
            "credentials": {"access_token": "do-not-render", "region": "cn"},
            "confirmed": True,
        },
        sensitive_fields=("credentials",),
        server_controlled_fields=("confirmed",),
    )

    assert projected == {
        "source_id": "exa",
        "query": "人形机器人产业链",
        "credentials": "***",
    }


def test_terminal_trace_persists_result_rows_instead_of_a_false_source_count() -> None:
    trace = _trace_tool_results(
        [
            {
                "action_id": "call-1",
                "tool_name": "read_rss_source",
                "arguments": {"source_id": "eastmoney_report", "limit": 20},
                "success": True,
                "source_refs": ["东方财富"],
                "result": {
                    "success": True,
                    "source": {"provider": "东方财富", "feed_name": "机构研报"},
                    "item_count": 1,
                    "items": [
                        {
                            "title": "机器人行业深度报告",
                            "link": "https://example.test/report",
                            "published": "2026-08-08T09:00:00+08:00",
                            "summary": "行业进入产业化阶段。",
                        }
                    ],
                },
            }
        ]
    )[0]

    assert trace["arguments"] == {"source_id": "eastmoney_report", "limit": 20}
    assert trace["source_labels"] == ["东方财富 · 机构研报"]
    assert trace["result_count"] == 1
    assert trace["result_items"][0]["title"] == "机器人行业深度报告"
    assert trace["result_items"][0]["url"] == "https://example.test/report"
    assert trace["source_refs"] == ["东方财富"]


def test_terminal_trace_normalizes_nested_empty_result_outcome() -> None:
    trace = _trace_tool_results(
        [
            {
                "action_id": "call-empty",
                "tool_name": "read_sector_news",
                "success": True,
                "result": {
                    "success": True,
                    "items": [],
                    "data_time": "2026-08-28",
                },
            }
        ]
    )[0]

    assert trace["success"] is True
    assert trace["data_time"] == "2026-08-28"
    assert trace["outcome"] == {
        "execution_status": "completed",
        "access_status": "structured_data",
        "data_status": "empty",
        "usable": False,
        "quality_status": "warning",
    }
