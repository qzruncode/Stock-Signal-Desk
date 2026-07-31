"""Professional buy-analysis function group 5."""

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

__all__ = ['_enforce_mainline_gate_policy']

def _enforce_mainline_gate_policy(
    result: DimensionAssessment,
    evidence: dict[str, Any],
) -> DimensionAssessment:
    """Enforce strategy boundaries without replacing the semantic judgment."""

    profile = normalize_mainline_strategy(evidence.get("mainline_strategy"))
    classification = result.mainline_classification
    if classification is None:
        raise ForcedSchemaResponseError(
            "market_mainline requires mainline_classification",
            result.model_dump(mode="json"),
            issues=[
                {
                    "pointer": "/mainline_classification",
                    "code": "mainline_classification_required",
                    "expected": ("one typed classification for the requested mainline " "strategy"),
                    "allowed": [],
                }
            ],
        )
    if normalize_mainline_strategy(classification.strategy_profile) != profile:
        raise ForcedSchemaResponseError(
            "mainline_classification.strategy_profile does not match request",
            result.model_dump(mode="json"),
            issues=[
                {
                    "pointer": ("/mainline_classification/strategy_profile"),
                    "code": "mainline_strategy_mismatch",
                    "expected": profile.value,
                    "allowed": [profile.value],
                }
            ],
        )

    report = _mainline_report_from_evidence(evidence)
    current_rows = report.get("current_mainlines") or []
    candidate_rows = report.get("candidate_mainlines") or report.get("future_mainlines") or []
    relation = MainlineDirectionRelation(classification.direction_relation)
    matched_current = _mainline_row_by_name(
        current_rows,
        classification.matched_mainline,
    )
    matched_candidate = _mainline_row_by_name(
        candidate_rows,
        classification.matched_mainline,
    )

    binding_issues: list[dict[str, Any]] = []
    if relation in {
        MainlineDirectionRelation.ACTIVE_BRANCH,
        MainlineDirectionRelation.EMERGING_BRANCH,
    }:
        expected_rows = current_rows if relation == MainlineDirectionRelation.ACTIVE_BRANCH else candidate_rows
        matched_row = matched_current if relation == MainlineDirectionRelation.ACTIVE_BRANCH else matched_candidate
        expected_names = [
            str(item.get("name") or "").strip()
            for item in expected_rows
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ]
        if matched_row is None:
            binding_issues.append(
                {
                    "pointer": "/mainline_classification/matched_mainline",
                    "code": "mainline_resource_binding_invalid",
                    "expected": ("one exact mainline name copied from the corresponding " "structured report section"),
                    "allowed": expected_names,
                }
            )
        else:
            expected_branches = [
                str(value).strip() for value in matched_row.get("branches") or [] if str(value).strip()
            ]
            if str(classification.matched_branch or "").strip() not in expected_branches:
                binding_issues.append(
                    {
                        "pointer": "/mainline_classification/matched_branch",
                        "code": "mainline_branch_binding_invalid",
                        "expected": ("one exact branch name copied from the matched " "structured mainline"),
                        "allowed": expected_branches,
                    }
                )
            expected_lifecycle = str(matched_row.get("lifecycle") or "").strip()
            if str(classification.lifecycle or "").strip() != expected_lifecycle:
                binding_issues.append(
                    {
                        "pointer": "/mainline_classification/lifecycle",
                        "code": "mainline_lifecycle_binding_invalid",
                        "expected": expected_lifecycle,
                        "allowed": [expected_lifecycle],
                    }
                )
    elif relation == MainlineDirectionRelation.CORE:
        matched_row = matched_current or matched_candidate
        if matched_row is None:
            binding_issues.append(
                {
                    "pointer": "/mainline_classification/matched_mainline",
                    "code": "mainline_resource_binding_invalid",
                    "expected": ("one exact mainline name copied from the structured report"),
                    "allowed": [
                        str(item.get("name") or "").strip()
                        for item in [*current_rows, *candidate_rows]
                        if isinstance(item, dict) and str(item.get("name") or "").strip()
                    ],
                }
            )
        else:
            expected_lifecycle = str(matched_row.get("lifecycle") or "").strip()
            if str(classification.lifecycle or "").strip() != expected_lifecycle:
                binding_issues.append(
                    {
                        "pointer": "/mainline_classification/lifecycle",
                        "code": "mainline_lifecycle_binding_invalid",
                        "expected": expected_lifecycle,
                        "allowed": [expected_lifecycle],
                    }
                )
    if binding_issues:
        raise ForcedSchemaResponseError(
            "mainline classification failed structured resource binding",
            result.model_dump(mode="json"),
            issues=binding_issues,
        )

    if result.status == "pass":
        current_branches = {
            str(value).strip() for value in (matched_current or {}).get("branches") or [] if str(value).strip()
        }
        current_lifecycle = str((matched_current or {}).get("lifecycle") or "")
        classified_lifecycle = str(classification.lifecycle or "")
        current_relation_bound = relation == MainlineDirectionRelation.CORE or (
            relation == MainlineDirectionRelation.ACTIVE_BRANCH
            and str(classification.matched_branch or "").strip() in current_branches
        )
        current_eligible = (
            matched_current is not None
            and current_relation_bound
            and MainlineLifecycle(classification.lifecycle)
            in {
                MainlineLifecycle.CONFIRMED,
                MainlineLifecycle.EXPANDING,
            }
            and current_lifecycle == classified_lifecycle
        )
        missing: list[str] = []
        early_eligible = False
        if profile == MainlineStrategyProfile.EARLY_POSITIONING and matched_candidate is not None:
            early_eligible, missing = _early_candidate_eligibility(
                matched_candidate,
                classification,
            )
        if not current_eligible and not early_eligible:
            candidate_name = str(classification.matched_mainline or "该方向")
            conditions = "、".join(missing) or "所选策略的结构化准入条件"
            return result.model_copy(
                update={
                    "status": "fail",
                    "headline": (f"{candidate_name}未达到" f"{mainline_strategy_label(profile)}准入条件")[:120],
                    "analysis": (
                        f"产业归属或候选关系可以成立，但程序复核发现尚未满足：{conditions}。"
                        "本次只否定所选买入策略下的准入资格，不否定该产业方向本身。"
                    ),
                    "counter_evidence": list(
                        dict.fromkeys(
                            [
                                *result.counter_evidence,
                                *missing,
                            ]
                        )
                    )[:4],
                }
            )

    if (
        result.status == "fail"
        and relation == MainlineDirectionRelation.EMERGING_BRANCH
        and matched_candidate is not None
    ):
        candidate_name = str(classification.matched_mainline or "候选主线")
        branch_name = str(classification.matched_branch or "本轮产业方向")
        if profile == MainlineStrategyProfile.CONFIRMED_MAINLINE:
            return result.model_copy(
                update={
                    "headline": (f"{branch_name}属于{candidate_name}候选分支，" "但未通过确认型主线门槛")[:120],
                    "analysis": (
                        f"{branch_name}与{candidate_name}的产业归属成立。"
                        "当前失败只表示该候选方向尚未升级为未来1—6个月已确认主导叙事，"
                        "不表示该产业不存在或与上位主题无关。"
                    ),
                }
            )
    return result
