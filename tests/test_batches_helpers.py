# -*- coding: utf-8 -*-
"""Batches helper tests — JSON parsing, resume code resolution, partial report.

Covers api.v1.endpoints.batches.helpers pure helpers:
- _parse_results_json / _parse_stock_codes_json
- _resolve_resume_stock_codes / _resolve_auto_resume_stock_codes
- _filter_results_for_stock_codes
- _parse_started_at
- _build_partial_report_from_run
- _is_batch_running / _mark_batch_stopped / _set_running_status
"""

from __future__ import annotations

import json
from unittest.mock import patch

from api.v1.endpoints.batches import helpers as h


# ---------------------------------------------------------------------------
# JSON parsers
# ---------------------------------------------------------------------------


def test_parse_results_json_returns_empty_for_invalid():
    assert h._parse_results_json(None) == {}
    assert h._parse_results_json("not-json") == {}
    assert h._parse_results_json("[]") == {}


def test_parse_results_json_filters_non_dict_and_all_key():
    raw = json.dumps({"000001": {"success": True}, "__all__": {"x": 1}, "bad": "str"})
    out = h._parse_results_json(raw)
    assert out == {"000001": {"success": True}}


def test_parse_stock_codes_json_returns_cleaned_list():
    raw = json.dumps([" 000001 ", "", "600519"])
    assert h._parse_stock_codes_json(raw) == ["000001", "600519"]


def test_parse_stock_codes_json_invalid_returns_empty():
    assert h._parse_stock_codes_json("not-json") == []
    assert h._parse_stock_codes_json("{}") == []


# ---------------------------------------------------------------------------
# resume code resolution
# ---------------------------------------------------------------------------


def test_resolve_resume_stock_codes_prefers_stored_codes():
    run = {"stock_codes_json": json.dumps(["000001", "600519"])}
    assert h._resolve_resume_stock_codes(run, ["fallback"]) == ["000001", "600519"]


def test_resolve_resume_stock_codes_falls_back_to_argument():
    run = {"stock_codes_json": "[]"}
    assert h._resolve_resume_stock_codes(run, ["sh600519", " 000001 "]) == ["sh600519", "000001"]


def test_resolve_auto_resume_returns_empty_when_no_partial_progress():
    run = {
        "results_json": "{}",
        "stock_count": 3,
        "stock_codes_json": json.dumps(["000001"]),
    }
    assert h._resolve_auto_resume_stock_codes(run) == []


def test_resolve_auto_resume_returns_empty_when_all_done():
    run = {
        "results_json": json.dumps(
            {
                "000001": {"success": True},
                "600519": {"success": True},
            }
        ),
        "stock_count": 2,
        "stock_codes_json": json.dumps(["000001", "600519"]),
    }
    assert h._resolve_auto_resume_stock_codes(run) == []


def test_resolve_auto_resume_returns_stored_codes_when_partial():
    run = {
        "results_json": json.dumps({"000001": {"success": True}}),
        "stock_count": 2,
        "stock_codes_json": json.dumps(["000001", "600519"]),
    }
    assert h._resolve_auto_resume_stock_codes(run) == ["000001", "600519"]


def test_resolve_auto_resume_falls_back_to_config_codes():
    fake_config = type("C", (), {"stock_list": ["000001", "600519"]})()
    run = {
        "results_json": json.dumps({"000001": {"success": True}}),
        "stock_count": 2,
        "stock_codes_json": "[]",
    }
    with patch("api.v1.endpoints.batches.helpers.get_config", return_value=fake_config):
        assert h._resolve_auto_resume_stock_codes(run) == ["000001", "600519"]


# ---------------------------------------------------------------------------
# filter / parse_started_at
# ---------------------------------------------------------------------------


def test_filter_results_for_stock_codes_keeps_only_requested():
    results = {"000001": {"x": 1}, "600519": {"x": 2}, "999": {"x": 3}}
    out = h._filter_results_for_stock_codes(results, ["000001", "600519"])
    assert out == {"000001": {"x": 1}, "600519": {"x": 2}}


def test_parse_started_at_handles_none_and_invalid():
    assert h._parse_started_at(None) is None
    assert h._parse_started_at("not-a-date") is None


def test_parse_started_at_parses_iso():
    from datetime import datetime

    assert h._parse_started_at("2026-06-01T10:00:00") == datetime(2026, 6, 1, 10, 0, 0)


# ---------------------------------------------------------------------------
# _build_partial_report_from_run
# ---------------------------------------------------------------------------


def test_build_partial_report_empty_results_returns_empty():
    assert h._build_partial_report_from_run({"results_json": "{}"}) == ""


def test_build_partial_report_invalid_json_returns_empty():
    assert h._build_partial_report_from_run({"results_json": "bad"}) == ""


def test_build_partial_report_includes_success_and_failure_sections():
    run = {
        "results_json": json.dumps(
            {
                "000001": {"success": True, "text": "ok content", "model": "gpt"},
                "600519": {"success": False, "text": "boom"},
            }
        ),
        "started_at": "2026-06-01T10:00:00",
        "template_name": "T",
        "stock_count": 2,
        "success_count": 1,
        "fail_count": 1,
    }
    report = h._build_partial_report_from_run(run)
    assert "批量分析报告（部分结果）" in report
    assert "000001" in report
    assert "ok content" in report
    assert "gpt" in report
    assert "600519" in report
    assert "boom" in report


# ---------------------------------------------------------------------------
# running state helpers
# ---------------------------------------------------------------------------


def test_is_batch_running_false_when_no_state():
    h._running_batch = None
    assert h._is_batch_running() is False


def test_mark_batch_stopped_sets_running_false():
    h._running_batch = {"running": True, "state": None}
    h._mark_batch_stopped()
    assert h._running_batch["running"] is False


def test_set_running_status_updates_state_dict():
    h._running_batch = {
        "running": True,
        "state": {"status": "running", "paused": False, "stopping": False, "current_message": ""},
    }
    h._set_running_status("paused", "已暂停")
    assert h._running_batch["state"]["status"] == "paused"
    assert h._running_batch["state"]["paused"] is True
    assert h._running_batch["state"]["current_message"] == "已暂停"


def test_set_running_status_noop_when_no_running_batch():
    h._running_batch = None
    # should not raise
    h._set_running_status("paused", "x")


def test_persist_current_status_skips_when_no_run_id():
    h._running_batch = {"state": {"run_id": None}}
    # should not raise or call db
    h._persist_current_status("paused")
