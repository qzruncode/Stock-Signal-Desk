from __future__ import annotations

from src.agent.behavior_audit import (
    build_behavior_audit,
    describe_tool_access,
    describe_tool_outcome,
    describe_tool_quality,
)


def _snapshot(tool_results, *, claims=None, evidence=None, final_text=""):
    return {
        "run": {"status": "completed", "final_text": final_text},
        "quality_projection": {
            "budgets": {"model_turn_count": 3, "tool_call_count": len(tool_results)},
            "tool_results": tool_results,
            "evidence": evidence or [],
            "claim_evidence": claims or [],
        },
        "steps": [],
    }


def test_reference_links_are_not_treated_as_document_reads() -> None:
    audit = build_behavior_audit(
        _snapshot(
            [
                {
                    "action_id": "research-1",
                    "tool_name": "read_company_research_reports_akshare",
                    "success": True,
                    "result_count": 2,
                    "reference_links": [
                        "https://example.test/a.pdf",
                        "https://example.test/b.pdf",
                    ],
                }
            ]
        )
    )

    assert audit["status"] == "info"
    assert audit["action_required_count"] == 0
    assert audit["advisory_count"] >= 1
    assert audit["reference_link_count"] == 2
    assert audit["unread_document_count"] == 2
    assert audit["content_read_call_count"] == 0
    finding = next(item for item in audit["findings"] if item["code"] == "reference_not_selected_document")
    assert finding["disposition"] == "advisory"
    assert [item["kind"] for item in audit["sampling"]["targets"]] == ["document", "document"]


def test_successful_web_read_covers_the_matching_reference() -> None:
    audit = build_behavior_audit(
        _snapshot(
            [
                {
                    "action_id": "research-1",
                    "tool_name": "read_company_research_reports_akshare",
                    "success": True,
                    "result_count": 1,
                    "reference_links": ["https://example.test/a.pdf"],
                },
                {
                    "action_id": "read-1",
                    "tool_name": "read_web_source",
                    "success": True,
                    "arguments": {"url": "https://example.test/a.pdf"},
                    "content_access": {
                        "mode": "content_read",
                        "content_read": True,
                        "content_extracted": True,
                    },
                },
            ],
            evidence=[{"action_id": "read-1", "evidence_id": "ev-read-1"}],
        )
    )

    assert audit["unread_document_count"] == 0
    assert audit["content_read_call_count"] == 1
    assert audit["content_extracted_call_count"] == 1
    assert not any(item["code"] == "reference_only_document" for item in audit["findings"])


def test_news_links_are_flagged_without_article_reads() -> None:
    audit = build_behavior_audit(
        _snapshot(
            [
                {
                    "action_id": "news-1",
                    "tool_name": "read_company_news_akshare",
                    "success": True,
                    "result_count": 1,
                    "reference_links": ["https://example.test/news/1"],
                }
            ]
        )
    )

    assert audit["unread_article_count"] == 1
    finding = next(item for item in audit["findings"] if item["code"] == "reference_not_selected_article")
    assert finding["disposition"] == "advisory"
    assert audit["status"] == "info"
    assert audit["action_required_count"] == 0


def test_unselected_candidates_are_an_info_hint_after_a_successful_read() -> None:
    audit = build_behavior_audit(
        _snapshot(
            [
                {
                    "action_id": "research-1",
                    "tool_name": "read_company_research_reports_akshare",
                    "success": True,
                    "result_count": 2,
                    "reference_links": [
                        "https://example.test/a.pdf",
                        "https://example.test/b.pdf",
                    ],
                },
                {
                    "action_id": "read-1",
                    "tool_name": "read_web_source",
                    "success": True,
                    "arguments": {"url": "https://example.test/a.pdf"},
                    "content_access": {
                        "mode": "content_read",
                        "content_read": True,
                        "content_extracted": True,
                        "content_length": 100,
                    },
                },
            ],
            evidence=[{"action_id": "read-1", "evidence_id": "ev-read-1"}],
        )
    )

    assert audit["unread_document_count"] == 1
    assert not any(item["code"] == "reference_only_document" for item in audit["findings"])
    assert any(
        item["code"] == "reference_not_selected_document" and item["severity"] == "info"
        for item in audit["findings"]
    )


