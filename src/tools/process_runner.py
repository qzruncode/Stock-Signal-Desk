"""Run one atomic native-risk tool in a cancellable child process."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from typing import Any

from src.tools.process_worker import RESULT_PREFIX


# This is a process-safety classification, not a business capability group.
# Every entry is still one registered atomic query.
ISOLATED_TOOL_NAMES = frozenset(
    {
        "read_market_breadth_sina",
        "read_market_indices_sina",
        "read_market_limit_up_pool_eastmoney",
        "read_market_limit_down_pool_eastmoney",
        "read_market_broken_board_pool_eastmoney",
        "read_sector_flow_eastmoney",
        "read_index_daily_history_sina",
        "read_index_quote_sina",
        "read_macro_indicator_akshare",
        "read_bond_yield_eastmoney",
    }
)


class ToolProcessTimeout(TimeoutError):
    """One isolated atomic call exceeded its application-owned deadline."""


def _terminate_process_group(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=0.75)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            try:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=0.75)
            except subprocess.TimeoutExpired:
                pass


def execute_process_isolated(
    command: Sequence[str],
    request: Mapping[str, Any],
    *,
    result_prefix: str = RESULT_PREFIX,
    process_label: str = "隔离",
    cancel_event: threading.Event | None = None,
    deadline_seconds: float | None = None,
) -> Any:
    """Run one JSON request in a fresh process and return its typed result.

    The command must write one line prefixed by ``result_prefix`` containing a
    JSON object with ``ok`` and ``result``/``error`` fields.  Keeping the
    lifecycle here lets other native-risk boundaries reuse the same timeout,
    process-group cleanup and crash-to-error behavior as agent tools.
    """
    request_text = json.dumps(dict(request), ensure_ascii=False, default=str)
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError(f"{process_label}执行已取消")

    with tempfile.TemporaryDirectory(prefix="dsa-isolated-run-") as temp_dir:
        root = Path(temp_dir)
        request_path = root / "request.json"
        stdout_path = root / "stdout.log"
        stderr_path = root / "stderr.log"
        request_path.write_text(request_text, encoding="utf-8")
        with (
            request_path.open("r", encoding="utf-8") as request_handle,
            stdout_path.open("w+", encoding="utf-8") as stdout_handle,
            stderr_path.open("w+", encoding="utf-8") as stderr_handle,
        ):
            process = subprocess.Popen(
                list(command),
                stdin=request_handle,
                stdout=stdout_handle,
                stderr=stderr_handle,
                text=True,
                start_new_session=os.name != "nt",
            )
            deadline_at = (
                time.monotonic() + max(0.1, float(deadline_seconds))
                if deadline_seconds is not None
                else None
            )
            try:
                while process.poll() is None:
                    if cancel_event is not None and cancel_event.is_set():
                        _terminate_process_group(process)
                        raise RuntimeError(f"{process_label}执行已取消")
                    if deadline_at is not None and time.monotonic() >= deadline_at:
                        _terminate_process_group(process)
                        raise ToolProcessTimeout(
                            f"{process_label}超过 {float(deadline_seconds):.1f} 秒截止时间"
                        )
                    if cancel_event is not None:
                        cancel_event.wait(timeout=0.05)
                    else:
                        time.sleep(0.05)
            except BaseException:
                _terminate_process_group(process)
                raise

            stdout_handle.flush()
            stderr_handle.flush()
            stdout_handle.seek(0)
            stderr_handle.seek(0)
            stdout = stdout_handle.read()
            stderr = stderr_handle.read()
            return_code = int(process.returncode or 0)

    marker = next(
        (
            line[len(result_prefix) :]
            for line in reversed(stdout.splitlines())
            if line.startswith(result_prefix)
        ),
        None,
    )
    if marker is None:
        tail = stderr.strip()[-800:]
        raise RuntimeError(
            f"{process_label}进程异常退出（code={return_code}）"
            + (f": {tail}" if tail else "")
        )
    payload = json.loads(marker)
    if not payload.get("ok"):
        raise RuntimeError(str(payload.get("error") or f"{process_label}执行失败"))
    return payload.get("result")


def execute_tool_isolated(
    name: str,
    arguments: dict[str, Any],
    *,
    cancel_event: threading.Event | None = None,
    deadline_seconds: float | None = None,
    idempotency_key: str | None = None,
    execution_context: dict[str, str | None] | None = None,
    effect_approved: bool = False,
) -> Any:
    """Execute exactly one registry tool in a one-shot worker process."""
    if not isinstance(arguments, dict):
        raise TypeError("tool arguments must be an object")
    command = [sys.executable, "-m", "src.tools.process_worker"]
    worker_key = (
        hashlib.sha256(
            json.dumps(
                {
                    "parent": idempotency_key,
                    "tool": name,
                    "arguments": arguments,
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        if idempotency_key
        else None
    )
    request = {
        "name": name,
        "arguments": arguments,
        "idempotency_key": worker_key,
        "execution_context": execution_context or {},
        # This value is authored only by the server-side graph policy. It
        # is never accepted from a model-visible tool schema or HTTP tool
        # execution endpoint.
        "effect_approved": bool(effect_approved),
    }
    return execute_process_isolated(
        command,
        request,
        cancel_event=cancel_event,
        deadline_seconds=deadline_seconds,
        process_label="隔离工具",
    )


__all__ = [
    "ISOLATED_TOOL_NAMES",
    "ToolProcessTimeout",
    "execute_process_isolated",
    "execute_tool_isolated",
]
