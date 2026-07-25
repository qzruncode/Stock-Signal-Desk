"""Run native-risk Agent tools outside the FastAPI worker process."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from src.services.buy_criteria.professional_analysis import (
    DIMENSION_DEFINITIONS,
    PROFESSIONAL_BUY_ANALYSIS_MODE,
    PROFESSIONAL_BUY_CONTRACT_VERSION,
)
from src.tools.process_worker import RESULT_PREFIX


ISOLATED_TOOL_NAMES = frozenset({
    "get_multi_stock_snapshot",
    "get_multi_stock_decision_evidence",
    "evaluate_multi_stock_buy_criteria",
    "analyze_stock_catalysts",
    "get_domain_stock_candidates",
    "get_market_status",
    "get_market_breadth",
    "get_sector_list",
    "get_sector_flow",
    "get_index_data",
    "get_macro_indicator",
    "get_bond_yield",
    "get_monetary_policy_operations",
})

# These tools create or control process-owned task queues/threads. Executing
# them in the one-shot safety worker would destroy the task as soon as the
# worker exits. They still run off the asyncio event loop in a thread.
STATEFUL_TOOL_NAMES = frozenset({
    "run_stock_analysis",
    "get_analysis_status",
    "run_batch_analysis",
    "manage_batch_run",
    "manage_analysis_schedule",
})

_PROFESSIONAL_EVIDENCE_TOOL = "get_multi_stock_decision_evidence"
_PROFESSIONAL_EVIDENCE_CHUNK_SIZE = 2
_PROFESSIONAL_BUY_ANALYSIS_TOOL = "evaluate_multi_stock_buy_criteria"
def _professional_buy_stock_concurrency() -> int:
    try:
        return max(
            1,
            min(
                4,
                int(os.getenv("PROFESSIONAL_BUY_STOCK_CONCURRENCY", "4")),
            ),
        )
    except (TypeError, ValueError):
        return 4


def _execute_tool_process(
    name: str,
    arguments: dict[str, Any],
    *,
    timeout_seconds: float,
    cancel_event: threading.Event | None = None,
) -> Any:
    """Execute exactly one worker process without higher-level fan-out."""
    command = [sys.executable, "-m", "src.tools.process_worker"]
    input_text = json.dumps(
        {"name": name, "arguments": arguments},
        ensure_ascii=False,
    )
    if cancel_event is None:
        try:
            completed = subprocess.run(
                command,
                input=input_text,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=max(1.0, timeout_seconds),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                f"隔离工具执行超时（>{timeout_seconds:.0f}s）"
            ) from exc
    else:
        if cancel_event.is_set():
            raise RuntimeError("隔离工具执行已取消")
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=os.name != "nt",
        )

        def terminate_process_group() -> None:
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

        deadline = time.monotonic() + max(1.0, timeout_seconds)
        first_communication = True
        while True:
            if cancel_event.is_set():
                terminate_process_group()
                raise RuntimeError("隔离工具执行已取消")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                terminate_process_group()
                raise TimeoutError(
                    f"隔离工具执行超时（>{timeout_seconds:.0f}s）"
                )
            try:
                stdout, stderr = process.communicate(
                    input=input_text if first_communication else None,
                    timeout=min(0.25, remaining),
                )
                completed = subprocess.CompletedProcess(
                    args=command,
                    returncode=int(process.returncode or 0),
                    stdout=stdout,
                    stderr=stderr,
                )
                break
            except subprocess.TimeoutExpired:
                first_communication = False

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


def _snapshot_fallback_for_professional_chunk(
    symbols: str,
    thesis: str,
    error: Exception,
    *,
    timeout_seconds: float,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Preserve every requested company when one deep-evidence shard stalls."""
    process_options: dict[str, Any] = {"timeout_seconds": timeout_seconds}
    if cancel_event is not None:
        process_options["cancel_event"] = cancel_event
    snapshot = _execute_tool_process(
        "get_multi_stock_snapshot",
        {"symbols": symbols},
        **process_options,
    )
    missing_dimensions = [
        "business_reality", "financial_quality", "valuation", "expectations",
        "peer_context", "trading_state", "catalyst_and_risk",
    ]
    items = []
    for source_item in snapshot.get("items") or []:
        if not isinstance(source_item, dict):
            continue
        items.append({
            "symbol": source_item.get("symbol"),
            "name": source_item.get("name"),
            "thesis": thesis or None,
            "snapshot": source_item,
            "evidence_coverage": {
                "dimensions": {name: False for name in missing_dimensions},
                "complete_count": 0,
                "required_count": len(missing_dimensions),
                "missing": missing_dimensions,
                "complete": False,
            },
        })
    message = f"专业证据分片 {symbols} 未在时限内完成，已保留多股快照：{error}"
    return {
        "success": bool(items),
        "partial": True,
        "fallback_used": True,
        "playbook": "professional_investment_decision",
        "thesis": thesis or None,
        "items": items,
        "resolved_entities": snapshot.get("resolved_entities") or [],
        "unresolved_entities": snapshot.get("unresolved_entities") or [],
        "total": len(items),
        "data_time": snapshot.get("data_time"),
        "quote_basis": snapshot.get("quote_basis"),
        "quote_is_intraday": snapshot.get("quote_is_intraday"),
        "source": {"snapshot": snapshot.get("source")},
        "evidence_standard": missing_dimensions,
        "errors": [message],
        "warnings": [message],
    }


