"""Function group 1 extracted from src/agent/orchestrator_v2/registry.py."""

from __future__ import annotations

import src.agent.orchestrator_v2.registry as _registry

for _name, _value in vars(_registry).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

__all__ = ['_normalize_output_request', '_project_outcome_resource', '_assumption', '_simple_compiler', '_price_history_compiler', '_theme_compiler', '_industry_compiler', '_financial_filter_compiler', '_investment_compiler']

def _normalize_output_request(
    *,
    node_id: str,
    intent: Any,
) -> tuple[Any, tuple[AssumptionRecord, ...]]:
    supplied = getattr(intent, "output", None)
    values: dict[str, Any] = {}
    assumptions: list[AssumptionRecord] = []
    for field_name, default_value in _OUTPUT_DEFAULTS.items():
        value = getattr(supplied, field_name) if supplied is not None else None
        if value is None:
            value = default_value
            assumptions.append(
                AssumptionRecord(
                    node_id=node_id,
                    field_path=f"/output/{field_name}",
                    value=value,
                    reason="用户未指定该输出偏好，采用能力契约声明的程序默认值。",
                )
            )
        values[field_name] = value
    return (
        intent.model_copy(update={"output": OutputRequestV2(**values)}),
        tuple(assumptions),
    )

def _project_outcome_resource(
    outcome: TaskOutcomeV2,
    resource_type: ResourceType,
) -> ProjectedResourceV2 | None:
    def projected(
        payload: Any,
        *,
        coverage: CoverageV2 | None = None,
    ) -> ProjectedResourceV2:
        return ProjectedResourceV2(
            resource_type=resource_type,
            coverage=coverage or outcome.coverage,
            payload=payload,
        )

    result = outcome.result if isinstance(outcome.result, Mapping) else {}
    if resource_type == ResourceType.SECURITY_COLLECTION:
        securities = result.get("output_entities")
        if isinstance(securities, list):
            return projected({"securities": list(securities)})
        return None
    resource_outputs = result.get("resource_outputs")
    if isinstance(resource_outputs, Mapping):
        direct = resource_outputs.get(resource_type.value)
        if direct is not None:
            if resource_type == ResourceType.DOMAIN_COLLECTION:
                domain_artifact = next(
                    (
                        artifact
                        for packet in result.get("derived_results") or ()
                        if isinstance(packet, Mapping) and isinstance(packet.get("result"), Mapping)
                        for artifact in packet["result"].get("semantic_artifacts") or ()
                        if isinstance(artifact, Mapping)
                        and artifact.get("type")
                        in {
                            "domain_collection_v2",
                            "ranked_domains",
                        }
                    ),
                    None,
                )
                return projected(
                    {
                        "domains": direct,
                        **(
                            {
                                (
                                    "domain_collection_v2"
                                    if domain_artifact.get("type") == "domain_collection_v2"
                                    else "ranked_domains"
                                ): dict(domain_artifact)
                            }
                            if domain_artifact is not None
                            else {}
                        ),
                    }
                )
            return projected(direct)
    if resource_type == ResourceType.MARKET_MAINLINE_SNAPSHOT:
        for call in result.get("calls") or ():
            if (
                isinstance(call, Mapping)
                and call.get("tool") == "prepare_market_mainline_snapshot"
                and isinstance(call.get("result"), Mapping)
                and call["result"].get("available") is True
            ):
                return projected(dict(call["result"]))
        return None
    if resource_type == ResourceType.EVIDENCE_COLLECTION:
        return projected(result)
    if resource_type == ResourceType.GENERIC_RESULT:
        return projected(result)
    return None

def _assumption(
    node_id: str,
    field: str,
    value: Any,
    reason: str,
) -> AssumptionRecord:
    return AssumptionRecord(
        node_id=node_id,
        field_path=f"/{field}",
        value=value,
        reason=reason,
    )

