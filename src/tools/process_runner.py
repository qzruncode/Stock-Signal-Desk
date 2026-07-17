"""Run native-risk Agent tools outside the FastAPI worker process."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from src.tools.process_worker import RESULT_PREFIX


ISOLATED_TOOL_NAMES = frozenset({
    "get_multi_stock_snapshot",
    "get_multi_stock_decision_evidence",
    "get_theme_stock_candidates",
    "get_market_status",
    "get_market_breadth",
    "get_sector_list",
    "get_sector_flow",
    "get_index_data",
    "get_macro_indicator",
    "get_bond_yield",
    "get_monetary_policy_operations",
})


def execute_tool_isolated(
    name: str,
    arguments: dict[str, Any],
    *,
    timeout_seconds: float = 40.0,
) -> Any:
    """Execute a tool in a one-shot Python process and return its result.

    A native abort (for example libmini_racer/V8) never raises a Python
    exception in the crashing process.  Process isolation turns that abort
    into a normal non-zero exit code that the Agent can render as a tool error.
    """
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "src.tools.process_worker"],
            input=json.dumps({"name": name, "arguments": arguments}, ensure_ascii=False),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=max(1.0, timeout_seconds),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"隔离工具执行超时（>{timeout_seconds:.0f}s）") from exc

    marker_line = next(
        (
            line[len(RESULT_PREFIX):]
            for line in reversed(completed.stdout.splitlines())
            if line.startswith(RESULT_PREFIX)
        ),
        None,
    )
    if marker_line is None:
        stderr_tail = completed.stderr.strip()[-800:]
        raise RuntimeError(
            f"隔离工具进程异常退出（code={completed.returncode}）"
            + (f": {stderr_tail}" if stderr_tail else "")
        )
    payload = json.loads(marker_line)
    if not payload.get("ok"):
        raise RuntimeError(str(payload.get("error") or "隔离工具执行失败"))
    return payload.get("result")


__all__ = ["ISOLATED_TOOL_NAMES", "execute_tool_isolated"]
