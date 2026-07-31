from __future__ import annotations

import json
from io import StringIO
import signal
import subprocess
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from src.tools.process_runner import execute_tool_isolated
from src.tools.base import (
    current_tool_idempotency_key,
    tool_idempotency_context,
)
from src.tools.process_worker import _exit_after_result, main as process_worker_main


def test_isolated_runner_parses_structured_result_after_noisy_stdout():
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout='progress\n__DSA_TOOL_RESULT__={"ok":true,"result":{"success":true}}\n',
        stderr="",
    )
    with patch("src.tools.process_runner.subprocess.run", return_value=completed):
        assert execute_tool_isolated("get_market_status", {}) == {"success": True}


def test_isolated_runner_forwards_a_stable_scoped_idempotency_key():
    forwarded_keys: list[str] = []

    def complete(_command, **kwargs):
        request = json.loads(kwargs["input"])
        forwarded_keys.append(request["idempotency_key"])
        assert request["idempotency_key"] != "durable-step-key"
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='__DSA_TOOL_RESULT__={"ok":true,"result":{"success":true}}\n',
            stderr="",
        )

    with patch("src.tools.process_runner.subprocess.run", side_effect=complete):
        execute_tool_isolated(
            "get_market_status",
            {"market": "A股"},
            idempotency_key="durable-step-key",
        )
        execute_tool_isolated(
            "get_market_status",
            {"market": "A股"},
            idempotency_key="durable-step-key",
        )

    assert len(forwarded_keys[0]) == 64
    assert forwarded_keys[0] == forwarded_keys[1]


def test_process_worker_exposes_and_resets_idempotency_context():
    request_stream = StringIO(
        json.dumps(
            {
                "name": "get_market_status",
                "arguments": {},
                "idempotency_key": "worker-key",
            }
        )
    )
    response_stream = StringIO()

    with (
        patch("src.tools.process_worker.sys.stdin", request_stream),
        patch(
            "src.tools.process_worker.sys.stdout",
            response_stream,
        ),
        patch(
            "src.tools.registry.ToolRegistry.execute",
            side_effect=lambda *_args, **_kwargs: {
                "success": True,
                "key": current_tool_idempotency_key(),
            },
        ),
    ):
        assert process_worker_main() == 0

    assert '"key": "worker-key"' in response_stream.getvalue()
    assert current_tool_idempotency_key() is None
    with tool_idempotency_context("parent-key"):
        assert current_tool_idempotency_key() == "parent-key"
    assert current_tool_idempotency_key() is None


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


def test_one_shot_worker_exits_without_interpreter_thread_finalization():
    stdout = MagicMock()
    stderr = MagicMock()
    with (
        patch("src.tools.process_worker.sys.stdout", stdout),
        patch(
            "src.tools.process_worker.sys.stderr",
            stderr,
        ),
        patch(
            "src.tools.process_worker.os._exit",
            side_effect=SystemExit(7),
        ) as immediate_exit,
    ):
        with pytest.raises(SystemExit) as captured:
            _exit_after_result(7)

    assert captured.value.code == 7
    stdout.flush.assert_called_once_with()
    stderr.flush.assert_called_once_with()
    immediate_exit.assert_called_once_with(7)


def test_isolated_runner_terminates_process_group_when_cancelled():
    cancel_event = threading.Event()

    class BlockingProcess:
        pid = 4242
        returncode = None

        def poll(self):
            return self.returncode

        def communicate(self, *, input=None, timeout=None):
            del input, timeout
            time.sleep(0.01)
            raise subprocess.TimeoutExpired(cmd=[], timeout=0.01)

        def wait(self, timeout=None):
            del timeout
            self.returncode = -signal.SIGTERM
            return self.returncode

    timer = threading.Timer(0.03, cancel_event.set)
    timer.start()
    try:
        with (
            patch(
                "src.tools.process_runner.subprocess.Popen",
                return_value=BlockingProcess(),
            ),
            patch("src.tools.process_runner.os.killpg") as kill_group,
        ):
            with pytest.raises(RuntimeError, match="已取消"):
                execute_tool_isolated(
                    "get_market_status",
                    {},
                    cancel_event=cancel_event,
                )
    finally:
        timer.cancel()

    kill_group.assert_called_once_with(4242, signal.SIGTERM)


