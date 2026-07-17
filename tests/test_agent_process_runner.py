from __future__ import annotations

import subprocess
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
