from __future__ import annotations

import json

from src.agent.langgraph_runtime.content_access import (
    build_content_access_targets,
    canonical_url,
    cited_reference_access_status,
    required_content_access_targets,
)
from src.agent.langgraph_runtime.middleware import _tool_message_content


def test_content_access_matching_preserves_query_parameters() -> None:
    assert canonical_url("https://example.test/article?id=1") != canonical_url(
        "https://example.test/article?id=2"
    )


def test_content_access_matching_treats_http_upgrade_as_the_same_public_resource() -> None:
    assert canonical_url("http://example.test/article?id=1") == canonical_url(
        "https://example.test/article?id=1"
    )


def test_read_web_source_tool_message_keeps_a_useful_body_preview() -> None:
    content = "正文段落。" * 500
    message = _tool_message_content(
        {
            "tool_name": "read_web_source",
            "success": True,
            "result": {
                "success": True,
                "content": content,
                "content_access": {
                    "content_read": True,
                    "content_extracted": True,
                    "content_length": len(content),
                },
            },
        },
        None,
    )

    payload = json.loads(message)
    assert len(payload["result"]["content"]) > 1_200
    assert payload["result"]["content_preview_length"] == len(content)


def test_tool_observation_preserves_empty_stale_and_fallback_state_for_the_model() -> None:
    payload = json.loads(
        _tool_message_content(
            {
                "tool_name": "search_web_source",
                "success": True,
                "data_time": None,
                "freshness_unknown": True,
                "result": {
                    "success": True,
                    "result_count": 0,
                    "is_stale": True,
                    "fallback_recommended": True,
                    "items": [],
                },
            },
            None,
        )
    )

    assert payload["observation_status"] == "stale"
    assert payload["freshness_unknown"] is True
    assert payload["next_action"]
    assert "最新" in payload["next_action"]


def test_reference_only_results_keep_all_links_as_model_selectable_candidates() -> None:
    news_url = "https://example.test/news/1"
    report_url = "https://example.test/report/1.pdf"
    targets, pending = build_content_access_targets(
        tool_results=[
            {
                "tool_name": "read_company_news_akshare",
                "action_id": "news",
                "success": True,
                "result": {
                    "items": [{"title": "新闻", "url": news_url}],
                    "reference_links": [news_url],
                },
            },
            {
                "tool_name": "read_company_research_reports_akshare",
                "action_id": "report",
                "success": True,
                "result": {
                    "items": [{"title": "研报", "url": report_url}],
                    "reference_links": [report_url],
                },
            },
        ],
    )

    assert [item["url"] for item in targets] == [news_url, report_url]
    assert [item["kind"] for item in targets] == ["article", "document"]
    assert pending == []

    six_news = [
        {"title": f"新闻 {index}", "url": f"https://example.test/news/{index}"}
        for index in range(6)
    ]
    all_targets, no_mandatory_reads = build_content_access_targets(
        tool_results=[
            {
                "tool_name": "read_company_news_akshare",
                "action_id": "many-news",
                "success": True,
                "result": {"items": six_news},
            }
        ],
    )
    assert len(all_targets) == len(six_news)
    assert no_mandatory_reads == []


def test_only_successful_non_empty_web_reads_leave_the_queue() -> None:
    url = "https://example.test/news/1"
    targets, pending = build_content_access_targets(
        tool_results=[
            {
                "tool_name": "read_company_news_akshare",
                "action_id": "news",
                "success": True,
                "result": {"items": [{"url": url}]},
            },
            {
                "tool_name": "read_web_source",
                "action_id": "read",
                "success": True,
                "arguments": {"url": url},
                "result": {"success": True, "url": url, "content": "正文"},
            },
        ],
    )

    assert [item["url"] for item in targets] == [url]
    assert pending == []

    _targets, failed_pending = build_content_access_targets(
        tool_results=[
            {
                "tool_name": "read_company_news_akshare",
                "action_id": "news",
                "success": True,
                "result": {"items": [{"url": url}]},
            },
            {
                "tool_name": "read_web_source",
                "action_id": "read",
                "success": False,
                "arguments": {"url": url},
                "result": {"success": False, "url": url, "content": ""},
            },
        ],
    )
    assert [item["url"] for item in failed_pending] == [url]

    _targets, attachment_pending = build_content_access_targets(
        tool_results=[
            {
                "tool_name": "read_company_news_akshare",
                "action_id": "news",
                "success": True,
                "result": {"items": [{"url": url}]},
            },
            {
                "tool_name": "read_web_source",
                "action_id": "attachment-read",
                "success": True,
                "arguments": {"url": url},
                "result": {
                    "success": True,
                    "url": url,
                    "content": "Binary file fetched successfully",
                    "content_access": {
                        "mode": "content_read",
                        "content_read": True,
                        "content_extracted": False,
                        "content_length": 0,
                    },
                },
            },
        ],
    )
    assert [item["url"] for item in attachment_pending] == [url]


