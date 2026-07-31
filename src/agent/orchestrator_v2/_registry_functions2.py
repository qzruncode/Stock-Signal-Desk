"""Function group 2 extracted from src/agent/orchestrator_v2/registry.py."""

from __future__ import annotations

import src.agent.orchestrator_v2.registry as _registry

for _name, _value in vars(_registry).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

__all__ = ['_theme_evidence_compiler', '_named_inputs', '_source_discovery_compiler', '_feed_compiler', '_article_compiler', '_export_compiler', '_webpage_compiler', '_effect', '_resources', '_cross_run_freshness', '_sem', '_make_spec', 'capability_for', 'capability_catalog', 'migration_coverage', 'normalize_capability_intent']

def _theme_evidence_compiler(
    node_id: str,
    objective: str,
    intent: BaseModel,
    input_refs: tuple[InputReferenceV2, ...],
    result_selection: ResultSelectionV2 | None,
    current_year: int,
) -> NormalizedIntent[Any]:
    del input_refs, result_selection, current_year
    normalized, output_assumptions = _normalize_output_request(
        node_id=node_id,
        intent=intent,
    )
    days = normalized.days
    assumptions = list(output_assumptions)
    if days is None:
        days = 365
        assumptions.append(
            _assumption(
                node_id,
                "days",
                365,
                "用户未指定公司业务证据窗口，按最近 365 天执行。",
            )
        )
    return NormalizedIntent(
        intent=normalized,
        execution_parameters=MappingProxyType(
            {
                "candidate_scope": "candidate_collection",
                "query": normalized.focus or objective,
                "days": days,
            }
        ),
        assumptions=tuple(assumptions),
    )

def _named_inputs(values: list[dict[str, Any]]) -> dict[str, Any]:
    return {str(item["name"]): item["value"] for item in values}

def _source_discovery_compiler(
    node_id: str,
    objective: str,
    intent: BaseModel,
    input_refs: tuple[InputReferenceV2, ...],
    result_selection: ResultSelectionV2 | None,
    current_year: int,
) -> NormalizedIntent[Any]:
    del node_id, input_refs, result_selection, current_year
    payload = intent.model_dump(
        mode="json",
        exclude_none=True,
        exclude_unset=True,
    )
    information_needs = payload.pop("information_needs", ())
    query_parts = [
        str(value).strip()
        for value in (
            *information_needs,
            payload.get("query"),
            payload.get("keyword"),
        )
        if str(value or "").strip()
    ]
    unique_query_parts = tuple(dict.fromkeys(query_parts))
    if unique_query_parts:
        # Source discovery is a union lookup: joining explicit information
        # needs lets the selector keep routes relevant to any requested use.
        payload["query"] = " ".join(unique_query_parts)
    payload.pop("keyword", None)
    if not payload.get("query") and not payload.get("route_path"):
        payload["query"] = objective
    payload["force"] = False
    return NormalizedIntent(
        intent=intent,
        execution_parameters=MappingProxyType(payload),
    )

def _feed_compiler(
    node_id: str,
    objective: str,
    intent: BaseModel,
    input_refs: tuple[InputReferenceV2, ...],
    result_selection: ResultSelectionV2 | None,
    current_year: int,
) -> NormalizedIntent[Any]:
    del node_id, objective, input_refs, result_selection, current_year
    payload = intent.model_dump(mode="json", exclude_none=True)
    inputs = _named_inputs(payload.pop("inputs", []))
    if inputs:
        payload["params"] = inputs
    payload["force"] = False
    return NormalizedIntent(
        intent=intent,
        execution_parameters=MappingProxyType(payload),
    )

def _article_compiler(
    node_id: str,
    objective: str,
    intent: BaseModel,
    input_refs: tuple[InputReferenceV2, ...],
    result_selection: ResultSelectionV2 | None,
    current_year: int,
) -> NormalizedIntent[Any]:
    del node_id, objective, result_selection, current_year
    payload = intent.model_dump(
        mode="json",
        exclude_none=True,
        exclude_unset=True,
    )
    # Artifact ids identify typed collection wrappers, never the documents
    # inside those collections. The runtime resolves the bound artifact payload
    # and the workflow compiler selects one validated TextDocumentResource.
    if any(
        ref.resource_type == ResourceType.TEXT_DOCUMENT_COLLECTION
        for ref in input_refs
    ):
        payload.pop("resource_id", None)
    payload["force"] = False
    return NormalizedIntent(
        intent=intent,
        execution_parameters=MappingProxyType(payload),
    )