def _simple_compiler(
    *,
    aliases: Mapping[str, str] | None = None,
    defaults: Mapping[str, tuple[Any, str]] | None = None,
    program_fields: Mapping[str, Any] | None = None,
) -> Compiler:
    """Build a closed intent-to-execution projection.

    This is a program compiler, not a planner schema: only fields declared by
    the selected Pydantic intent can enter the projection.
    """

    field_aliases = dict(aliases or {})
    declared_defaults = dict(defaults or {})
    fixed_fields = dict(program_fields or {})

    def compile_intent(
        node_id: str,
        objective: str,
        intent: BaseModel,
        input_refs: tuple[InputReferenceV2, ...],
        result_selection: ResultSelectionV2 | None,
        current_year: int,
    ) -> NormalizedIntent[Any]:
        del objective, input_refs, result_selection, current_year
        raw = intent.model_dump(
            mode="json",
            exclude_none=True,
            exclude_unset=True,
        )
        raw.pop("output", None)
        raw.pop("user_confirmed", None)
        raw = {key: value for key, value in raw.items() if value not in ([], {}, ())}
        parameters: dict[str, Any] = {}
        for key, value in raw.items():
            parameters[field_aliases.get(key, key)] = value
        assumptions: list[AssumptionRecord] = []
        for key, (value, reason) in declared_defaults.items():
            target = field_aliases.get(key, key)
            if target not in parameters:
                parameters[target] = value
                assumptions.append(_assumption(node_id, key, value, reason))
        parameters.update(fixed_fields)
        return NormalizedIntent(
            intent=intent,
            execution_parameters=MappingProxyType(parameters),
            assumptions=tuple(assumptions),
        )

    return compile_intent

def _price_history_compiler(
    node_id: str,
    objective: str,
    intent: BaseModel,
    input_refs: tuple[InputReferenceV2, ...],
    result_selection: ResultSelectionV2 | None,
    current_year: int,
) -> NormalizedIntent[Any]:
    del objective, input_refs, result_selection, current_year
    period = getattr(intent, "period", None)
    assumptions: tuple[AssumptionRecord, ...] = ()
    if period is None:
        parameters = {"count": 120, "use_cache": True}
        assumptions = (
            _assumption(
                node_id,
                "period",
                {"kind": "recent", "count": 120},
                "用户未指定历史区间，按最近 120 个交易日执行。",
            ),
        )
    elif period.kind == "recent":
        parameters = {"count": period.count, "use_cache": True}
    else:
        parameters = {
            "start_date": period.start_date.strftime("%Y%m%d"),
            "end_date": period.end_date.strftime("%Y%m%d"),
            "use_cache": True,
        }
    return NormalizedIntent(
        intent=intent,
        execution_parameters=MappingProxyType(parameters),
        assumptions=assumptions,
    )

def _theme_compiler(
    node_id: str,
    objective: str,
    intent: ThemeStockDiscoveryIntent,
    input_refs: tuple[InputReferenceV2, ...],
    result_selection: ResultSelectionV2 | None,
    current_year: int,
) -> NormalizedIntent[Any]:
    del objective, input_refs, result_selection, current_year
    normalized, assumptions = _normalize_output_request(node_id=node_id, intent=intent)
    return NormalizedIntent(
        intent=normalized,
        execution_parameters=MappingProxyType(
            {
                "domains": [{"label": theme} for theme in normalized.themes],
            }
        ),
        assumptions=assumptions,
    )

def _industry_compiler(
    node_id: str,
    objective: str,
    intent: BaseModel,
    input_refs: tuple[InputReferenceV2, ...],
    result_selection: ResultSelectionV2 | None,
    current_year: int,
) -> NormalizedIntent[Any]:
    del input_refs, current_year
    explicit_subjects = list(getattr(intent, "explicit_subjects"))
    assumptions = (
        (
            _assumption(
                node_id,
                "result_selection",
                {"mode": "top_k", "max_items": 16},
                "用户未指定返回数量，产业受益板块默认最多返回 16 个。",
            ),
        )
        if result_selection is None
        else ()
    )
    return NormalizedIntent(
        intent=intent,
        execution_parameters=MappingProxyType(
            {
                "query": objective,
                "domains": [{"label": item} for item in explicit_subjects],
                "_assumptions": [
                    {
                        "field_path": assumption.field_path,
                        "value": assumption.value,
                        "reason": assumption.reason,
                        "source": assumption.source,
                    }
                    for assumption in assumptions
                ],
            }
        ),
        assumptions=assumptions,
    )

