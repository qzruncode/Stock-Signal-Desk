# -*- coding: utf-8 -*-
"""Extract only typed financial conclusions from fixed-workflow outcomes."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any, Mapping, Sequence


_BUY_TOOL = "evaluate_multi_stock_buy_criteria"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _parse_datetime(value: Any) -> datetime:
    text = str(value or "").strip()
    if text:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
        except ValueError:
            pass
    return datetime.now()


def extract_financial_conclusions(
    outcomes: Sequence[Any],
) -> list[dict[str, Any]]:
    """Project validated buy-gate packets without parsing answer prose."""

    conclusions: list[dict[str, Any]] = []
    for raw_outcome in outcomes:
        outcome = (
            raw_outcome.model_dump(mode="json")
            if hasattr(raw_outcome, "model_dump")
            else _mapping(raw_outcome)
        )
        task_id = str(outcome.get("task_id") or "").strip()
        result = _mapping(outcome.get("result"))
        for call in result.get("calls") or ():
            call = _mapping(call)
            if call.get("tool") != _BUY_TOOL:
                continue
            packet = _mapping(call.get("result"))
            packet_time = _parse_datetime(packet.get("data_time"))
            for item in packet.get("items") or ():
                item = _mapping(item)
                if item.get("analysis_status") != "completed":
                    continue
                raw_verdict = str(item.get("final_decision") or "")
                if raw_verdict not in {"可买入", "不可买入"}:
                    continue
                symbol = str(item.get("symbol") or "").strip()
                if not symbol:
                    continue
                evidence = {
                    "criteria": item.get("criteria")
                    or item.get("dimensions")
                    or [],
                    "stopped_at": item.get("stopped_at"),
                    "gate_pass_complete": item.get(
                        "gate_pass_complete"
                    ),
                    "coverage_complete": item.get(
                        "coverage_complete"
                    ),
                    "quote_basis": item.get("quote_basis"),
                    "data_time": item.get("data_time")
                    or packet.get("data_time"),
                    "source": packet.get("source"),
                }
                fingerprint = hashlib.sha256(
                    json.dumps(
                        {
                            "task_id": task_id,
                            "symbol": symbol,
                            "verdict": raw_verdict,
                            "contract_version": item.get(
                                "contract_version"
                            )
                            or packet.get("contract_version"),
                            "evidence": evidence,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ).encode("utf-8")
                ).hexdigest()
                conclusions.append(
                    {
                        "task_id": task_id,
                        "conclusion_type": "buy_gate",
                        "symbol": symbol,
                        "name": item.get("name"),
                        "verdict": (
                            "buy"
                            if raw_verdict == "可买入"
                            else "not_buy"
                        ),
                        "contract_version": (
                            item.get("contract_version")
                            or packet.get("contract_version")
                        ),
                        "as_of_at": _parse_datetime(
                            item.get("data_time")
                        )
                        if item.get("data_time")
                        else packet_time,
                        "thesis": {
                            "text": item.get("thesis")
                            or packet.get("thesis"),
                            "context": item.get("thesis_context")
                            or packet.get("thesis_context"),
                            "mainline_strategy": item.get(
                                "mainline_strategy"
                            )
                            or packet.get("mainline_strategy"),
                        },
                        "evidence": evidence,
                        "evidence_fingerprint": fingerprint,
                    }
                )
    return conclusions


__all__ = ["extract_financial_conclusions"]