def test_https_body_read_satisfies_an_http_reference_target() -> None:
    http_url = "http://example.test/news/1"
    https_url = "https://example.test/news/1"
    _targets, pending = build_content_access_targets(
        tool_results=[
            {
                "tool_name": "read_company_news_akshare",
                "action_id": "news",
                "success": True,
                "result": {"items": [{"url": http_url}]},
            },
            {
                "tool_name": "read_web_source",
                "action_id": "read",
                "success": True,
                "arguments": {"url": https_url},
                "result": {
                    "success": True,
                    "url": https_url,
                    "final_url": https_url,
                    "content": "正文",
                    "content_access": {
                        "content_read": True,
                        "content_extracted": True,
                        "content_length": 2,
                    },
                },
            },
        ],
    )

    assert pending == []


def test_required_reads_are_scoped_to_the_cited_reference_tool_call() -> None:
    news_url = "https://example.test/news/1"
    report_url = "https://example.test/report/1.pdf"
    tool_results = [
        {
            "tool_name": "read_company_news_akshare",
            "action_id": "news-call",
            "success": True,
            "result": {
                "content_access": {
                    "mode": "reference_only",
                    "content_read_required": True,
                },
                "reference_links": [news_url],
            },
        },
        {
            "tool_name": "read_company_research_reports_akshare",
            "action_id": "report-call",
            "success": True,
            "result": {
                "content_access": {
                    "mode": "reference_only",
                    "content_read_required": True,
                },
                "reference_links": [report_url],
            },
        },
    ]
    targets, _pending = build_content_access_targets(tool_results=tool_results)

    required = required_content_access_targets(
        answer="新闻已核对【证据 ev_news】",
        evidence=[
            {"evidence_id": "ev_news", "action_id": "news-call"},
            {"evidence_id": "ev_report", "action_id": "report-call"},
        ],
        tool_results=tool_results,
        targets=targets,
    )

    assert [item["url"] for item in required] == [news_url]


def test_required_reads_resolve_a_unique_short_reference_id() -> None:
    news_url = "https://example.test/news/1"
    tool_results = [
        {
            "tool_name": "read_company_news_akshare",
            "action_id": "news-call",
            "success": True,
            "result": {
                "content_access": {
                    "mode": "reference_only",
                    "content_read_required": True,
                },
                "reference_links": [news_url],
            },
        }
    ]
    targets, _pending = build_content_access_targets(tool_results=tool_results)

    required = required_content_access_targets(
        answer="新闻已核对【证据 ev_abcdefgh】",
        evidence=[
            {
                "evidence_id": "ev_abcdefghijklmnop",
                "action_id": "news-call",
            }
        ],
        tool_results=tool_results,
        targets=targets,
    )

    assert [item["url"] for item in required] == [news_url]


def test_cited_multi_link_access_requires_selection_per_reference_action() -> None:
    news_url = "https://example.test/news/1"
    second_news_url = "https://example.test/news/2"
    report_url = "https://example.test/report/1.pdf"
    tool_results = [
        {
            "tool_name": "read_company_news_akshare",
            "action_id": "news-call",
            "success": True,
            "result": {
                "content_access": {
                    "mode": "reference_only",
                    "content_read_required": True,
                },
                "reference_links": [news_url, second_news_url],
            },
        },
        {
            "tool_name": "read_company_research_reports_akshare",
            "action_id": "report-call",
            "success": True,
            "result": {
                "content_access": {
                    "mode": "reference_only",
                    "content_read_required": True,
                },
                "reference_links": [report_url],
            },
        },
    ]
    targets, _pending = build_content_access_targets(tool_results=tool_results)

    status = cited_reference_access_status(
        answer="新闻结论【证据 ev_news】",
        evidence=[
            {"evidence_id": "ev_news", "action_id": "news-call"},
            {"evidence_id": "ev_report", "action_id": "report-call"},
        ],
        tool_results=[
            *tool_results,
            {
                "tool_name": "read_web_source",
                "action_id": "read-report",
                "success": True,
                "arguments": {"url": report_url},
                "result": {"content": "研报正文"},
            },
        ],
        targets=targets,
    )

    news_status = status["news-call"]
    assert news_status["selection_required"] is True
    assert news_status["satisfied"] is False
    assert news_status["selected_targets"] == []
    assert news_status["required_targets"] == []
    assert status.keys() == {"news-call"}

    selected_status = cited_reference_access_status(
        answer="新闻结论【证据 ev_news】",
        evidence=[{"evidence_id": "ev_news", "action_id": "news-call"}],
        tool_results=tool_results,
        targets=targets,
        selected_urls={news_url, report_url},
        successful_urls={news_url, report_url},
    )["news-call"]
    assert selected_status["selection_required"] is False
    assert selected_status["satisfied"] is True
    assert [item["url"] for item in selected_status["required_targets"]] == [news_url]
