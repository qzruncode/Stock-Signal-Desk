# -*- coding: utf-8 -*-
"""Analysis report helper tests — push content + report builders.

Covers api.v1.endpoints.analysis.report pure helpers:
- _stringify_report_strategy_value
- _build_single_stock_push_content (conversation + report forms)
- _build_conversation_report (normal / empty response paths)
"""

from __future__ import annotations

from api.v1.endpoints.analysis.report import (
    _build_conversation_report,
    _build_single_stock_push_content,
    _stringify_report_strategy_value,
)


# ---------------------------------------------------------------------------
# _stringify_report_strategy_value
# ---------------------------------------------------------------------------


def test_stringify_none_returns_none():
    assert _stringify_report_strategy_value(None) is None


def test_stringify_string_returned_as_is():
    assert _stringify_report_strategy_value("10.5") == "10.5"


def test_stringify_non_string_coerced():
    assert _stringify_report_strategy_value(10.5) == "10.5"


# ---------------------------------------------------------------------------
# _build_single_stock_push_content
# ---------------------------------------------------------------------------


def test_push_content_conversation_form_includes_response_text():
    report_data = {
        "conversation": {"response": "看多，建议持有", "model_used": "gpt-test"},
    }
    content = _build_single_stock_push_content(report_data, "600519", "贵州茅台")
    assert "贵州茅台 (600519)" in content
    assert "看多，建议持有" in content
    assert "gpt-test" in content


def test_push_content_conversation_truncates_overlong_text():
    long_text = "x" * 4000
    report_data = {"conversation": {"response": long_text}}
    content = _build_single_stock_push_content(report_data, "000001", None)
    assert "..." in content
    assert len(long_text) > len(content)


def test_push_content_report_form_includes_score_and_strategy():
    report_data = {
        "report": {
            "meta": {"model_used": "gpt-test"},
            "summary": {
                "sentiment_score": 80,
                "operation_advice": "逢低买入",
                "trend_prediction": "震荡上行",
                "analysis_summary": "基本面良好",
            },
            "strategy": {
                "ideal_buy": "1800",
                "stop_loss": "1700",
                "take_profit": "2000",
            },
        }
    }
    content = _build_single_stock_push_content(report_data, "600519", "贵州茅台")
    assert "**80**" in content
    assert "逢低买入" in content
    assert "理想买入" in content
    assert "1800" in content


def test_push_content_no_strategy_omits_key_points_section():
    report_data = {
        "report": {
            "meta": {},
            "summary": {"sentiment_score": None},
            "strategy": {},
        }
    }
    content = _build_single_stock_push_content(report_data, "000001", None)
    assert "关键点位" not in content
    assert "N/A" in content  # score shown as N/A when None


def test_push_content_uses_stock_code_when_name_missing():
    report_data = {"conversation": {"response": "ok"}}
    content = _build_single_stock_push_content(report_data, "000001", None)
    assert "000001 (000001)" in content


# ---------------------------------------------------------------------------
# _build_conversation_report
# ---------------------------------------------------------------------------


def test_build_conversation_report_normal_response():
    conversation = {
        "response": "第一行\n第二行\n第三行\n第四行",
        "model_used": "gpt-test",
        "stock_name": "测试股票",
    }
    report = _build_conversation_report(
        conversation=conversation,
        query_id="q1",
        stock_code="000001",
    )
    assert report.meta.query_id == "q1"
    assert report.meta.stock_code == "000001"
    assert report.meta.stock_name == "测试股票"
    assert report.meta.report_type == "conversation"
    assert report.meta.model_used == "gpt-test"
    assert report.summary.analysis_summary == "第一行\n第二行\n第三行"
    assert report.details.news_content == conversation["response"]


def test_build_conversation_report_empty_response_yields_no_summary():
    report = _build_conversation_report(
        conversation={"response": "   \n  "},
        query_id="q2",
        stock_code="000002",
        stock_name="某股",
    )
    assert report.summary.analysis_summary is None
    assert report.details.news_content.strip() == ""


def test_build_conversation_report_truncates_long_brief():
    long_response = "\n".join([f"第{i}行" for i in range(200)])
    report = _build_conversation_report(
        conversation={"response": long_response},
        query_id="q3",
        stock_code="000003",
    )
    assert report.summary.analysis_summary is not None
    assert len(report.summary.analysis_summary) <= 500
