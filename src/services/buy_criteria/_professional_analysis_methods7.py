"""Professional buy-analysis function group 7."""

from __future__ import annotations

from src.services.buy_criteria.professional_analysis import (
    annotations,
    json,
    logging,
    re,
    ThreadPoolExecutor,
    as_completed,
    datetime,
    Any,
    Callable,
    Iterable,
    Literal,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
    CriterionEvidence,
    _parse_verdict_json,
    CompetitionLandscapeEvaluator,
    CatalystEventsEvaluator,
    FatalRisksEvaluator,
    GrowthDriversEvaluator,
    IndustrialCompetitivenessEvaluator,
    MainlinePositionEvaluator,
    ProsperityCycleEvaluator,
    MainlineDirectionRelation,
    MainlineGateClassification,
    MainlineLifecycle,
    MainlineStrategyProfile,
    MainlineTriggerProgress,
    mainline_strategy_label,
    normalize_mainline_strategy,
    collect_public_research,
    derive_research_scope,
    research_summary_for_lenses,
    logger,
    PROFESSIONAL_BUY_CONTRACT_VERSION,
    PROFESSIONAL_BUY_ANALYSIS_MODE,
    DIMENSION_DEFINITIONS,
    DIMENSION_IDS,
    DIMENSION_TITLES,
    PUBLIC_RESEARCH_LENSES_BY_DIMENSION,
    DimensionId,
    DimensionStatus,
    ModelDimensionStatus,
    RecommendationCode,
    DimensionAssessment,
    ModelDimensionAssessment,
    ForcedSchemaResponseError,
    ProfessionalAssessment,
    OverallAssessment,
    ANALYST_SYSTEM_PROMPT,
    _NUMERIC_CLAIM_RE,
    _NUMERIC_RANGE_RE,
    _ISO_DATE_RE,
    _BARE_DECIMAL_RE,
    _DANGLING_HEADLINE_RE,
    __all__,
 )

__all__ = ['_derive_overall_from_dimensions', '_fallback_assessment', '_inline_json_schema', '_recommendation_label', 'evaluate_shared_market_mainline', 'analyze_professional_buy']

