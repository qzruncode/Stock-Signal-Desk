"""End-to-end contracts for collection-level eight-dimensional decisions."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from api.v1.endpoints.agent.chat import (
    _build_professional_buy_decision_answer,
)
from src.agent.task_executor import WorkflowExecutor
from src.agent.task_workflows import (
    ConfirmationState,
    EntityScope,
    ResolvedTask,
    StandardTask,
    StandardTaskKind,
    compile_task,
    workflow_for,
)
from src.services.buy_criteria.professional_analysis import (
    DIMENSION_DEFINITIONS,
    DimensionAssessment,
)
from src.tools.evaluate_market_mainline_gate import (
    evaluate_market_mainline_gate,
)
from src.tools.evaluate_multi_stock_buy_criteria import (
    build_professional_buy_failure_result,
    evaluate_multi_stock_buy_criteria,
)
from src.tools.prepare_market_mainline_snapshot import (
    prepare_market_mainline_snapshot,
)
from src.tools.base import ToolProgressUpdate, tool_progress_observer
from src.tools.registry import ToolRegistry


EXPECTED_DIMENSIONS = (
    "market_mainline",
    "industrial_competitiveness",
    "industry_cycle",
    "competition_quality",
    "growth_drivers",
    "forward_catalysts",
    "valuation_odds",
    "major_risks",
)



"""Shared fixtures for the focused test slices."""

def _task() -> StandardTask:
    return StandardTask(
        task_id="buy_now",
        kind=StandardTaskKind.INVESTMENT_DECISION,
        objective="这些股票中哪些现在能买入",
        entity_scope=EntityScope.PREVIOUS_ANSWER,
        entities=[],
        parameters={"thesis": "用户给出的结构化投资逻辑"},
        depends_on=[],
        output_requirements=[],
        confirmation=ConfirmationState.NOT_REQUIRED,
        confidence=0.99,
    )

def _criterion(index: int, status: str = "pass") -> dict:
    dimension_id, title = DIMENSION_DEFINITIONS[index]
    return {
        "criterion_id": dimension_id,
        "criterion_name": title,
        "index": index,
        "passed": status == "pass",
        "status": status,
        "verdict": f"{title}的证据与反证已经核验。",
        "details": {
            "key_evidence": [f"{title}支持证据"],
            "counter_evidence": [f"{title}主要反证"],
        },
    }

def _item(symbol: str, statuses: list[str]) -> dict:
    criteria = [_criterion(index, status) for index, status in enumerate(statuses)]
    all_pass = len(criteria) == 8 and all(status == "pass" for status in statuses)
    stopped = next(
        (item for item in criteria if item["status"] != "pass"),
        None,
    )
    return {
        "symbol": symbol,
        "name": f"公司{symbol}",
        "criteria": criteria,
        "analysis_status": ("source_unavailable" if "insufficient" in statuses else "completed"),
        "final_decision": ("可买入" if all_pass else "分析未完成" if "insufficient" in statuses else "不可买入"),
        "gate_pass_complete": all_pass,
        "stopped_at": stopped["criterion_id"] if stopped else None,
        "stopped_at_name": stopped["criterion_name"] if stopped else None,
    }
