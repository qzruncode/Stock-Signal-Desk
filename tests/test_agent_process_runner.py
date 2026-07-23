from __future__ import annotations

import subprocess
import threading
from unittest.mock import patch

import pytest

from src.tools.process_runner import execute_tool_isolated


def test_isolated_runner_parses_structured_result_after_noisy_stdout():
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout='progress\n__DSA_TOOL_RESULT__={"ok":true,"result":{"success":true}}\n',
        stderr="",
    )
    with patch("src.tools.process_runner.subprocess.run", return_value=completed):
        assert execute_tool_isolated("get_market_status", {}) == {"success": True}


def test_isolated_runner_turns_native_abort_into_regular_error():
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=-6,
        stdout="",
        stderr="FATAL libmini_racer abort",
    )
    with patch("src.tools.process_runner.subprocess.run", return_value=completed):
        with pytest.raises(RuntimeError, match="隔离工具进程异常退出"):
            execute_tool_isolated("get_market_status", {})


def test_professional_evidence_is_split_into_parallel_two_stock_shards():
    calls: list[str] = []
    lock = threading.Lock()

    def run_process(name, arguments, *, timeout_seconds):
        assert name == "get_multi_stock_decision_evidence"
        symbols = arguments["symbols"]
        with lock:
            calls.append(symbols)
        items = [
            {"symbol": symbol, "name": symbol, "evidence_coverage": {"complete": True}}
            for symbol in symbols.split(",")
        ]
        return {
            "success": True,
            "partial": False,
            "items": items,
            "resolved_entities": [{"symbol": item["symbol"]} for item in items],
            "unresolved_entities": [],
            "total": len(items),
            "errors": [],
            "warnings": [],
        }

    with patch("src.tools.process_runner._execute_tool_process", side_effect=run_process):
        result = execute_tool_isolated(
            "get_multi_stock_decision_evidence",
            {"symbols": "000001,000002,000003,000004,000005", "thesis": "测试"},
            timeout_seconds=87,
        )

    assert sorted(calls) == ["000001,000002", "000003,000004", "000005"]
    assert [item["symbol"] for item in result["items"]] == [
        "000001", "000002", "000003", "000004", "000005",
    ]
    assert result["total"] == 5
    assert result["partial"] is False


def test_professional_evidence_keeps_snapshot_when_one_shard_times_out():
    def run_process(name, arguments, *, timeout_seconds):
        if name == "get_multi_stock_decision_evidence" and arguments["symbols"] == "000003,000004":
            raise TimeoutError("upstream stalled")
        if name == "get_multi_stock_snapshot":
            return {
                "success": True,
                "items": [
                    {"symbol": symbol, "name": symbol, "quote": {"price": 10}}
                    for symbol in arguments["symbols"].split(",")
                ],
                "resolved_entities": [
                    {"symbol": symbol, "name": symbol}
                    for symbol in arguments["symbols"].split(",")
                ],
                "unresolved_entities": [],
            }
        items = [
            {"symbol": symbol, "name": symbol, "evidence_coverage": {"complete": True}}
            for symbol in arguments["symbols"].split(",")
        ]
        return {
            "success": True,
            "partial": False,
            "items": items,
            "resolved_entities": [{"symbol": item["symbol"]} for item in items],
            "unresolved_entities": [],
            "total": len(items),
            "errors": [],
            "warnings": [],
        }

    with patch("src.tools.process_runner._execute_tool_process", side_effect=run_process):
        result = execute_tool_isolated(
            "get_multi_stock_decision_evidence",
            {"symbols": "000001,000002,000003,000004", "thesis": "测试"},
            timeout_seconds=87,
        )

    assert result["success"] is True
    assert result["partial"] is True
    assert result["fallback_used"] is True
    assert [item["symbol"] for item in result["items"]] == [
        "000001", "000002", "000003", "000004",
    ]
    fallback_items = [item for item in result["items"] if item["symbol"] in {"000003", "000004"}]
    assert all(item["snapshot"]["quote"]["price"] == 10 for item in fallback_items)
    assert any("000003,000004" in error for error in result["errors"])


def test_two_stock_professional_evidence_also_falls_back_before_deadline():
    def run_process(name, arguments, *, timeout_seconds):
        del timeout_seconds
        if name == "get_multi_stock_decision_evidence":
            raise TimeoutError("upstream stalled")
        assert name == "get_multi_stock_snapshot"
        return {
            "success": True,
            "items": [
                {"symbol": symbol, "name": symbol, "quote": {"price": 10}}
                for symbol in arguments["symbols"].split(",")
            ],
            "resolved_entities": [],
            "unresolved_entities": [],
        }

    with patch("src.tools.process_runner._execute_tool_process", side_effect=run_process):
        result = execute_tool_isolated(
            "get_multi_stock_decision_evidence",
            {"symbols": "000001,000002", "thesis": "测试"},
            timeout_seconds=87,
        )

    assert result["success"] is True
    assert result["partial"] is True
    assert result["fallback_used"] is True
    assert [item["symbol"] for item in result["items"]] == ["000001", "000002"]


def test_professional_buy_analysis_isolates_each_stock_preserves_order_and_failure():
    calls: list[str] = []
    lock = threading.Lock()

    def run_process(name, arguments, *, timeout_seconds):
        del timeout_seconds
        assert name == "evaluate_multi_stock_buy_criteria"
        symbol = arguments["symbols"]
        with lock:
            calls.append(symbol)
        if symbol == "000002":
            raise TimeoutError("model stalled")
        return {
            "success": True,
            "partial": False,
            "playbook": "professional_eight_dimension_buy_analysis",
            "items": [{
                "symbol": symbol,
                "name": symbol,
                "analysis_mode": "professional_eight_dimension_buy_analysis",
                "recommendation_code": "watchlist",
                "recommendation": "进入中期跟踪池",
                "coverage_complete": True,
                "dimensions": [],
            }],
            "resolved_entities": [{"symbol": symbol, "name": symbol}],
            "unresolved_entities": [],
            "requested_count": 1,
            "covered_count": 1,
            "coverage_complete": True,
            "errors": [],
            "warnings": [],
        }

    with patch("src.tools.process_runner._execute_tool_process", side_effect=run_process):
        result = execute_tool_isolated(
            "evaluate_multi_stock_buy_criteria",
            {"symbols": "000001,000002,000003", "thesis": "测试"},
            timeout_seconds=297,
        )

    assert sorted(calls) == ["000001", "000002", "000003"]
    assert [item["symbol"] for item in result["items"]] == ["000001", "000002", "000003"]
    failed = result["items"][1]
    assert failed["recommendation_code"] == "evidence_insufficient"
    assert failed["recommendation"] == "证据不足，暂停判断"
    assert failed["counts"]["insufficient"] == 8
    assert result["partial"] is True
    assert result["coverage_complete"] is True