def _derive_overall_from_dimensions(
    dimensions: list[DimensionAssessment],
    stock_info: dict[str, Any],
    errors: list[str],
) -> ProfessionalAssessment:
    """Render the Boolean gate state without inventing a scoring fallback."""
    counts = {
        status: sum(1 for item in dimensions if item.status == status)
        for status in ("pass", "fail", "insufficient", "not_evaluated")
    }
    name = str(stock_info.get("name") or stock_info.get("short_name") or stock_info.get("symbol") or "该公司")
    executed = [item for item in dimensions if item.status != "not_evaluated"]
    supportive = [item for item in executed if item.status == "pass"]
    blocking = next(
        (item for item in executed if item.status in {"fail", "insufficient"}),
        None,
    )
    incomplete_scope = len(executed) < len(DIMENSION_IDS)
    biggest = blocking or (
        executed[-1]
        if executed
        else DimensionAssessment(
            dimension_id="market_mainline",
            status="not_evaluated",
            evaluated_subjects=[],
            headline="本次没有执行公司级维度",
            analysis="当前调用只负责共享资源准备。",
        )
    )
    if blocking and blocking.status == "insufficient":
        recommendation_code: RecommendationCode = "analysis_unavailable"
        recommendation_reason = (
            f"“{DIMENSION_TITLES[blocking.dimension_id]}”的关键来源或分析服务未完成，" "本轮不对公司形成买入结论。"
        )
    elif (
        blocking
        and blocking.dimension_id == "market_mainline"
        and blocking.mainline_classification is not None
        and MainlineDirectionRelation(blocking.mainline_classification.direction_relation)
        == MainlineDirectionRelation.EMERGING_BRANCH
    ):
        recommendation_code = "watchlist"
        recommendation_reason = (
            "产业归属已经确认，但候选主线尚未满足本轮所选策略的第一关准入条件；"
            "后续七维不执行，当前进入主线触发跟踪而不是买入名单。"
        )
    elif blocking:
        recommendation_code = "wait"
        recommendation_reason = (
            f"“{DIMENSION_TITLES[blocking.dimension_id]}”未通过，" "后续维度不再执行，当前不可进入买入计划。"
        )
    elif incomplete_scope:
        recommendation_code = "watchlist"
        recommendation_reason = f"本次只完成前{len(executed)}个共享维度，" "其余公司级维度将在逐股流程中继续执行。"
    else:
        recommendation_code = "conditional_buy"
        recommendation_reason = "八个布尔闸门均已通过，可以进入有纪律的买入计划。"

    positive_headlines = [item.headline for item in supportive[:4]]
    risk_headlines = [item.headline for item in ([blocking] if blocking else []) if item is not None]
    monitoring_points = list(dict.fromkeys(point for item in dimensions for point in item.monitoring_points if point))[
        :6
    ]
    while len(monitoring_points) < 3:
        monitoring_points.append("下一期财报后重新核验八维证据")

    return ProfessionalAssessment(
        investment_profile=(
            f"{name}的八维布尔闸门：通过{counts['pass']}项、"
            f"不通过{counts['fail']}项、取证未完成{counts['insufficient']}项、"
            f"未执行{counts['not_evaluated']}项"
        ),
        overall_summary=(f"{name}本轮执行到第{len(executed)}维。{recommendation_reason}"),
        core_thesis=" → ".join(positive_headlines) or "本轮尚未形成可验证的核心看多逻辑",
        biggest_issue=biggest.headline,
        recommendation_code=recommendation_code,
        recommendation_reason=recommendation_reason,
        dimensions=dimensions,
        bull_case_chain=(" → ".join(positive_headlines) if positive_headlines else "本轮未形成可验证的支持链条"),
        risk_chain=(" → ".join(risk_headlines) if risk_headlines else "当前未识别硬性失败，但仍需持续核验经营兑现"),
        monitoring_points=monitoring_points,
        evidence_gaps=list(dict.fromkeys(errors))[:8],
    )

def _fallback_assessment(error: str) -> ProfessionalAssessment:
    dimensions = [
        DimensionAssessment(
            dimension_id=dimension_id,
            status=("insufficient" if index == 0 else "not_evaluated"),
            evaluated_subjects=[],
            headline=("首个维度的专业复核未完成" if index == 0 else "前序布尔闸门已关闭"),
            analysis=(
                "本轮未能取得首个维度的有效结构化判断，程序按分析未完成关闭后续闸门。"
                if index == 0
                else "前一维度未通过，本维度未执行，也不能用于抵消首个阻断项。"
            ),
            key_evidence=[],
            counter_evidence=[],
            monitoring_points=(["待分析服务恢复后基于同一证据包重新评估"] if index == 0 else []),
        )
        for index, dimension_id in enumerate(DIMENSION_IDS)
    ]
    return ProfessionalAssessment(
        investment_profile="八维布尔闸门在首个维度因取证未完成而停止",
        overall_summary="本轮首个维度未形成可验证判断，后续七维按状态机未执行。",
        core_thesis="待专业分析服务恢复后重新核验",
        biggest_issue=error or "专业分析服务不可用",
        recommendation_code="analysis_unavailable",
        recommendation_reason="首个维度分析未完成，程序已关闭买入闸门且不形成公司结论。",
        dimensions=dimensions,
        bull_case_chain="本轮专业复核未完成，暂不构造看多链条",
        risk_chain="分析服务失败 → 八维结论不可验证 → 暂停买入判断",
        monitoring_points=[
            "专业分析服务恢复情况",
            "行情与财务证据的数据时间",
            "重新评估后的八维状态",
        ],
        evidence_gaps=[error or "专业分析模型不可用"],
    )

