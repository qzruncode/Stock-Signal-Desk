"""Run MiniRacer-backed AKShare calls in a fresh interpreter process.

The market-data service is independently deployable, so this module uses the
same one-shot process protocol as the agent tool runner while keeping the
AKShare boundary inside this service.  A native V8 crash therefore becomes a
failed source read instead of taking down a Celery worker.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any

import pandas as pd

from src.tools.process_runner import execute_process_isolated
from src.tools.process_worker import RESULT_PREFIX


# Keep this list aligned with the AKShare version in market_data_service's
# lockfile.  These are the MiniRacer-backed functions used by this service;
# ordinary AKShare calls remain in the parent worker for throughput.
MINI_RACER_AKSHARE_FUNCTIONS = frozenset(
    {
        "stock_cyq_em",
        "stock_hold_control_cninfo",
        "stock_profile_cninfo",
        "stock_zh_a_daily",
        "stock_zh_index_daily",
        "tool_trade_date_hist_sina",
    }
)

_WORKER_ARGUMENT = "--worker"


def _encode_result(result: Any) -> Any:
    if not isinstance(result, pd.DataFrame):
        return result
    return {
        "__kind__": "dataframe",
        "columns": [str(column) for column in result.columns],
        "records": json.loads(
            result.to_json(orient="records", date_format="iso", default_handler=str)
        ),
    }


def _decode_result(result: Any) -> Any:
    if not isinstance(result, dict) or result.get("__kind__") != "dataframe":
        return result
    return pd.DataFrame(
        result.get("records") or [],
        columns=result.get("columns") or None,
    )


def _deadline_seconds() -> float:
    from market_data_service.settings import get_settings

    return max(60.0, min(180.0, get_settings().request_timeout * 4))


def call_akshare_isolated(name: str, *args: Any, **kwargs: Any) -> Any:
    """Call one MiniRacer-backed AKShare function in a one-shot child process."""
    normalized = str(name or "").strip()
    if normalized not in MINI_RACER_AKSHARE_FUNCTIONS:
        raise ValueError(f"未登记的 MiniRacer AKShare 接口: {normalized}")
    result = execute_process_isolated(
        [sys.executable, "-m", __name__, _WORKER_ARGUMENT],
        {"name": normalized, "args": list(args), "kwargs": kwargs},
        result_prefix=RESULT_PREFIX,
        process_label=f"AKShare {normalized}",
        deadline_seconds=_deadline_seconds(),
    )
    return _decode_result(result)


def _worker_main() -> int:
    try:
        request = json.loads(sys.stdin.read() or "{}")
        name = str(request.get("name") or "").strip()
        if name not in MINI_RACER_AKSHARE_FUNCTIONS:
            raise ValueError(f"未登记的 MiniRacer AKShare 接口: {name}")
        args = request.get("args") or []
        kwargs = request.get("kwargs") or {}
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            raise TypeError("AKShare 子进程参数格式无效")

        import akshare as ak

        function = getattr(ak, name, None)
        if not callable(function):
            raise AttributeError(f"AKShare 未提供接口: {name}")
        result = function(*args, **kwargs)
        payload = {"ok": True, "result": _encode_result(result)}
    except Exception as exc:
        payload = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(limit=8),
        }
    sys.stdout.write(
        "\n"
        + RESULT_PREFIX
        + json.dumps(payload, ensure_ascii=False, default=str)
        + "\n"
    )
    sys.stdout.flush()
    return 0 if payload.get("ok") else 1


def _exit_after_result(exit_code: int) -> None:
    """Do not wait for provider-created helper threads during child teardown."""
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        os._exit(exit_code)


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] != _WORKER_ARGUMENT:
        raise SystemExit("market_data_service.akshare_isolation requires --worker")
    _exit_after_result(_worker_main())


__all__ = ["MINI_RACER_AKSHARE_FUNCTIONS", "call_akshare_isolated"]