def _export_compiler(
    node_id: str,
    objective: str,
    intent: BaseModel,
    input_refs: tuple[InputReferenceV2, ...],
    result_selection: ResultSelectionV2 | None,
    current_year: int,
) -> NormalizedIntent[Any]:
    del node_id, objective, input_refs, result_selection, current_year
    payload = intent.model_dump(mode="json", exclude_none=True)
    inputs = _named_inputs(payload.pop("inputs", []))
    if inputs:
        payload["params"] = inputs
    return NormalizedIntent(
        intent=intent,
        execution_parameters=MappingProxyType(payload),
    )

def _webpage_compiler(
    node_id: str,
    objective: str,
    intent: BaseModel,
    input_refs: tuple[InputReferenceV2, ...],
    result_selection: ResultSelectionV2 | None,
    current_year: int,
) -> NormalizedIntent[Any]:
    del node_id, objective, input_refs, result_selection, current_year
    aliases = {
        "item_selector": "item",
        "title_selector": "item_title",
        "link_selector": "item_link",
        "description_selector": "item_desc",
        "published_selector": "item_pubdate",
        "content_selector": "item_content",
    }
    payload = {
        aliases.get(key, key): value
        for key, value in intent.model_dump(
            mode="json",
            exclude_none=True,
            exclude_unset=True,
        ).items()
    }
    return NormalizedIntent(
        intent=intent,
        execution_parameters=MappingProxyType(payload),
    )

def _effect(value: EffectClass) -> EffectLevel:
    return EffectLevel(value.value)

def _resources(
    values: frozenset[TaskResource],
) -> frozenset[ResourceType]:
    mapping = {
        TaskResource.SECURITY_COLLECTION: ResourceType.SECURITY_COLLECTION,
        TaskResource.DOMAIN_COLLECTION: ResourceType.DOMAIN_COLLECTION,
        TaskResource.RSS_SOURCE_COLLECTION: ResourceType.RSS_SOURCE_COLLECTION,
        TaskResource.RSS_ITEM_COLLECTION: ResourceType.RSS_ITEM_COLLECTION,
        TaskResource.TEXT_DOCUMENT_COLLECTION: ResourceType.TEXT_DOCUMENT_COLLECTION,
        TaskResource.EVIDENCE_COLLECTION: ResourceType.EVIDENCE_COLLECTION,
    }
    return frozenset(mapping[item] for item in values)

def _cross_run_freshness(
    seconds: int,
    *,
    market_session_sensitive: bool = False,
    require_observed_at: bool = False,
) -> FreshnessPolicy:
    return FreshnessPolicy(
        reuse_scope=CacheReuseScope.CROSS_RUN,
        max_age_seconds=seconds,
        market_session_sensitive=market_session_sensitive,
        require_observed_at=require_observed_at,
    )

def _sem(
    dimensions: tuple[EvidenceDimension, ...],
    claims: tuple[str, ...],
    limitations: tuple[str, ...],
    *,
    question_types: frozenset[QuestionType] | None = None,
    fallbacks: tuple[Capability, ...] = (),
    auto_expandable: bool = False,
) -> _CapabilitySemantics:
    return _CapabilitySemantics(
        question_types=question_types if question_types is not None else _FACT_RESEARCH,
        dimensions=frozenset(dimensions),
        claims=claims,
        limitations=limitations,
        fallbacks=fallbacks,
        auto_expandable=auto_expandable,
    )