def _merge_professional_chunks(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    first = chunks[0]
    items = [item for chunk in chunks for item in (chunk.get("items") or [])]
    errors = [error for chunk in chunks for error in (chunk.get("errors") or [])]
    warnings = [warning for chunk in chunks for warning in (chunk.get("warnings") or [])]
    unresolved = [
        entity
        for chunk in chunks
        for entity in (chunk.get("unresolved_entities") or [])
    ]
    return {
        **first,
        "success": bool(items),
        "partial": any(bool(chunk.get("partial")) for chunk in chunks) or bool(errors),
        "fallback_used": any(bool(chunk.get("fallback_used")) for chunk in chunks),
        "items": items,
        "resolved_entities": [
            entity
            for chunk in chunks
            for entity in (chunk.get("resolved_entities") or [])
        ],
        "unresolved_entities": unresolved,
        "total": len(items),
        "errors": errors,
        "warnings": warnings,
    }


def _execute_professional_evidence_chunked(
    arguments: dict[str, Any],
    *,
    timeout_seconds: float,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    symbols = [part.strip() for part in str(arguments.get("symbols") or "").split(",") if part.strip()]
    if not symbols:
        process_options: dict[str, Any] = {
            "timeout_seconds": timeout_seconds,
        }
        if cancel_event is not None:
            process_options["cancel_event"] = cancel_event
        return _execute_tool_process(
            _PROFESSIONAL_EVIDENCE_TOOL,
            arguments,
            **process_options,
        )
    symbol_chunks = [
        symbols[index:index + _PROFESSIONAL_EVIDENCE_CHUNK_SIZE]
        for index in range(0, len(symbols), _PROFESSIONAL_EVIDENCE_CHUNK_SIZE)
    ]
    # Reserve time for the snapshot fallback even when only one two-stock
    # shard was requested.  Previously the <=2 path consumed the entire
    # envelope in the deep worker and returned a hard timeout instead of the
    # documented partial snapshot.
    deep_timeout = min(max(10.0, timeout_seconds), 65.0)
    fallback_timeout = max(8.0, timeout_seconds - deep_timeout - 3.0)
    thesis = str(arguments.get("thesis") or "").strip()

    def run_chunk(index: int, chunk: list[str]) -> tuple[int, dict[str, Any]]:
        chunk_symbols = ",".join(chunk)
        chunk_arguments = {**arguments, "symbols": chunk_symbols}
        process_options: dict[str, Any] = {
            "timeout_seconds": deep_timeout,
        }
        if cancel_event is not None:
            process_options["cancel_event"] = cancel_event
        try:
            result = _execute_tool_process(
                _PROFESSIONAL_EVIDENCE_TOOL,
                chunk_arguments,
                **process_options,
            )
        except Exception as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise
            result = _snapshot_fallback_for_professional_chunk(
                chunk_symbols,
                thesis,
                exc,
                timeout_seconds=fallback_timeout,
                cancel_event=cancel_event,
            )
        return index, result

    ordered: list[dict[str, Any] | None] = [None] * len(symbol_chunks)
    with ThreadPoolExecutor(max_workers=len(symbol_chunks)) as pool:
        futures = [pool.submit(run_chunk, index, chunk) for index, chunk in enumerate(symbol_chunks)]
        for future in as_completed(futures):
            index, result = future.result()
            ordered[index] = result
    return _merge_professional_chunks([chunk for chunk in ordered if isinstance(chunk, dict)])


def _professional_buy_failure_item(entity: str, error: Exception) -> dict[str, Any]:
    """Turn one failed worker into an explicit evidence-insufficient result."""
    message = f"{entity}专业买入分析进程失败：{type(error).__name__}: {str(error)[:240]}"
    first_id, first_name = DIMENSION_DEFINITIONS[0]
    blocking = {
        "criterion_id": first_id,
        "criterion_name": first_name,
        "index": 0,
        "passed": False,
        "status": "insufficient",
        "confidence": "",
        "verdict": message,
        "details": {},
    }
    return {
        "success": True,
        "partial": True,
        "contract_version": PROFESSIONAL_BUY_CONTRACT_VERSION,
        "playbook": PROFESSIONAL_BUY_ANALYSIS_MODE,
        "items": [{
            "symbol": entity,
            "name": entity,
            "analysis_mode": PROFESSIONAL_BUY_ANALYSIS_MODE,
            "final_decision": "不可买入",
            "gate_pass_complete": False,
            "coverage_complete": False,
            "passed_count": 0,
            "failed_count": 0,
            "insufficient_count": 1,
            "not_evaluated_count": len(DIMENSION_DEFINITIONS) - 1,
            "total": len(DIMENSION_DEFINITIONS),
            "stopped_at": first_id,
            "stopped_at_name": first_name,
            "stopped_verdict": message,
            "blocking_reasons": [blocking],
            "criteria": [blocking],
        }],
        "resolved_entities": [],
        "unresolved_entities": [],
        "requested_count": 1,
        "covered_count": 1,
        "coverage_complete": True,
        "errors": [message],
        "warnings": [],
        "data_time": None,
        "is_stale": None,
    }


def _merge_professional_buy_chunks(
    chunks: list[dict[str, Any]],
    requested_count: int,
) -> dict[str, Any]:
    first = chunks[0] if chunks else {}
    items = [item for chunk in chunks for item in (chunk.get("items") or [])]
    resolved = [item for chunk in chunks for item in (chunk.get("resolved_entities") or [])]
    unresolved = [item for chunk in chunks for item in (chunk.get("unresolved_entities") or [])]
    errors = [item for chunk in chunks for item in (chunk.get("errors") or [])]
    warnings = [item for chunk in chunks for item in (chunk.get("warnings") or [])]
    coverage_complete = len(items) == requested_count and not unresolved
    return {
        **first,
        "success": bool(items),
        "partial": bool(errors) or not coverage_complete,
        "contract_version": PROFESSIONAL_BUY_CONTRACT_VERSION,
        "playbook": PROFESSIONAL_BUY_ANALYSIS_MODE,
        "items": items,
        "resolved_entities": resolved,
        "unresolved_entities": unresolved,
        "requested_count": requested_count,
        "covered_count": len(items),
        "coverage_complete": coverage_complete,
        "errors": errors,
        "warnings": warnings,
    }


def _execute_professional_buy_analysis_chunked(
    arguments: dict[str, Any],
    *,
    timeout_seconds: float,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Isolate each company and evaluate the collection with bounded concurrency."""
    symbols = [part.strip() for part in str(arguments.get("symbols") or "").split(",") if part.strip()]
    if not symbols:
        process_options: dict[str, Any] = {
            "timeout_seconds": timeout_seconds,
        }
        if cancel_event is not None:
            process_options["cancel_event"] = cancel_event
        return _execute_tool_process(
            _PROFESSIONAL_BUY_ANALYSIS_TOOL,
            arguments,
            **process_options,
        )

    def run_one(index: int, symbol: str) -> tuple[int, dict[str, Any]]:
        process_options: dict[str, Any] = {
            "timeout_seconds": min(2100.0, timeout_seconds),
        }
        if cancel_event is not None:
            process_options["cancel_event"] = cancel_event
        try:
            result = _execute_tool_process(
                _PROFESSIONAL_BUY_ANALYSIS_TOOL,
                {**arguments, "symbols": symbol},
                **process_options,
            )
        except Exception as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise
            result = _professional_buy_failure_item(symbol, exc)
        return index, result

    ordered: list[dict[str, Any] | None] = [None] * len(symbols)
    stock_concurrency = _professional_buy_stock_concurrency()
    with ThreadPoolExecutor(
        max_workers=min(stock_concurrency, len(symbols))
    ) as pool:
        futures = [pool.submit(run_one, index, symbol) for index, symbol in enumerate(symbols)]
        for future in as_completed(futures):
            index, result = future.result()
            ordered[index] = result
    return _merge_professional_buy_chunks(
        [chunk for chunk in ordered if isinstance(chunk, dict)],
        len(symbols),
    )


def execute_tool_isolated(
    name: str,
    arguments: dict[str, Any],
    *,
    timeout_seconds: float = 40.0,
    cancel_event: threading.Event | None = None,
) -> Any:
    """Execute a tool in a one-shot Python process and return its result.

    A native abort (for example libmini_racer/V8) never raises a Python
    exception in the crashing process.  Process isolation turns that abort
    into a normal non-zero exit code that the Agent can render as a tool error.
    """
    if name == _PROFESSIONAL_EVIDENCE_TOOL:
        return _execute_professional_evidence_chunked(
            arguments,
            timeout_seconds=timeout_seconds,
            cancel_event=cancel_event,
        )
    if name == _PROFESSIONAL_BUY_ANALYSIS_TOOL:
        return _execute_professional_buy_analysis_chunked(
            arguments,
            timeout_seconds=timeout_seconds,
            cancel_event=cancel_event,
        )
    process_options: dict[str, Any] = {"timeout_seconds": timeout_seconds}
    if cancel_event is not None:
        process_options["cancel_event"] = cancel_event
    return _execute_tool_process(name, arguments, **process_options)


__all__ = ["ISOLATED_TOOL_NAMES", "STATEFUL_TOOL_NAMES", "execute_tool_isolated"]