def _inline_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Expand local Pydantic refs for gateways without ``$defs`` support."""
    definitions = schema.get("$defs")
    definitions = definitions if isinstance(definitions, dict) else {}

    def expand(value: Any) -> Any:
        if isinstance(value, list):
            return [expand(item) for item in value]
        if not isinstance(value, dict):
            return value
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            name = ref.rsplit("/", 1)[-1]
            target = definitions.get(name)
            if not isinstance(target, dict):
                raise ValueError(f"unresolved local JSON schema reference: {ref}")
            merged = {
                **expand(target),
                **{key: expand(item) for key, item in value.items() if key != "$ref"},
            }
            return merged
        return {key: expand(item) for key, item in value.items() if key != "$defs"}

    result = expand(schema)
    if not isinstance(result, dict):
        raise ValueError("expanded JSON schema is not an object")
    return result

def _recommendation_label(code: str) -> str:
    return {
        "conditional_buy": "满足条件时可进入买入计划",
        "watchlist": "进入中期跟踪池",
        "wait": "等待更好的价格或验证信号",
        "avoid": "当前回避",
        "analysis_unavailable": "本轮分析未完成，等待系统恢复",
        "evidence_insufficient": "关键取证未完成，暂停判断",
    }.get(code, "关键取证未完成，暂停判断")

def evaluate_shared_market_mainline(
    *,
    thesis: str,
    thesis_context: dict[str, Any] | None,
    mainline_strategy: MainlineStrategyProfile | str,
    market_mainline_snapshot: dict[str, Any],
    on_reasoning: Callable[[str], None] | None = None,
) -> tuple[DimensionAssessment, str]:
    """Evaluate the market-level first gate exactly once for a stock batch."""
    evidence = collect_market_mainline_evidence(
        thesis=thesis,
        thesis_context=thesis_context,
        mainline_strategy=mainline_strategy,
        market_mainline_snapshot=market_mainline_snapshot,
    )
    assessment, model_error = _call_professional_model(
        evidence,
        evaluation_limit=1,
        on_reasoning=on_reasoning,
    )
    if assessment is None:
        assessment = _fallback_assessment(model_error)
    return assessment.dimensions[0], model_error

def analyze_professional_buy(
    symbol: str,
    *,
    thesis: str = "",
    thesis_context: dict[str, Any] | None = None,
    mainline_strategy: MainlineStrategyProfile | str = (MainlineStrategyProfile.CONFIRMED_MAINLINE),
    pre_fetched_data: dict[str, Any] | None = None,
    on_reasoning: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Collect evidence, evaluate the eight gates in order and normalize them."""
    effective_thesis = resolve_investment_thesis(thesis, thesis_context)
    strategy_profile = normalize_mainline_strategy(mainline_strategy)
    prefetched = pre_fetched_data if isinstance(pre_fetched_data, dict) else {}
    raw_shared_gate = prefetched.get("market_mainline_assessment")
    shared_gate = DimensionAssessment.model_validate(raw_shared_gate) if raw_shared_gate is not None else None
    shared_model_error = str(prefetched.get("market_mainline_model_error") or "").strip()
    if shared_gate is not None and shared_gate.dimension_id != "market_mainline":
        raise ValueError("shared market mainline assessment must be the first dimension")
    if shared_gate is not None and shared_gate.status != "pass":
        evidence = {
            "contract_version": PROFESSIONAL_BUY_CONTRACT_VERSION,
            "requested_at": datetime.now().astimezone().isoformat(),
            "symbol": symbol,
            "stock_info": {"symbol": symbol, "name": symbol},
            "investment_thesis": effective_thesis or None,
            "thesis_context": thesis_context,
            "mainline_strategy": strategy_profile.value,
            "research_scope": {},
            "public_research": _empty_public_research({}),
            "base_company_packet": {},
            "base_packet_meta": {
                "success": True,
                "partial": False,
                "data_time": (
                    (
                        prefetched.get("market_mainline_snapshot")
                        if isinstance(
                            prefetched.get("market_mainline_snapshot"),
                            dict,
                        )
                        else {}
                    ).get("data_time")
                ),
                "errors": [],
                "warnings": [],
            },
            "dimension_evidence": {},
        }
    else:
        evidence = collect_professional_evidence(
            symbol,
            thesis=effective_thesis,
            thesis_context=thesis_context,
            mainline_strategy=strategy_profile,
            pre_fetched_data=pre_fetched_data,
            requested_sections=(() if shared_gate is not None else ("market_mainline",)),
        )

    def load_dimension(dimension_id: str) -> None:
        _collect_professional_sections(
            evidence,
            (dimension_id,),
            pre_fetched_data=pre_fetched_data,
        )

    assessment, model_error = _call_professional_model(
        evidence,
        dimension_loader=load_dimension,
        precomputed_dimensions=((shared_gate,) if shared_gate is not None else ()),
        on_reasoning=on_reasoning,
    )
    model_error = "；".join(value for value in (shared_model_error, model_error) if value)
    if assessment is None:
        assessment = _fallback_assessment(model_error)

    dimensions = [item.model_dump() for item in assessment.dimensions]
    counts = {
        status: sum(1 for item in dimensions if item["status"] == status)
        for status in ("pass", "fail", "insufficient", "not_evaluated")
    }
    blocking = next(
        (item for item in dimensions if item["status"] in {"fail", "insufficient"}),
        None,
    )
    all_passed = counts["pass"] == len(DIMENSION_IDS)
    recommendation: RecommendationCode
    if all_passed:
        recommendation = "conditional_buy"
    elif blocking and blocking["status"] == "insufficient":
        recommendation = "analysis_unavailable"
    else:
        recommendation = (
            assessment.recommendation_code
            if assessment.recommendation_code
            in {
                "watchlist",
                "wait",
                "avoid",
            }
            else "wait"
        )
    if model_error and blocking and blocking["status"] == "insufficient":
        analysis_status = "execution_failed"
        final_decision = "分析失败"
    elif blocking and blocking["status"] == "insufficient":
        analysis_status = "source_unavailable"
        final_decision = "分析未完成"
    elif all_passed:
        analysis_status = "completed"
        final_decision = "可买入"
    else:
        analysis_status = "completed"
        final_decision = "不可买入"
    recommendation_reason = assessment.recommendation_reason
    overall_summary = assessment.overall_summary

    stock_info = evidence.get("stock_info") or {}
    base_meta = evidence.get("base_packet_meta") or {}
    return {
        "contract_version": PROFESSIONAL_BUY_CONTRACT_VERSION,
        "analysis_mode": PROFESSIONAL_BUY_ANALYSIS_MODE,
        "symbol": symbol,
        "name": (stock_info.get("name") or stock_info.get("short_name") or symbol),
        "thesis": effective_thesis or None,
        "thesis_context": thesis_context,
        "mainline_strategy": strategy_profile.value,
        "investment_profile": assessment.investment_profile,
        "overall_summary": overall_summary,
        "core_thesis": assessment.core_thesis,
        "biggest_issue": assessment.biggest_issue,
        "recommendation_code": recommendation,
        "recommendation": _recommendation_label(recommendation),
        "recommendation_reason": recommendation_reason,
        "analysis_status": analysis_status,
        "final_decision": final_decision,
        "counts": counts,
        "dimensions": dimensions,
        "executed_count": len(DIMENSION_IDS) - counts["not_evaluated"],
        "not_evaluated_count": counts["not_evaluated"],
        "stopped_at": (blocking.get("dimension_id") if blocking else None),
        "stopped_at_name": (DIMENSION_TITLES.get(str(blocking.get("dimension_id") or "")) if blocking else None),
        "gate_pass_complete": all_passed,
        "bull_case_chain": assessment.bull_case_chain,
        "risk_chain": assessment.risk_chain,
        "monitoring_points": assessment.monitoring_points,
        "evidence_gaps": list(
            dict.fromkeys(
                [
                    *assessment.evidence_gaps,
                    *(evidence.get("evidence_gaps") or []),
                ]
            )
        )[:12],
        "source_links": evidence.get("source_links") or [],
        "data_time": base_meta.get("data_time") or evidence.get("requested_at"),
        "quote_basis": base_meta.get("quote_basis"),
        "quote_is_intraday": base_meta.get("quote_is_intraday"),
        "coverage_complete": analysis_status == "completed",
        "model_error": model_error or None,
    }