def test_cited_reference_only_call_is_checked_independently_from_other_calls() -> None:
    audit = build_behavior_audit(
        _snapshot(
            [
                {
                    "action_id": "news-1",
                    "tool_name": "read_company_news_akshare",
                    "success": True,
                    "result_count": 1,
                    "reference_links": ["https://example.test/news/1"],
                },
                {
                    "action_id": "research-1",
                    "tool_name": "read_company_research_reports_akshare",
                    "success": True,
                    "result_count": 1,
                    "reference_links": ["https://example.test/report/1.pdf"],
                },
                {
                    "action_id": "read-1",
                    "tool_name": "read_web_source",
                    "success": True,
                    "arguments": {"url": "https://example.test/news/1"},
                    "content_access": {
                        "mode": "content_read",
                        "content_read": True,
                        "content_extracted": True,
                        "content_length": 100,
                    },
                },
            ],
            claims=[
                {
                    "evidence_ids": ["ev-research"],
                    "checks": {"tool_success": True},
                }
            ],
            evidence=[
                {
                    "evidence_id": "ev-news",
                    "action_id": "news-1",
                    "tool_name": "read_company_news_akshare",
                },
                {
                    "evidence_id": "ev-research",
                    "action_id": "research-1",
                    "tool_name": "read_company_research_reports_akshare",
                },
            ],
        )
    )

    finding = next(item for item in audit["findings"] if item["code"] == "cited_reference_without_body")
    assert finding["action_ids"] == ["research-1"]
    assert finding["links"] == ["https://example.test/report/1.pdf"]
    assert audit["cited_reference_tool_count"] == 1


def test_cited_reference_falls_back_to_final_text_when_partial_run_has_no_ledger() -> None:
    audit = build_behavior_audit(
        _snapshot(
            [
                {
                    "action_id": "research-1",
                    "tool_name": "read_company_research_reports_akshare",
                    "success": True,
                    "result_count": 1,
                    "reference_links": ["https://example.test/report/1.pdf"],
                }
            ],
            evidence=[
                {
                    "evidence_id": "ev_research",
                    "action_id": "research-1",
                    "tool_name": "read_company_research_reports_akshare",
                }
            ],
            final_text="研报索引显示相关观点【证据 ev_research】\n\n[正文取证未完成]",
        )
    )

    finding = next(item for item in audit["findings"] if item["code"] == "cited_reference_without_body")
    assert finding["action_ids"] == ["research-1"]
    assert audit["cited_reference_tool_count"] == 1


def test_static_identity_lookup_is_not_reported_as_missing_source_time() -> None:
    audit = build_behavior_audit(
        _snapshot(
            [
                {
                    "action_id": "stock-1",
                    "tool_name": "search_stocks",
                    "success": True,
                    "result_count": 1,
                    "freshness_unknown": True,
                }
            ]
        )
    )

    assert not any(item["code"] == "unknown_data_time" for item in audit["findings"])


def test_failed_tool_is_high_risk_and_quality_checks_are_exposed() -> None:
    access = describe_tool_access(
        "read_web_source",
        {
            "success": True,
            "result": {
                "success": True,
                "content": "正文内容",
                "extraction_method": "http+markdown",
            },
        },
    )
    quality = describe_tool_quality(
        "read_company_news_akshare",
        {
            "success": True,
            "result": {
                "success": True,
                "item_count": 2,
                "items": [
                    {"title": "相同标题", "url": "https://example.test/1"},
                    {"title": "相同标题", "url": "https://example.test/1"},
                ],
                "warnings": ["主体需要复核"],
            },
        },
    )
    audit = build_behavior_audit(
        _snapshot(
            [
                {
                    "action_id": "failed-1",
                    "tool_name": "read_recent_kline",
                    "success": False,
                    "errors": ["上游连接失败"],
                }
            ]
        )
    )

    assert access["mode"] == "content_read"
    assert access["content_read"] is True
    assert access["content_extracted"] is True
    assert quality["status"] == "warning"
    assert quality["duplicate_count"] == 1
    assert audit["status"] == "danger"
    assert audit["danger_count"] == 1
    assert audit["findings"][0]["code"] == "tool_execution_failed"


def test_successful_reader_without_body_is_not_marked_as_extracted() -> None:
    access = describe_tool_access(
        "read_web_source",
        {"success": True, "result": {"success": True, "content": ""}},
    )

    assert access["mode"] == "content_read"
    assert access["content_read"] is True
    assert access["content_extracted"] is False


def test_reader_metadata_does_not_prove_body_extraction() -> None:
    access = describe_tool_access(
        "read_web_source",
        {
            "success": True,
            "result": {
                "extraction_method": "http+markdown",
                "coverage_digest": {"coverage_complete": True},
            },
        },
    )

    assert access["content_read"] is True
    assert access["content_extracted"] is False


def test_outer_execution_failure_wins_over_nested_success_payload() -> None:
    outcome = describe_tool_outcome(
        "read_realtime_quote",
        {
            "success": False,
            "result": {"success": True, "result_count": 1},
        },
    )

    assert outcome["execution_status"] == "failed"
    assert outcome["data_status"] == "error"


def test_data_time_not_applicable_is_preserved_from_nested_result() -> None:
    quality = describe_tool_quality(
        "search_stocks",
        {
            "success": True,
            "result": {
                "result_count": 1,
                "data_time_applicable": False,
                "freshness_unknown": True,
                "items": [{"symbol": "000682"}],
            },
        },
    )

    assert not any(check["code"] == "unknown_data_time" for check in quality["checks"])