def _make_spec(capability: Capability) -> CapabilitySpec[Any, TaskOutcomeV2]:
    workflow = workflow_for(StandardTaskKind(capability.value))
    input_resources = _resources(workflow.input_resources)
    required_input_resources = _resources(
        workflow.required_input_resources
    )
    alternative_input_resource_groups = tuple(
        _resources(group)
        for group in workflow.alternative_input_resource_groups
    )
    output_resources = _resources(workflow.output_resources)
    if not output_resources:
        output_resources = frozenset({ResourceType.GENERIC_RESULT})
    if capability in {
        Capability.INVESTMENT_DECISION,
        Capability.STOCK_DEEP_RESEARCH,
        Capability.CATALYST_ANALYSIS,
    }:
        output_resources = frozenset(
            {
                *output_resources,
                ResourceType.EVIDENCE_COLLECTION,
            }
        )
    if capability == Capability.MARKET_MAINLINE_RESEARCH:
        output_resources = frozenset(
            {
                ResourceType.MARKET_MAINLINE_SNAPSHOT,
                ResourceType.EVIDENCE_COLLECTION,
            }
        )
    policy = ExecutionPolicy(
        effect=_effect(workflow.effect),
        confirmation_required=False,
        max_calls=workflow.max_tool_calls,
        max_parallelism=workflow.max_parallel_steps,
        max_attempts=(2 if workflow.effect == EffectClass.READ else 1),
        retry_backoff_seconds=0.5,
        retry_backoff_multiplier=2.0,
        retryable_error_codes=(
            "timeout",
            "connection_error",
            "provider_rate_limited",
            "provider_unavailable",
            "tool_process_crashed",
        ),
    )
    compiler = _COMPILERS.get(capability, _simple_compiler())
    capability_version = "4.2.0" if capability == Capability.INVESTMENT_DECISION else "4.0.0"
    semantics = _SEMANTICS_BY_CAPABILITY[capability]
    return CapabilitySpec(
        capability=capability,
        version=capability_version,
        title=workflow.title,
        description=workflow.description,
        intent_model=_INTENT_MODELS[capability],
        result_model=TaskOutcomeV2,
        input_resources=input_resources,
        required_input_resources=required_input_resources,
        alternative_input_resource_groups=alternative_input_resource_groups,
        output_resources=output_resources,
        compiler=compiler,
        execution_policy=policy,
        freshness_policy=(
            _FRESHNESS_BY_CAPABILITY[capability] if workflow.effect == EffectClass.READ else _RUN_ONLY_FRESHNESS
        ),
        projector=_project_outcome_resource,
        renderer=(
            RendererMode.DETERMINISTIC
            if workflow.result_contract in _DETERMINISTIC_CONTRACTS
            else RendererMode.EVIDENCE_SYNTHESIS
        ),
        supported_question_types=semantics.question_types,
        evidence_dimensions=semantics.dimensions,
        supported_claims=semantics.claims,
        limitations=semantics.limitations,
        fallback_capabilities=semantics.fallbacks,
        auto_expandable=semantics.auto_expandable,
        allow_direct_entities=(
            workflow.requires_entities or any(req.requires_entities for req in workflow.parameter_requirements)
        ),
        supports_result_selection=workflow.supports_result_selection,
        program_default_fields=_PROGRAM_DEFAULT_FIELDS.get(
            capability,
            frozenset(),
        ),
        subsumes_capabilities=_SUBSUMED_CAPABILITIES.get(
            capability,
            frozenset(),
        ),
    )

def capability_for(
    capability: Capability | str,
) -> CapabilitySpec[Any, TaskOutcomeV2]:
    return CAPABILITY_REGISTRY[Capability(capability)]

def capability_catalog() -> list[dict[str, Any]]:
    return [
        {
            "capability": capability.value,
            "title": spec.title,
            "description": spec.description,
            "input_resources": sorted(item.value for item in spec.input_resources),
            "required_input_resources": sorted(
                item.value for item in spec.required_input_resources
            ),
            "alternative_input_resource_groups": [
                sorted(item.value for item in group)
                for group in spec.alternative_input_resource_groups
            ],
            "output_resources": sorted(item.value for item in spec.output_resources),
            "allow_direct_entities": spec.allow_direct_entities,
            "supports_result_selection": spec.supports_result_selection,
            "supported_question_types": sorted(item.value for item in spec.supported_question_types),
            "evidence_dimensions": sorted(item.value for item in spec.evidence_dimensions),
            "supported_claims": list(spec.supported_claims),
            "limitations": list(spec.limitations),
            "fallback_capabilities": [item.value for item in spec.fallback_capabilities],
            "auto_expandable": spec.auto_expandable,
            "subsumes_capabilities": sorted(item.value for item in spec.subsumes_capabilities),
        }
        for capability, spec in CAPABILITY_REGISTRY.items()
    ]

def migration_coverage() -> dict[str, Any]:
    """Compatibility metadata for health endpoints; the migration is complete."""
    return {
        "migrated": len(CAPABILITY_REGISTRY),
        "total": len(Capability),
        "complete": set(CAPABILITY_REGISTRY) == set(Capability),
        "capabilities": sorted(item.value for item in CAPABILITY_REGISTRY),
    }

def normalize_capability_intent(
    *,
    node_id: str,
    objective: str,
    capability: Capability,
    intent: Any,
    input_refs: tuple[InputReferenceV2, ...],
    result_selection: ResultSelectionV2 | None,
    current_year: int,
) -> NormalizedIntent[Any]:
    spec = capability_for(capability)
    validated = spec.intent_model.model_validate(intent)
    return cast(
        NormalizedIntent[Any],
        spec.compiler(
            node_id,
            objective,
            validated,
            input_refs,
            result_selection,
            current_year,
        ),
    )
