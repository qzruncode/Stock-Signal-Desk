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
