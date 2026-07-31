"""Professional buy-analysis function group 6."""

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

__all__ = ['_call_professional_model']

def _call_professional_model(
    evidence: dict[str, Any],
    *,
    dimension_loader: Callable[[str], None] | None = None,
    precomputed_dimensions: Iterable[DimensionAssessment | dict[str, Any]] = (),
    evaluation_limit: int | None = None,
    on_reasoning: Callable[[str], None] | None = None,
) -> tuple[ProfessionalAssessment | None, str]:

    from src.services.buy_criteria._professional_call_support import _forced_call
    from src.services.buy_criteria._professional_prompts import (
        DIMENSION_SYSTEM_PROMPT,
        build_dimension_instructions,
    )

    def forced_call(**kwargs: Any) -> BaseModel:
        return _forced_call(
            **kwargs,
            evidence=evidence,
            on_reasoning=on_reasoning,
            inline_json_schema=_inline_json_schema,
        )

    evidence_view = _prompt_evidence_view(evidence)
    strategy_profile = normalize_mainline_strategy(evidence_view.get("mainline_strategy"))
    dimension_instructions = build_dimension_instructions(strategy_profile)
    dimension_system_prompt = DIMENSION_SYSTEM_PROMPT
    stock_info = evidence_view.get("stock_info")
    stock_info = stock_info if isinstance(stock_info, dict) else {}
    compact_stock_info = {
        key: stock_info.get(key)
        for key in (
            "symbol",
            "name",
            "short_name",
            "industry",
            "main_business",
            "business_scope",
        )
        if stock_info.get(key)
    }

    adjacent_evidence = {dimension_id: set() for dimension_id in DIMENSION_IDS}

    def dimension_request(
        definition: tuple[str, str],
        *,
        retry: bool = False,
        repair_error: Exception | None = None,
    ) -> DimensionAssessment:
        dimension_id, title = definition
        section_names = {dimension_id, *adjacent_evidence.get(dimension_id, set())}
        sections = {
            key: {
                "success": value.get("success"),
                "evidence_gap": value.get("evidence_gap"),
                "summary": _bounded_text(
                    (
                        "技术止损、均线、ATR和机械目标价已从基本面估值判断中排除；"
                        "请只使用company_context的结构化估值、同行、增长和财务质量。"
                        if key == "valuation_odds"
                        else value.get("summary")
                    ),
                    850 if retry else 1_250,
                ),
                "structured_context": value.get("structured_context") or {},
                "deterministic_entry_context": None,
            }
            for key, value in evidence_view["dimension_evidence"].items()
            if key in section_names and isinstance(value, dict)
        }
        company_context = _dimension_company_context(
            (evidence_view.get("company_packet") if isinstance(evidence_view.get("company_packet"), dict) else {}),
            dimension_id,
        )
        if retry and dimension_id == "major_risks":
            financial = company_context.get("financials")
            financial = financial if isinstance(financial, dict) else {}
            company_context = {
                "profile": company_context.get("profile") or {},
                "financials": {
                    "basis": financial.get("basis"),
                    "latest_full_year": financial.get("latest_full_year"),
                    "latest_quarters": financial.get("latest_quarters"),
                },
                "risk_events": company_context.get("risk_events") or {},
            }
        request = {
            "stock_info": compact_stock_info,
            "requested_at": evidence_view.get("requested_at"),
            "investment_thesis": evidence_view.get("investment_thesis"),
            "thesis_context": evidence_view.get("thesis_context"),
            "mainline_strategy": strategy_profile.value,
            "required_scope_confirmation": {
                "subjects": _structured_thesis_labels(evidence_view.get("thesis_context")),
                "instruction": (
                    "evaluated_subjects 必须逐字复制本轮实际判断的结构化产业方向；"
                    "不得用公司法定行业或公开搜索自行替换。"
                ),
            },
            "research_scope": evidence_view.get("research_scope"),
            "public_research_coverage": evidence_view.get("public_research_coverage"),
            "public_research": [
                item
                for item in evidence_view.get("public_research_evidence") or []
                if isinstance(item, dict)
                and item.get("lens")
                in PUBLIC_RESEARCH_LENSES_BY_DIMENSION.get(
                    dimension_id,
                    set(),
                )
            ][:8],
            "company_context": company_context,
            "requested_dimension": {
                "dimension_id": dimension_id,
                "title": title,
                "professional_instruction": dimension_instructions.get(
                    dimension_id,
                    "使用本轮证据完成本维度的支持、反证与边界分析。",
                ),
            },
            "prior_dimensions": [item.model_dump(mode="json") for item in dimensions if item.status != "not_evaluated"],
            "dimension_evidence": sections,
            "capability_gaps": [
                gap
                for gap in evidence_view.get("capability_gaps") or []
                if any(section in str(gap) for section in section_names)
            ][:6],
            "source_failures": [
                failure
                for failure in evidence_view.get("source_failures") or []
                if isinstance(failure, dict)
                and (
                    failure.get("section") in section_names
                    or failure.get("section") == "base_company_packet"
                    or (
                        failure.get("section") == "public_research"
                        and failure.get("source")
                        in PUBLIC_RESEARCH_LENSES_BY_DIMENSION.get(
                            dimension_id,
                            set(),
                        )
                    )
                )
            ][:8],
            "public_disclosure_limits": [gap for gap in evidence_view.get("public_disclosure_limits") or []][:6],
        }
        if retry and repair_error is not None:
            invalid_payload = (
                repair_error.payload
                if isinstance(repair_error, ForcedSchemaResponseError)
                else {"error": f"{type(repair_error).__name__}: {repair_error}"}
            )
            request["targeted_repair"] = {
                "invalid_payload": invalid_payload,
                "issues": (
                    repair_error.issues
                    if isinstance(repair_error, ForcedSchemaResponseError)
                    else [
                        {
                            "pointer": "/choices/0/message/tool_calls",
                            "code": "forced_schema_missing",
                            "expected": (
                                "exactly one submit_dimension_assessment tool call "
                                "whose arguments match the supplied schema"
                            ),
                            "allowed": ["submit_dimension_assessment"],
                        }
                    ]
                ),
                "instruction": (
                    "只修复上述结构化输出错误；保持同一维度、同一证据和同一 JSON Schema，"
                    "不得重做任务图或改判其他维度。"
                ),
            }
        user_prompt = json.dumps(request, ensure_ascii=False, default=str)
        raw_sections = evidence.get("dimension_evidence")
        raw_sections = raw_sections if isinstance(raw_sections, dict) else {}
        validation_evidence = {
            "requested_at": evidence.get("requested_at"),
            "thesis_context": evidence.get("thesis_context"),
            "base_company_packet": evidence.get("base_company_packet"),
            "public_research": request.get("public_research") or [],
            "structured_context": {key: sections[key].get("structured_context") or {} for key in sections},
            "dimension_evidence": {key: raw_sections.get(key) for key in section_names if key in raw_sections},
        }
        active_system_prompt = dimension_system_prompt + (
            "这是同一失败维度的定点恢复调用。上一次完整非法 payload 和字段错误已附在"
            " targeted_repair；只返回最小但完整的合规判断，"
            "优先使用company_context中的结构化字段，删除任何无法逐字或等值追溯的数字。"
            if retry
            else ""
        )
        result = forced_call(
            tool_name="submit_dimension_assessment",
            description="提交程序指定的一个买入分析维度",
            system_prompt=active_system_prompt,
            user_prompt=user_prompt,
            response_model=ModelDimensionAssessment,
            # Provider-side reasoning counts against this budget. Leave enough
            # room for the reasoning plus the required typed tool call.
            max_tokens=16_000 if retry else 20_000,
            call_type="professional_buy_dimensions",
        )
        if not isinstance(result, ModelDimensionAssessment):
            raise ValueError("dimension response has unexpected type")
        result = DimensionAssessment.model_validate(result.model_dump())
        if result.dimension_id != dimension_id:
            raise ValueError(f"模型返回维度 {result.dimension_id}，预期 {dimension_id}")
        if result.status == "not_evaluated":
            raise ValueError("模型不得把当前已执行维度标记为 not_evaluated")
        if dimension_id == "market_mainline":
            result = _enforce_mainline_gate_policy(result, evidence)
        elif result.mainline_classification is not None:
            raise ForcedSchemaResponseError(
                "mainline_classification is only valid for market_mainline",
                result.model_dump(mode="json"),
                issues=[
                    {
                        "pointer": "/mainline_classification",
                        "code": "field_not_allowed_for_dimension",
                        "expected": "null",
                        "allowed": [None],
                    }
                ],
            )
        required_subjects = _structured_thesis_labels(evidence_view.get("thesis_context"))
        confirmed_subjects = {str(value).strip() for value in result.evaluated_subjects if str(value).strip()}
        if required_subjects and not set(required_subjects).intersection(confirmed_subjects):
            raise ValueError("模型没有确认本轮结构化产业方向，拒绝接受脱离主题的维度判断")
        unsupported_claims = _unsupported_numeric_claims(
            result,
            validation_evidence,
        )
        if unsupported_claims:
            logger.warning(
                "professional buy redacted unsupported numeric claims for %s/%s: %s",
                evidence.get("symbol"),
                dimension_id,
                "、".join(unsupported_claims[:8]),
            )
            result = _redact_unsupported_numeric_claims(
                result,
                unsupported_claims,
            )
        return _repair_incomplete_dimension_headline(result)

    dimensions = [
        (item if isinstance(item, DimensionAssessment) else DimensionAssessment.model_validate(item))
        for item in precomputed_dimensions
    ]
    if len(dimensions) > len(DIMENSION_IDS):
        raise ValueError("precomputed dimensions exceed the eight-gate contract")
    if tuple(item.dimension_id for item in dimensions) != DIMENSION_IDS[: len(dimensions)]:
        raise ValueError("precomputed dimensions must be an exact prefix of the eight gates")
    model_errors: list[str] = []
    blocked = bool(dimensions and dimensions[-1].status != "pass")
    evaluated_in_call = 0
    for dimension_id, title in DIMENSION_DEFINITIONS[len(dimensions) :]:
        if blocked:
            dimensions.append(
                DimensionAssessment(
                    dimension_id=dimension_id,
                    status="not_evaluated",
                    evaluated_subjects=_structured_thesis_labels(evidence_view.get("thesis_context")),
                    headline="前序布尔闸门已关闭",
                    analysis="前一维度未通过，本维度按固定状态机不再执行，不能用于抵消首个阻断项。",
                    key_evidence=[],
                    counter_evidence=[],
                    monitoring_points=[],
                )
            )
            continue
        if evaluation_limit is not None and evaluated_in_call >= evaluation_limit:
            dimensions.append(
                DimensionAssessment(
                    dimension_id=dimension_id,
                    status="not_evaluated",
                    evaluated_subjects=_structured_thesis_labels(evidence_view.get("thesis_context")),
                    headline="当前共享评估范围不包含本维度",
                    analysis=("本次只生成批次共享的市场主线结论；公司级维度将在逐股流程中继续执行。"),
                    key_evidence=[],
                    counter_evidence=[],
                    monitoring_points=[],
                )
            )
            continue

        if dimension_id not in evidence.get("dimension_evidence", {}) and dimension_loader is not None:
            try:
                dimension_loader(dimension_id)
            except Exception as exc:
                logger.warning(
                    "professional buy lazy evidence %s failed for %s: %s",
                    dimension_id,
                    evidence.get("symbol"),
                    exc,
                )
                evidence.setdefault("dimension_evidence", {})[dimension_id] = {
                    "success": False,
                    "evidence_gap": (f"{type(exc).__name__}: {str(exc)[:240]}"),
                    "summary": "该证据维度按需获取失败。",
                    "raw_data": {},
                    "structured_context": {},
                }
            evidence_view = _prompt_evidence_view(evidence)

        current_section = (evidence_view.get("dimension_evidence") or {}).get(dimension_id)
        if not isinstance(current_section, dict) or current_section.get("success") is False:
            gap = str((current_section or {}).get("evidence_gap") or "当前维度关键证据未取得")
            result = DimensionAssessment(
                dimension_id=dimension_id,
                status="insufficient",
                evaluated_subjects=_structured_thesis_labels(evidence_view.get("thesis_context")),
                headline=f"{title}的关键取证未完成",
                analysis=(f"{gap}。程序将本轮标记为分析未完成并关闭后续闸门，" "不会把取证失败写成公司的事实性结论。"),
                key_evidence=[],
                counter_evidence=[],
                monitoring_points=[f"重新取得“{title}”的关键证据"],
            )
            dimensions.append(result)
            blocked = True
            continue

        try:
            result = dimension_request((dimension_id, title))
        except Exception as first_error:
            try:
                result = dimension_request(
                    (dimension_id, title),
                    retry=True,
                    repair_error=first_error,
                )
            except Exception as retry_error:
                error = f"{title}: {type(retry_error).__name__}: " f"{str(retry_error)[:180]}"
                model_errors.append(error)
                logger.warning(
                    "professional buy %s failed for %s; first=%s",
                    error,
                    evidence.get("symbol"),
                    first_error,
                )
                result = DimensionAssessment(
                    dimension_id=dimension_id,
                    status="insufficient",
                    evaluated_subjects=_structured_thesis_labels(evidence_view.get("thesis_context")),
                    headline="当前维度的专业复核未完成",
                    analysis=(f"模型没有返回“{title}”的有效结构化判断，" "程序将本轮标记为分析未完成并关闭后续闸门。"),
                    key_evidence=[],
                    counter_evidence=[],
                    monitoring_points=[f"重新核验“{title}”"],
                )
        dimensions.append(result)
        evaluated_in_call += 1
        if result.status != "pass":
            blocked = True

    if blocked or any(item.status == "not_evaluated" for item in dimensions):
        error = "；".join(model_errors)
        return (
            _derive_overall_from_dimensions(
                dimensions,
                compact_stock_info,
                model_errors,
            ),
            error,
        )

    compact_dimensions = [
        {
            "dimension_id": item.dimension_id,
            "status": item.status,
            "headline": item.headline,
            "key_evidence": item.key_evidence[:1],
            "counter_evidence": item.counter_evidence[:1],
            "monitoring_points": item.monitoring_points[:1],
        }
        for item in dimensions
    ]
    overall_request = {
        "stock_info": compact_stock_info,
        "requested_at": evidence_view.get("requested_at"),
        "investment_thesis": evidence_view.get("investment_thesis"),
        "dimensions": compact_dimensions,
        "evidence_gaps": (evidence_view.get("evidence_gaps") or [])[:6],
    }
    overall_system_prompt = (
        "你是A股投委会的资深分析师。八个维度已经逐项完成，你只负责形成一致的结论先行摘要。"
        "不得改变任何维度的状态，不得补造数字。请定义投资画像、核心逻辑、最大问题、"
        "当前建议、看多传导链、风险传导链和至少三个有明确数据口径的监控指标；"
        "证据没有给出目标值或阈值时，不得自行设定。"
        "调用本步骤意味着八个布尔闸门已经全部 pass，recommendation_code 必须为"
        " conditional_buy；不得改成评分、加权或跨维度抵消。"
    )
    try:
        overall = forced_call(
            tool_name="submit_overall_assessment",
            description="提交八维分析的统一投委会结论",
            system_prompt=overall_system_prompt,
            user_prompt=json.dumps(overall_request, ensure_ascii=False, default=str),
            response_model=OverallAssessment,
            max_tokens=16_000,
            call_type="professional_buy_overall",
        )
        if not isinstance(overall, OverallAssessment):
            raise ValueError("overall response has unexpected type")
        unsupported_overall_claims = _unsupported_numeric_claims(
            overall,
            {
                "request": overall_request,
                "validated_dimensions": [item.model_dump() for item in dimensions],
            },
        )
        if unsupported_overall_claims:
            logger.warning(
                "professional buy redacted unsupported overall numeric claims for %s: %s",
                evidence.get("symbol"),
                "、".join(unsupported_overall_claims[:8]),
            )
            overall = _redact_unsupported_numeric_claims(
                overall,
                unsupported_overall_claims,
            )
            if not isinstance(overall, OverallAssessment):
                raise ValueError("redacted overall response has unexpected type")
    except Exception as exc:
        error = f"综合结论: {type(exc).__name__}: {str(exc)[:240]}"
        logger.warning("professional buy overall failed for %s: %s", evidence.get("symbol"), error)
        return (
            _derive_overall_from_dimensions(
                dimensions,
                compact_stock_info,
                [error],
            ),
            error,
        )

    try:
        return (
            ProfessionalAssessment(
                **overall.model_dump(),
                dimensions=dimensions,
            ),
            "",
        )
    except Exception as exc:
        return None, f"ProfessionalAssessment: {type(exc).__name__}: {str(exc)[:240]}"
