"""Eight-dimensional Boolean buy gates for a complete stock collection."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from src.agent.result_contracts import InvestmentThesisContext
from src.services.buy_criteria.orchestrator import (
    BUY_GATE_CONTRACT_VERSION,
    CriterionOrchestrator,
)
from src.services.buy_criteria.professional_analysis import (
    DIMENSION_DEFINITIONS,
    DimensionAssessment,
    PROFESSIONAL_BUY_ANALYSIS_MODE,
    resolve_investment_thesis,
)
from src.services.buy_criteria.mainline_policy import (
    MainlineStrategyProfile,
    normalize_mainline_strategy,
)
from src.tools.base import ToolSpec, TypedToolResult, report_tool_progress
from src.tools.symbols import resolve_securities_csv

logger = logging.getLogger(__name__)

DESCRIPTION = (
    "对完整A股集合逐只执行用户定义的八维串行布尔买入闸门。每一维由模型阅读该维证据"
    "后返回明确布尔结果；程序只控制顺序、证据校验和首个失败即停止。连续八维全部通过"
    "才可买入。第一关按显式主线策略区分确认型与前瞻布局型，不使用关键词命中、"
    "固定分数或跨关抵消。"
)


class MarketMainlineSnapshotArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool = True
    partial: bool = False
    available: bool = True
    generation_pending: bool = False
    report_pending: bool = False
    snapshot_id: str | None = None
    contract_version: str | None = None
    generated_at: str | None = None
    as_of_date: str | None = None
    overview: Any = None
    market_stage: Any = None
    current_mainlines: list[Any] = Field(default_factory=list)
    candidate_mainlines: list[Any] = Field(default_factory=list)
    future_mainlines: list[Any] = Field(default_factory=list)
    action_summary: Any = None
    evidence_digest: Any = None
    source_summary: Any = None
    validation: Any = None
    llm_used: Any = None
    model_used: Any = None
    data_time: str | None = None
    is_stale: bool | None = None
    freshness_unknown: bool = False
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class EvaluateMultiStockBuyCriteriaArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    symbols: str = Field(min_length=1)
    thesis: str = ""
    thesis_context: InvestmentThesisContext | None = None
    mainline_strategy: MainlineStrategyProfile = MainlineStrategyProfile.CONFIRMED_MAINLINE
    market_mainline_snapshot: MarketMainlineSnapshotArgs | None = None
    market_mainline_assessment: DimensionAssessment | None = None
    market_mainline_model_error: str | None = None


class EvaluateMultiStockBuyCriteriaResult(TypedToolResult):
    contract_version: str
    playbook: str
    thesis: str | None = None
    thesis_context: dict[str, Any] | None = None
    mainline_strategy: MainlineStrategyProfile
    market_mainline_snapshot_id: str | None = None
    items: list[dict[str, Any]]
    resolved_entities: list[dict[str, Any]]
    unresolved_entities: list[str]
    requested_count: int = Field(ge=0)
    covered_count: int = Field(ge=0)
    coverage_complete: bool
    gate_order: list[dict[str, Any]]
    decision_rule: str
    completed_count: int = Field(default=0, ge=0)
    source_unavailable_count: int = Field(default=0, ge=0)
    # Deprecated compatibility field. V9 no longer exposes a user-facing
    # "evidence insufficient" terminal state.
    evidence_insufficient_count: int = Field(default=0, ge=0)
    execution_failed_count: int = Field(default=0, ge=0)
    source: str | None = None


def _gate_contract() -> list[dict[str, Any]]:
    return [
        {
            "index": index,
            "criterion_id": dimension_id,
            "criterion_name": title,
        }
        for index, (dimension_id, title) in enumerate(DIMENSION_DEFINITIONS)
    ]


def public_buy_analysis_error(value: Any) -> str:
    """Return a stable user-safe failure without leaking provider payloads."""
    text = str(value or "").strip()
    if text.startswith("analysis_"):
        return text
    if text.startswith(
        (
            "无法解析：",
            "没有可验证的A股公司",
            "单轮最多分析",
            "前置流程阻止",
            "工具没有返回",
        )
    ):
        return text
    lowered = text.lower()
    if any(
        marker in lowered
        for marker in (
            "timeout",
            "timed out",
            "gateway time-out",
            "gateway timeout",
            "504",
            "超时",
        )
    ):
        return "analysis_timeout：分析服务响应超时，本轮未形成公司结论"
    if any(
        marker in lowered
        for marker in (
            "validationerror",
            "schema",
            "string_too_long",
            "tool arguments",
            "结构化",
        )
    ):
        return "analysis_schema_invalid：结构化分析结果校验失败，本轮未形成公司结论"
    if any(
        marker in lowered
        for marker in (
            "connection",
            "connecterror",
            "remoteprotocolerror",
            "ssl",
            "broken pipe",
            "连接",
        )
    ):
        return "analysis_connection_failed：分析服务连接失败，本轮未形成公司结论"
    return "analysis_failed：专业分析未完成，本轮未形成公司结论"


def build_professional_buy_failure_item(
    entity: dict[str, Any],
    message: str,
    *,
    thesis: str,
    mainline_strategy: MainlineStrategyProfile | str = (MainlineStrategyProfile.CONFIRMED_MAINLINE),
) -> dict[str, Any]:
    public_message = public_buy_analysis_error(message)
    strategy_profile = normalize_mainline_strategy(mainline_strategy)
    return {
        "contract_version": BUY_GATE_CONTRACT_VERSION,
        "analysis_mode": PROFESSIONAL_BUY_ANALYSIS_MODE,
        "symbol": str(entity.get("symbol") or ""),
        "name": entity.get("name") or entity.get("symbol"),
        "thesis": thesis or None,
        "mainline_strategy": strategy_profile.value,
        "analysis_status": "execution_failed",
        "final_decision": "分析失败",
        "coverage_complete": False,
        "gate_pass_complete": False,
        "passed_count": 0,
        "failed_count": 0,
        "insufficient_count": 0,
        "not_evaluated_count": len(DIMENSION_DEFINITIONS),
        "total": len(DIMENSION_DEFINITIONS),
        "stopped_at": None,
        "stopped_at_name": None,
        "stopped_verdict": public_message,
        "blocking_reasons": [],
        "criteria": [],
        "position_advice": {
            "initial_position_pct": 0,
            "max_position_pct": 0,
        },
        "invalidation_conditions": [],
        "model_error": public_message,
    }


def build_professional_buy_failure_result(
    symbol: str,
    message: str,
    *,
    thesis: str = "",
    mainline_strategy: MainlineStrategyProfile | str = (MainlineStrategyProfile.CONFIRMED_MAINLINE),
    market_mainline_snapshot_id: str | None = None,
) -> dict[str, Any]:
    """Return one complete, fail-closed terminal packet for a failed company run."""
    clean_symbol = str(symbol or "").strip()
    item = build_professional_buy_failure_item(
        {"symbol": clean_symbol, "name": clean_symbol},
        str(message or "专业买入分析未完成"),
        thesis=str(thesis or "").strip(),
        mainline_strategy=mainline_strategy,
    )
    strategy_profile = normalize_mainline_strategy(mainline_strategy)
    return {
        "success": False,
        "partial": True,
        "contract_version": BUY_GATE_CONTRACT_VERSION,
        "playbook": PROFESSIONAL_BUY_ANALYSIS_MODE,
        "thesis": str(thesis or "").strip() or None,
        "mainline_strategy": strategy_profile.value,
        "market_mainline_snapshot_id": (str(market_mainline_snapshot_id or "").strip() or None),
        "items": [item],
        "resolved_entities": [],
        "unresolved_entities": [],
        "requested_count": 1,
        "covered_count": 1,
        "coverage_complete": True,
        "gate_order": _gate_contract(),
        "decision_rule": (
            "每只股票逐关执行；首个fail表示不符合本次准入条件并停止，"
            "关键来源或执行失败则标记分析未完成；连续八维全部pass且集合覆盖完整才可买入"
        ),
        "completed_count": 0,
        "source_unavailable_count": 0,
        "evidence_insufficient_count": 0,
        "execution_failed_count": 1,
        "errors": [public_buy_analysis_error(message)],
        "warnings": [],
        "data_time": datetime.now().astimezone().isoformat(),
        "is_stale": None,
    }


def evaluate_multi_stock_buy_criteria(
    symbols: str,
    thesis: str = "",
    thesis_context: dict[str, Any] | None = None,
    mainline_strategy: MainlineStrategyProfile | str = (MainlineStrategyProfile.CONFIRMED_MAINLINE),
    market_mainline_snapshot: dict[str, Any] | None = None,
    market_mainline_assessment: dict[str, Any] | None = None,
    market_mainline_model_error: str | None = None,
) -> dict[str, Any]:
    resolved, unresolved = resolve_securities_csv(symbols)
    requested_count = len(resolved) + len(unresolved)
    clean_thesis = resolve_investment_thesis(thesis, thesis_context)
    strategy_profile = normalize_mainline_strategy(mainline_strategy)
    if len(resolved) > 300:
        return {
            "success": False,
            "partial": False,
            "contract_version": BUY_GATE_CONTRACT_VERSION,
            "playbook": PROFESSIONAL_BUY_ANALYSIS_MODE,
            "thesis": clean_thesis or None,
            "thesis_context": thesis_context,
            "mainline_strategy": strategy_profile.value,
            "market_mainline_snapshot_id": (
                market_mainline_snapshot.get("snapshot_id") if isinstance(market_mainline_snapshot, dict) else None
            ),
            "items": [],
            "resolved_entities": [],
            "unresolved_entities": unresolved,
            "requested_count": requested_count,
            "covered_count": 0,
            "coverage_complete": False,
            "gate_order": _gate_contract(),
            "decision_rule": ("每只股票首项失败立即停止；连续八维全部通过才可买入"),
            "completed_count": 0,
            "source_unavailable_count": 0,
            "evidence_insufficient_count": 0,
            "execution_failed_count": 0,
            "errors": ["单轮最多分析300只股票；本次没有静默截断"],
            "warnings": [],
            "data_time": None,
            "is_stale": None,
        }
    if not resolved:
        return {
            "success": False,
            "partial": False,
            "contract_version": BUY_GATE_CONTRACT_VERSION,
            "playbook": PROFESSIONAL_BUY_ANALYSIS_MODE,
            "thesis": clean_thesis or None,
            "thesis_context": thesis_context,
            "mainline_strategy": strategy_profile.value,
            "market_mainline_snapshot_id": (
                market_mainline_snapshot.get("snapshot_id") if isinstance(market_mainline_snapshot, dict) else None
            ),
            "items": [],
            "resolved_entities": [],
            "unresolved_entities": unresolved,
            "requested_count": requested_count,
            "covered_count": 0,
            "coverage_complete": False,
            "gate_order": _gate_contract(),
            "decision_rule": ("每只股票首项失败立即停止；连续八维全部通过才可买入"),
            "completed_count": 0,
            "source_unavailable_count": 0,
            "evidence_insufficient_count": 0,
            "execution_failed_count": 0,
            "errors": ["没有可验证的A股公司名称或代码"],
            "warnings": [],
            "data_time": None,
            "is_stale": None,
        }

    results_by_symbol: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    def analyze(entity: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
        symbol = str(entity["symbol"])
        try:
            result = CriterionOrchestrator().analyze_for_agent(
                symbol,
                thesis=clean_thesis,
                thesis_context=thesis_context,
                mainline_strategy=strategy_profile,
                pre_fetched_data=(
                    {
                        **(
                            {"market_mainline_snapshot": (market_mainline_snapshot)}
                            if market_mainline_snapshot is not None
                            else {}
                        ),
                        **(
                            {
                                "market_mainline_assessment": (market_mainline_assessment),
                                "market_mainline_model_error": (market_mainline_model_error),
                            }
                            if market_mainline_assessment is not None
                            else {}
                        ),
                    }
                    if (market_mainline_snapshot is not None or market_mainline_assessment is not None)
                    else None
                ),
                on_reasoning=lambda delta: report_tool_progress(
                    f"{entity.get('name') or symbol} 正在执行八维专业判断",
                    reasoning_delta=delta,
                ),
            )
            result["name"] = entity.get("name") or symbol
            if result.get("analysis_status") == "execution_failed":
                raw_error = str(result.get("model_error") or result.get("stopped_verdict") or f"{symbol}分析执行失败")
                error = public_buy_analysis_error(raw_error)
                result["model_error"] = error
                result["stopped_verdict"] = error
                return symbol, result, error
            if result.get("analysis_status") == "source_unavailable":
                gaps = "；".join(str(value) for value in result.get("evidence_gaps") or [])
                if "取证超时" in gaps:
                    error = "analysis_timeout：关键来源取证超时，" "本轮未形成公司结论"
                elif "连接失败" in gaps:
                    error = "analysis_connection_failed：关键来源连接失败，" "本轮未形成公司结论"
                else:
                    error = "analysis_source_unavailable：关键来源未完成，" "本轮未形成公司结论"
                return symbol, result, error
            return symbol, result, ""
        except Exception as exc:
            logger.exception(
                "professional buy analysis failed for %s(%s)",
                entity.get("name") or symbol,
                symbol,
            )
            message = public_buy_analysis_error(f"{type(exc).__name__}: {str(exc)[:240]}")
            return (
                symbol,
                build_professional_buy_failure_item(
                    entity,
                    message,
                    thesis=clean_thesis,
                    mainline_strategy=strategy_profile,
                ),
                message,
            )

    with ThreadPoolExecutor(
        max_workers=min(4, max(1, len(resolved))),
    ) as pool:
        futures = {pool.submit(analyze, entity): entity for entity in resolved}
        for future in as_completed(futures):
            symbol, result, error = future.result()
            results_by_symbol[symbol] = result
            if error:
                errors.append(error)

    items = [results_by_symbol[str(entity["symbol"])] for entity in resolved]
    if unresolved:
        errors.append("无法解析：" + "、".join(unresolved))
    covered_count = len(items)
    coverage_complete = covered_count == len(resolved) and not unresolved
    status_counts = {
        status: sum(str(item.get("analysis_status") or "completed") == status for item in items)
        for status in (
            "completed",
            "source_unavailable",
            "execution_failed",
        )
    }
    source_unavailable = status_counts["source_unavailable"]
    execution_failed = status_counts["execution_failed"]
    if source_unavailable:
        warnings = [f"{source_unavailable} 只股票因关键来源未完成而暂停，系统未对公司形成结论。"]
    else:
        warnings = []
    now = datetime.now().astimezone().isoformat()
    return {
        "success": bool(items) and not (source_unavailable or execution_failed),
        "partial": bool(errors or source_unavailable or execution_failed or not coverage_complete),
        "contract_version": BUY_GATE_CONTRACT_VERSION,
        "playbook": PROFESSIONAL_BUY_ANALYSIS_MODE,
        "thesis": clean_thesis or None,
        "thesis_context": thesis_context,
        "mainline_strategy": strategy_profile.value,
        "market_mainline_snapshot_id": (
            market_mainline_snapshot.get("snapshot_id") if isinstance(market_mainline_snapshot, dict) else None
        ),
        "items": items,
        "resolved_entities": resolved,
        "unresolved_entities": unresolved,
        "requested_count": requested_count,
        "covered_count": covered_count,
        "coverage_complete": coverage_complete,
        "gate_order": _gate_contract(),
        "decision_rule": (
            "每只股票逐关执行；首个fail表示不符合本次准入条件并停止，"
            "关键来源或执行失败标记分析未完成；连续八维全部pass且集合覆盖完整才可买入"
        ),
        "completed_count": status_counts["completed"],
        "source_unavailable_count": source_unavailable,
        "evidence_insufficient_count": 0,
        "execution_failed_count": execution_failed,
        "errors": errors,
        "warnings": warnings,
        "data_time": now,
        "is_stale": None,
        "source": ("内部同步股票池、市场证据、公司披露、财务、公告、" "行情及技术指标"),
    }


def _failure_result(
    arguments: Mapping[str, Any],
    error_text: str,
    attempt: int,
) -> dict[str, Any]:
    symbol = str(arguments.get("symbols") or "").strip()
    result = build_professional_buy_failure_result(
        symbol,
        f"{symbol}{error_text}",
        thesis=str(arguments.get("thesis") or ""),
        mainline_strategy=arguments.get("mainline_strategy"),
        market_mainline_snapshot_id=str((arguments.get("market_mainline_snapshot") or {}).get("snapshot_id") or ""),
    )
    result["runtime_attempts"] = attempt
    return result


def _project_shared_mainline_block(
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Project the shared first-gate verdict across one stock deterministically."""
    raw_assessment = arguments.get("market_mainline_assessment")
    assessment = raw_assessment if isinstance(raw_assessment, Mapping) else {}
    status = str(assessment.get("status") or "").strip()
    if status not in {"fail", "insufficient"}:
        raise ValueError("shared market mainline projection requires a non-pass assessment")
    return evaluate_multi_stock_buy_criteria(**dict(arguments))


TOOL = ToolSpec(
    name="evaluate_multi_stock_buy_criteria",
    description=DESCRIPTION,
    parameters=None,
    executor=evaluate_multi_stock_buy_criteria,
    category="analysis",
    args_model=EvaluateMultiStockBuyCriteriaArgs,
    result_model=EvaluateMultiStockBuyCriteriaResult,
    failure_result=_failure_result,
    guard_blocked_result=_project_shared_mainline_block,
)
