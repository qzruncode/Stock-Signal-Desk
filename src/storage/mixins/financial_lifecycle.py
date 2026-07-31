# -*- coding: utf-8 -*-
"""Structured financial conclusions and forward market outcomes."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import hashlib
import json
from typing import Any, Mapping, Sequence

from sqlalchemy import select

from src.storage.mixins.agent_run_trace import redact_agent_trace
from src.storage.models import (
    AgentFinancialConclusion,
    AgentFinancialOutcome,
    StockDaily,
)


OUTCOME_ENGINE_VERSION = "financial-outcome-1.0"
DEFAULT_OUTCOME_HORIZONS = (5, 20, 60)


def _json(value: Any) -> str:
    return json.dumps(
        redact_agent_trace(value),
        ensure_ascii=False,
        default=str,
    )


def _load_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _conclusion_dict(
    record: AgentFinancialConclusion,
    outcomes: Sequence[AgentFinancialOutcome] = (),
) -> dict[str, Any]:
    return {
        "id": record.id,
        "run_id": record.run_id,
        "conversation_id": record.conversation_id,
        "task_id": record.task_id,
        "conclusion_type": record.conclusion_type,
        "symbol": record.symbol,
        "name": record.name,
        "verdict": record.verdict,
        "contract_version": record.contract_version,
        "as_of_at": _iso(record.as_of_at),
        "baseline_trade_date": _iso(record.baseline_trade_date),
        "baseline_price": record.baseline_price,
        "baseline_source": record.baseline_source,
        "thesis": _load_json(record.thesis_json, {}),
        "evidence": _load_json(record.evidence_json, {}),
        "evidence_fingerprint": record.evidence_fingerprint,
        "lifecycle_status": record.lifecycle_status,
        "outcomes": [
            {
                "id": outcome.id,
                "horizon_trading_days": (
                    outcome.horizon_trading_days
                ),
                "status": outcome.status,
                "evaluated_through_date": _iso(
                    outcome.evaluated_through_date
                ),
                "end_price": outcome.end_price,
                "max_high": outcome.max_high,
                "min_low": outcome.min_low,
                "return_pct": outcome.return_pct,
                "max_favorable_excursion_pct": (
                    outcome.max_favorable_excursion_pct
                ),
                "max_adverse_excursion_pct": (
                    outcome.max_adverse_excursion_pct
                ),
                "outcome_label": outcome.outcome_label,
                "directional_success": outcome.directional_success,
                "engine_version": outcome.engine_version,
                "diagnostics": _load_json(
                    outcome.diagnostics_json,
                    {},
                ),
                "evaluated_at": _iso(outcome.evaluated_at),
            }
            for outcome in sorted(
                outcomes,
                key=lambda item: item.horizon_trading_days,
            )
        ],
        "created_at": _iso(record.created_at),
        "updated_at": _iso(record.updated_at),
    }



from ._financial_lifecycle_methods1 import _FinancialLifecycleMethods1
from ._financial_lifecycle_methods2 import _FinancialLifecycleMethods2


class FinancialLifecycleMixin(
    _FinancialLifecycleMethods1,
    _FinancialLifecycleMethods2,
):
    """Persistence and deterministic forward-outcome evaluation."""


__all__ = [
    "DEFAULT_OUTCOME_HORIZONS",
    "FinancialLifecycleMixin",
    "OUTCOME_ENGINE_VERSION",
]