def _financial_filter_compiler(
    node_id: str,
    objective: str,
    intent: CollectionFinancialFilterIntent,
    input_refs: tuple[InputReferenceV2, ...],
    result_selection: ResultSelectionV2 | None,
    current_year: int,
) -> NormalizedIntent[Any]:
    del objective, input_refs, result_selection
    normalized_intent, output_assumptions = _normalize_output_request(
        node_id=node_id,
        intent=intent,
    )
    conditions: list[dict[str, Any]] = []
    assumptions: list[AssumptionRecord] = list(output_assumptions)
    canonical_predicates: list[Any] = []
    for index, predicate in enumerate(normalized_intent.predicates):
        period = predicate.period
        if period is None:
            requested_period_basis = _DEFAULT_PERIOD[predicate.metric]
            fiscal_year = None
            assumptions.append(
                _assumption(
                    node_id,
                    f"predicates/{index}/period",
                    {
                        "kind": requested_period_basis,
                        **(
                            {"resolved_year": current_year - 1}
                            if requested_period_basis == "previous_fiscal_year"
                            else {}
                        ),
                    },
                    (
                        "资产负债率未指定期间，按最新可用报告期执行。"
                        if predicate.metric == "debt_ratio"
                        else "年度型财务指标未指定期间，按上一完整财年执行。"
                    ),
                )
            )
        else:
            requested_period_basis = period.kind
            fiscal_year = getattr(period, "year", None)
        if requested_period_basis not in _SUPPORTED_PERIODS[predicate.metric]:
            raise OrchestratorV2Error(
                AgentErrorCode.CLARIFICATION_REQUIRED,
                (
                    f"{predicate.metric} 不支持 {requested_period_basis} 口径；"
                    f"可用口径为 {sorted(_SUPPORTED_PERIODS[predicate.metric])}。"
                ),
                task_id=node_id,
            )
        period_basis = requested_period_basis
        if requested_period_basis == "previous_fiscal_year":
            period_basis = "fiscal_year"
            fiscal_year = current_year - 1
        canonical_period = (
            LatestReportPeriod(kind="latest_report")
            if period_basis == "latest_report"
            else (
                TtmPeriod(kind="ttm")
                if period_basis == "ttm"
                else FiscalYearPeriod(kind="fiscal_year", year=int(fiscal_year))
            )
        )
        condition: dict[str, Any] = {
            "metric": predicate.metric,
            "period_basis": period_basis,
            "operator": predicate.operator,
            "action": predicate.action,
        }
        if fiscal_year is not None:
            condition["fiscal_year"] = fiscal_year
        if predicate.metric == "debt_ratio":
            condition.update(
                {
                    "threshold": predicate.percent,
                    "threshold_unit": "percent",
                }
            )
            canonical_predicates.append(predicate.model_copy(update={"period": canonical_period}))
        else:
            amount_in_cny = (
                predicate.amount.value
                * {
                    "cny": 1.0,
                    "wan_cny": 10_000.0,
                    "yi_cny": 100_000_000.0,
                }[predicate.amount.unit]
            )
            condition.update({"threshold": amount_in_cny, "threshold_unit": "cny"})
            canonical_predicates.append(
                predicate.model_copy(
                    update={
                        "period": canonical_period,
                        "amount": MoneyAmount(value=amount_in_cny, unit="cny"),
                    }
                )
            )
        conditions.append(condition)
    conditions.sort(
        key=lambda item: (
            str(item["metric"]),
            str(item["period_basis"]),
            int(item.get("fiscal_year") or 0),
            str(item["operator"]),
            float(item["threshold"]),
            str(item["threshold_unit"]),
            str(item["action"]),
        )
    )
    canonical_predicates.sort(
        key=lambda predicate: (
            predicate.metric,
            predicate.period.model_dump_json(),
            predicate.operator,
            predicate.percent if predicate.metric == "debt_ratio" else predicate.amount.value,
            predicate.action,
        )
    )
    normalized_intent = normalized_intent.model_copy(update={"predicates": tuple(canonical_predicates)})
    return NormalizedIntent(
        intent=normalized_intent,
        execution_parameters=MappingProxyType({"conditions": conditions}),
        assumptions=tuple(assumptions),
    )

def _investment_compiler(
    node_id: str,
    objective: str,
    intent: BaseModel,
    input_refs: tuple[InputReferenceV2, ...],
    result_selection: ResultSelectionV2 | None,
    current_year: int,
) -> NormalizedIntent[Any]:
    del objective, input_refs, result_selection, current_year
    normalized, assumptions = _normalize_output_request(node_id=node_id, intent=intent)
    requested_profile = getattr(normalized, "mainline_strategy", None)
    profile = normalize_mainline_strategy(requested_profile)
    if requested_profile is None:
        assumptions = (
            *assumptions,
            _assumption(
                node_id,
                "mainline_strategy",
                profile.value,
                "用户未指定主线投资时机，按确认型主线执行。",
            ),
        )
        normalized = normalized.model_copy(
            update={
                "mainline_strategy": MainlineStrategyProfile.CONFIRMED_MAINLINE,
            }
        )
    return NormalizedIntent(
        intent=normalized,
        execution_parameters=MappingProxyType(
            {
                **({"thesis": normalized.thesis} if normalized.thesis else {}),
                "mainline_strategy": profile.value,
            }
        ),
        assumptions=assumptions,
    )
