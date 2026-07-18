"""Run native-risk Agent tools outside the FastAPI worker process."""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
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

_PROFESSIONAL_EVIDENCE_TOOL = "get_multi_stock_decision_evidence"
_PROFESSIONAL_EVIDENCE_CHUNK_SIZE = 2


def _execute_tool_process(
    name: str,
    arguments: dict[str, Any],
    *,
    timeout_seconds: float,
) -> Any:
    """Execute exactly one worker process without higher-level fan-out."""
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


def _snapshot_fallback_for_professional_chunk(
    symbols: str,
    thesis: str,
    error: Exception,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Preserve every requested company when one deep-evidence shard stalls."""
    snapshot = _execute_tool_process(
        "get_multi_stock_snapshot",
        {"symbols": symbols},
        timeout_seconds=timeout_seconds,
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
            "screening_flags": {"positive": [], "negative": []},
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
) -> dict[str, Any]:
    symbols = [part.strip() for part in str(arguments.get("symbols") or "").split(",") if part.strip()]
    if len(symbols) <= _PROFESSIONAL_EVIDENCE_CHUNK_SIZE:
        return _execute_tool_process(
            _PROFESSIONAL_EVIDENCE_TOOL,
            arguments,
            timeout_seconds=timeout_seconds,
        )

    symbol_chunks = [
        symbols[index:index + _PROFESSIONAL_EVIDENCE_CHUNK_SIZE]
        for index in range(0, len(symbols), _PROFESSIONAL_EVIDENCE_CHUNK_SIZE)
    ]
    # Two-stock cold-cache shards complete well inside the Agent's 90-second
    # envelope.  Running four shards concurrently avoids one process issuing
    # 70+ rate-limited upstream calls serially and timing out as a whole.
    deep_timeout = min(max(10.0, timeout_seconds), 65.0)
    fallback_timeout = max(8.0, timeout_seconds - deep_timeout - 3.0)
    thesis = str(arguments.get("thesis") or "").strip()

    def run_chunk(index: int, chunk: list[str]) -> tuple[int, dict[str, Any]]:
        chunk_symbols = ",".join(chunk)
        chunk_arguments = {**arguments, "symbols": chunk_symbols}
        try:
            result = _execute_tool_process(
                _PROFESSIONAL_EVIDENCE_TOOL,
                chunk_arguments,
                timeout_seconds=deep_timeout,
            )
        except Exception as exc:
            result = _snapshot_fallback_for_professional_chunk(
                chunk_symbols,
                thesis,
                exc,
                timeout_seconds=fallback_timeout,
            )
        return index, result

    ordered: list[dict[str, Any] | None] = [None] * len(symbol_chunks)
    with ThreadPoolExecutor(max_workers=len(symbol_chunks)) as pool:
        futures = [pool.submit(run_chunk, index, chunk) for index, chunk in enumerate(symbol_chunks)]
        for future in as_completed(futures):
            index, result = future.result()
            ordered[index] = result
    return _merge_professional_chunks([chunk for chunk in ordered if isinstance(chunk, dict)])


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
    if name == _PROFESSIONAL_EVIDENCE_TOOL:
        return _execute_professional_evidence_chunked(
            arguments,
            timeout_seconds=timeout_seconds,
        )
    return _execute_tool_process(name, arguments, timeout_seconds=timeout_seconds)


__all__ = ["ISOLATED_TOOL_NAMES", "execute_tool_isolated"]