def test_claim_without_evidence_is_reported_even_when_tools_succeed() -> None:
    audit = build_behavior_audit(
        _snapshot(
            [
                {
                    "action_id": "quote-1",
                    "tool_name": "read_realtime_quote",
                    "success": True,
                    "result_count": 1,
                }
            ],
            claims=[
                {
                    "claim_id": "claim-1",
                    "text": "公司当前股价为 10 元",
                    "evidence_ids": [],
                    "checks": {"tool_success": True, "source": False, "entity_scope": False, "time": False},
                }
            ],
        )
    )

    assert any(item["code"] == "claim_evidence_check_failed" for item in audit["findings"])


def test_tool_outcome_keeps_execution_access_and_data_status_separate() -> None:
    outcome = describe_tool_outcome(
        "read_web_source",
        {
            "success": True,
            "result": {
                "success": True,
                "content": "",
                "data_time": "2026-08-28",
                "is_stale": True,
            },
        },
    )

    assert outcome == {
        "execution_status": "completed",
        "access_status": "content_unavailable",
        "data_status": "empty",
        "usable": False,
        "quality_status": "warning",
    }


def test_nested_result_source_refs_are_used_by_quality_projection() -> None:
    quality = describe_tool_quality(
        "read_realtime_quote",
        {
            "success": True,
            "result": {
                "result_count": 1,
                "source_refs": ["tool:read_realtime_quote"],
                "items": [{"symbol": "000682", "price": 10.2}],
            },
        },
    )

    assert quality["source_present"] is False
    assert any(check["code"] == "missing_source" for check in quality["checks"])


def test_empty_result_collection_without_count_is_detected() -> None:
    quality = describe_tool_quality(
        "read_sector_news",
        {"success": True, "result": {"items": []}},
    )

    assert any(check["code"] == "empty_result" for check in quality["checks"])


def test_empty_result_is_advisory_when_another_action_is_usable() -> None:
    audit = build_behavior_audit(
        _snapshot(
            [
                {
                    "action_id": "empty-1",
                    "tool_name": "read_sector_news",
                    "success": True,
                    "result_count": 0,
                },
                {
                    "action_id": "usable-1",
                    "tool_name": "read_realtime_quote",
                    "success": True,
                    "result_count": 1,
                    "source_refs": ["ev-usable"],
                },
            ]
        )
    )

    finding = next(item for item in audit["findings"] if item["code"] == "successful_empty_result")
    assert finding["severity"] == "info"
    assert finding["disposition"] == "advisory"
    assert audit["action_required_count"] == 0


def test_failed_tool_is_advisory_when_same_arguments_later_succeed() -> None:
    args = {"symbol": "000682", "period": "daily"}
    audit = build_behavior_audit(
        _snapshot(
            [
                {
                    "action_id": "failed-1",
                    "tool_name": "read_recent_kline",
                    "success": False,
                    "arguments": args,
                    "errors": ["upstream timeout"],
                },
                {
                    "action_id": "recovered-1",
                    "tool_name": "read_recent_kline",
                    "success": True,
                    "arguments": args,
                    "result_count": 5,
                },
            ]
        )
    )

    finding = next(item for item in audit["findings"] if item["code"] == "tool_execution_failed")
    assert finding["disposition"] == "advisory"
    assert finding["severity"] == "info"
    assert "已恢复" in finding["title"]
    assert audit["status"] == "info"
    assert audit["action_required_count"] == 0


def test_reused_tool_observation_is_not_reported_as_repeated_model_call() -> None:
    args = {"symbol": "000682"}
    audit = build_behavior_audit(
        _snapshot(
            [
                {
                    "action_id": "call-1",
                    "tool_name": "read_realtime_quote",
                    "success": True,
                    "arguments": args,
                    "result_count": 1,
                },
                {
                    "action_id": "call-2",
                    "tool_name": "read_realtime_quote",
                    "success": True,
                    "arguments": args,
                    "result_count": 1,
                    "reused": True,
                },
            ]
        )
    )

    assert not any(item["code"] == "repeated_identical_tool_call" for item in audit["findings"])


def test_unknown_data_time_count_is_per_call_not_per_tool_name() -> None:
    audit = build_behavior_audit(
        _snapshot(
            [
                {
                    "action_id": "read-1",
                    "tool_name": "read_web_source",
                    "success": True,
                    "freshness_unknown": True,
                },
                {
                    "action_id": "read-2",
                    "tool_name": "read_web_source",
                    "success": True,
                    "freshness_unknown": True,
                },
            ]
        )
    )

    finding = next(item for item in audit["findings"] if item["code"] == "unknown_data_time")
    assert finding["detail"].startswith("2 个成功工具调用")