def test_cancel_aware_runner_transports_large_request_and_result_without_pipes():
    request_blob = "请求" * 12_000
    result_blob = "结果" * 12_000

    class CompletedFileProcess:
        pid = 4343
        returncode = 0

        def __init__(self, _command, *, stdin, stdout, stderr, **kwargs):
            del stderr, kwargs
            request = json.load(stdin)
            assert request["arguments"]["blob"] == request_blob
            stdout.write(
                "__DSA_TOOL_RESULT__="
                + json.dumps(
                    {
                        "ok": True,
                        "result": {
                            "success": True,
                            "blob": result_blob,
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            stdout.flush()

        def poll(self):
            return self.returncode

    with patch(
        "src.tools.process_runner.subprocess.Popen",
        side_effect=CompletedFileProcess,
    ):
        result = execute_tool_isolated(
            "get_market_status",
            {"blob": request_blob},
            cancel_event=threading.Event(),
        )

    assert result == {
        "success": True,
        "blob": result_blob,
    }


def test_professional_evidence_is_split_into_parallel_two_stock_shards():
    calls: list[str] = []
    lock = threading.Lock()

    def run_process(name, arguments, **kwargs):
        assert "timeout_seconds" not in kwargs
        assert name == "get_multi_stock_decision_evidence"
        symbols = arguments["symbols"]
        with lock:
            calls.append(symbols)
        items = [
            {"symbol": symbol, "name": symbol, "evidence_coverage": {"complete": True}} for symbol in symbols.split(",")
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
        )

    assert sorted(calls) == ["000001,000002", "000003,000004", "000005"]
    assert [item["symbol"] for item in result["items"]] == [
        "000001",
        "000002",
        "000003",
        "000004",
        "000005",
    ]
    assert result["total"] == 5
    assert result["partial"] is False


def test_professional_evidence_keeps_snapshot_when_one_shard_fails():
    def run_process(name, arguments, **kwargs):
        assert "timeout_seconds" not in kwargs
        if name == "get_multi_stock_decision_evidence" and arguments["symbols"] == "000003,000004":
            raise RuntimeError("upstream failed")
        if name == "get_multi_stock_snapshot":
            return {
                "success": True,
                "items": [
                    {"symbol": symbol, "name": symbol, "quote": {"price": 10}}
                    for symbol in arguments["symbols"].split(",")
                ],
                "resolved_entities": [{"symbol": symbol, "name": symbol} for symbol in arguments["symbols"].split(",")],
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
        )

    assert result["success"] is True
    assert result["partial"] is True
    assert result["fallback_used"] is True
    assert [item["symbol"] for item in result["items"]] == [
        "000001",
        "000002",
        "000003",
        "000004",
    ]
    fallback_items = [item for item in result["items"] if item["symbol"] in {"000003", "000004"}]
    assert all(item["snapshot"]["quote"]["price"] == 10 for item in fallback_items)
    assert any("000003,000004" in error for error in result["errors"])


def test_two_stock_professional_evidence_falls_back_after_real_failure():
    def run_process(name, arguments, **kwargs):
        assert "timeout_seconds" not in kwargs
        if name == "get_multi_stock_decision_evidence":
            raise RuntimeError("upstream failed")
        assert name == "get_multi_stock_snapshot"
        return {
            "success": True,
            "items": [
                {"symbol": symbol, "name": symbol, "quote": {"price": 10}} for symbol in arguments["symbols"].split(",")
            ],
            "resolved_entities": [],
            "unresolved_entities": [],
        }

    with patch("src.tools.process_runner._execute_tool_process", side_effect=run_process):
        result = execute_tool_isolated(
            "get_multi_stock_decision_evidence",
            {"symbols": "000001,000002", "thesis": "测试"},
        )

    assert result["success"] is True
    assert result["partial"] is True
    assert result["fallback_used"] is True
    assert [item["symbol"] for item in result["items"]] == ["000001", "000002"]


def test_professional_buy_analysis_isolates_each_stock_preserves_order_and_failure():
    calls: list[str] = []
    lock = threading.Lock()

    def run_process(name, arguments, **kwargs):
        assert "timeout_seconds" not in kwargs
        assert name == "evaluate_multi_stock_buy_criteria"
        symbol = arguments["symbols"]
        with lock:
            calls.append(symbol)
        if symbol == "000002":
            raise RuntimeError("model failed")
        return {
            "success": True,
            "partial": False,
            "playbook": "professional_eight_dimension_boolean_gate",
            "items": [
                {
                    "symbol": symbol,
                    "name": symbol,
                    "analysis_mode": "professional_eight_dimension_boolean_gate",
                    "decision_code": "buy",
                    "decision": "可买入",
                    "dimensions": [
                        {"id": dimension, "status": "pass"}
                        for dimension in (
                            "market_mainline",
                            "industrial_competitiveness",
                            "industry_cycle",
                            "competition_quality",
                            "growth_drivers",
                            "forward_catalysts",
                            "valuation_odds",
                            "major_risks",
                        )
                    ],
                }
            ],
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
        )

    assert sorted(calls) == ["000001", "000002", "000003"]
    assert [item["symbol"] for item in result["items"]] == ["000001", "000002", "000003"]
    failed = result["items"][1]
    assert failed["final_decision"] == "分析失败"
    assert failed["analysis_status"] == "execution_failed"
    assert failed["insufficient_count"] == 0
    assert failed["not_evaluated_count"] == 8
    assert failed["criteria"] == []
    assert result["partial"] is True
    assert result["coverage_complete"] is True
