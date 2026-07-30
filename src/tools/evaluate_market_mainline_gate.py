"""Evaluate the shared market-level first gate for one investment batch."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from src.agent.result_contracts import InvestmentThesisContext
from src.services.buy_criteria.professional_analysis import (
    DimensionAssessment,
    evaluate_shared_market_mainline,
    resolve_investment_thesis,
)
from src.services.buy_criteria.mainline_policy import (
    MainlineStrategyProfile,
    normalize_mainline_strategy,
)
from src.tools.base import ToolSpec, TypedToolResult, report_tool_progress
from src.tools.evaluate_multi_stock_buy_criteria import (
    MarketMainlineSnapshotArgs,
    public_buy_analysis_error,
)

DESCRIPTION = (
    "基于批次共享的结构化产业方向和同一份市场主线快照，只执行一次八维闸门的"
    "第一维。先区分产业归属与主线生命周期，再按确认型或前瞻布局型策略执行"
    "布尔准入；输出随后绑定到每只股票，个股流程不得重新判断市场主线。"
)


class EvaluateMarketMainlineGateArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    thesis: str = ""
    thesis_context: InvestmentThesisContext | None = None
    mainline_strategy: MainlineStrategyProfile = (
        MainlineStrategyProfile.CONFIRMED_MAINLINE
    )
    # The immutable workflow binds this resource after preflight, so the
    # static call shape permits omission while the executor enforces it.
    market_mainline_snapshot: MarketMainlineSnapshotArgs | None = None


class EvaluateMarketMainlineGateResult(TypedToolResult):
    thesis: str | None = None
    thesis_context: dict[str, Any] | None = None
    mainline_strategy: MainlineStrategyProfile
    market_mainline_snapshot_id: str | None = None
    market_mainline_assessment: DimensionAssessment
    market_mainline_model_error: str | None = None


def evaluate_market_mainline_gate(
    thesis: str = "",
    thesis_context: dict[str, Any] | None = None,
    mainline_strategy: MainlineStrategyProfile | str = (
        MainlineStrategyProfile.CONFIRMED_MAINLINE
    ),
    market_mainline_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if market_mainline_snapshot is None:
        raise ValueError("market_mainline_snapshot is required at execution")
    report_tool_progress(
        "正在绑定本批次的产业方向与市场主线快照",
        progress=10,
    )
    snapshot = MarketMainlineSnapshotArgs.model_validate(
        market_mainline_snapshot
    ).model_dump(mode="python")
    effective_thesis = resolve_investment_thesis(thesis, thesis_context)
    strategy_profile = normalize_mainline_strategy(mainline_strategy)
    report_tool_progress(
        "正在统一判断本批次是否属于当前市场主线",
        progress=35,
    )
    assessment, model_error = evaluate_shared_market_mainline(
        thesis=effective_thesis,
        thesis_context=thesis_context,
        mainline_strategy=strategy_profile,
        market_mainline_snapshot=snapshot,
        on_reasoning=lambda delta: report_tool_progress(
            "正在统一判断本批次是否属于当前市场主线",
            progress=35,
            reasoning_delta=delta,
        ),
    )
    source_unavailable = assessment.status == "insufficient" and not model_error
    public_error = (
        public_buy_analysis_error(model_error)
        if model_error
        else (
            "analysis_source_unavailable：市场主线关键来源未完成，"
            "本批次未形成市场主线结论"
            if source_unavailable
            else ""
        )
    )
    warnings = (
        ["市场主线分析未完成；系统不会把取证故障解释为市场事实。"]
        if source_unavailable
        else []
    )
    report_tool_progress(
        (
            f"批次共享第一关已完成：{assessment.headline}"
            if not model_error
            else "批次共享第一关执行失败，未形成事实性结论"
        ),
        progress=100,
    )
    return {
        "success": not bool(model_error or source_unavailable),
        "partial": bool(model_error or source_unavailable),
        "thesis": effective_thesis or None,
        "thesis_context": thesis_context,
        "mainline_strategy": strategy_profile.value,
        "market_mainline_snapshot_id": snapshot.get("snapshot_id"),
        "market_mainline_assessment": assessment.model_dump(mode="python"),
        "market_mainline_model_error": public_error or None,
        "errors": [public_error] if public_error else [],
        "warnings": warnings,
        "data_time": snapshot.get("data_time") or snapshot.get("as_of_date"),
        "is_stale": snapshot.get("is_stale"),
    }


TOOL = ToolSpec(
    name="evaluate_market_mainline_gate",
    description=DESCRIPTION,
    parameters=None,
    executor=evaluate_market_mainline_gate,
    category="analysis",
    args_model=EvaluateMarketMainlineGateArgs,
    result_model=EvaluateMarketMainlineGateResult,
)


__all__ = ["TOOL", "evaluate_market_mainline_gate"]
