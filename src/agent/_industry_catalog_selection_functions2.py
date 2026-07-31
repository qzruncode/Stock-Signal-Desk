"""Function group 2 extracted from src/agent/industry_catalog_selection.py."""

from __future__ import annotations

from src.agent.industry_catalog_selection import (
    hashlib,
    json,
    logging,
    re,
    Any,
    Awaitable,
    Callable,
    Mapping,
    BaseModel,
    AgentErrorCode,
    OrchestratorV2Error,
    RepairIssueV2,
    ExactContractValidationError,
    call_model_exact_v2,
    DomainBoardBindingV2,
    DomainCatalogSelectionV2,
    DomainCollectionCoverageV2,
    DomainCollectionV2,
    DomainResultSelectionV2,
    DomainSelectionAssumptionV2,
    IndustryBenefitOutlineV2,
    ResolvedTask,
    ResultSelectionMode,
    ResultSelectionSpec,
    domain_board_catalog_snapshot_id,
    Completion,
    ProcessorProgress,
    logger,
    INDUSTRY_CATALOG_SELECTION_VERSION,
    _BENEFIT_OUTLINE_PROMPT,
    _CATALOG_SELECTION_PROMPT,
    __all__,
 )

__all__ = ['rank_project_board_domains_v2']

async def rank_project_board_domains_v2(
    task: ResolvedTask,
    catalog: dict[str, Any],
    llm_cfg: dict[str, Any],
    completion: Completion,
    progress: ProcessorProgress | None = None,
) -> dict[str, Any]:
    selection = task.result_selection
    if selection is None:
        raise ValueError("industry_research requires typed result_selection")

    try:
        boards, snapshot_id = _normalized_boards(catalog)
    except OrchestratorV2Error as exc:
        await _report(
            progress,
            completed=0,
            total=4,
            stage="catalog_loading",
            status="failed",
            summary=_public_error_summary(exc),
            error_code=exc.code.value,
        )
        return _failure_result(
            catalog_total=0,
            catalog_supplied=0,
            snapshot_id="unavailable",
            selection=selection,
            error=exc,
        )

    board_by_id = {item["board_id"]: item for item in boards}
    await _report(
        progress,
        completed=1,
        total=4,
        stage="catalog_loading",
        status="succeeded",
        summary=f"完整实时板块目录已载入：{len(boards)} 个板块",
    )

    cache_key = _cache_key(task, llm_cfg, snapshot_id, selection)
    cached = _load_cache(
        cache_key,
        snapshot_id=snapshot_id,
        board_ids=set(board_by_id),
    )
    if cached is not None:
        await _report(
            progress,
            completed=4,
            total=4,
            stage="resource_published",
            status="succeeded",
            summary="已复用同一目录快照下通过完整校验的板块集合",
        )
        return cached

    requested_topic = str(task.parameters.get("query") or task.candidate.objective).strip()
    root_topics = _domain_labels(task)
    repairs: list[dict[str, Any]] = []

    await _report(
        progress,
        completed=1,
        total=4,
        stage="benefit_outline",
        status="started",
        summary="正在拆解产业受益链条",
    )

    async def outline_heartbeat(elapsed: int) -> None:
        await _report(
            progress,
            completed=1,
            total=4,
            stage="benefit_outline",
            status="started",
            summary=f"模型仍在拆解产业受益链条，已等待 {elapsed} 秒",
        )

    try:
        outline_value, _raw_outline, outline_repair = await call_model_exact_v2(
            llm_cfg=llm_cfg,
            completion=completion,
            function_name="submit_industry_benefit_outline_v2",
            description="提交产业受益链条的紧凑强类型拆解。",
            model=IndustryBenefitOutlineV2,
            system_prompt=_BENEFIT_OUTLINE_PROMPT,
            semantic_context={
                "requested_topic": requested_topic,
                "explicit_subjects": root_topics,
                "result_selection": _selection_payload(selection),
            },
            node_id=task.task_id,
            value_validator=_outline_issues,
            progress_observer=outline_heartbeat,
            provider_error_code=AgentErrorCode.SYNTHESIS_FAILED,
            schema_error_code=AgentErrorCode.PLANNER_SCHEMA_INVALID,
            max_tokens=1_800,
            # The configured reasoning gateway has demonstrated that it can
            # place visible analysis in forced function arguments and then
            # delay or truncate the actual object.  This capability therefore
            # uses the exact same generated Schema through the provider's
            # content channel and keeps Pydantic as the acceptance boundary.
            contract_transport="json_content",
        )
        assert isinstance(outline_value, IndustryBenefitOutlineV2)
        if outline_repair is not None:
            repairs.append(outline_repair.model_dump(mode="json"))
    except OrchestratorV2Error as exc:
        await _report(
            progress,
            completed=1,
            total=4,
            stage="benefit_outline",
            status="failed",
            summary=_public_error_summary(exc),
            error_code=exc.code.value,
        )
        return _failure_result(
            catalog_total=len(boards),
            # Loaded locally, but not yet sent to the catalog-selection model.
            catalog_supplied=0,
            snapshot_id=snapshot_id,
            selection=selection,
            error=exc,
        )

    await _report(
        progress,
        completed=2,
        total=4,
        stage="benefit_outline",
        status="succeeded",
        summary=f"产业受益链条已形成：{len(outline_value.roles)} 个环节",
    )
    await _report(
        progress,
        completed=2,
        total=4,
        stage="catalog_mapping",
        status="started",
        summary=f"正在完整目录的 {len(boards)} 个板块中选择真实板块 ID",
    )

    async def mapping_heartbeat(elapsed: int) -> None:
        await _report(
            progress,
            completed=2,
            total=4,
            stage="catalog_mapping",
            status="started",
            summary=f"模型仍在完整目录中选择板块 ID，已等待 {elapsed} 秒",
        )

    role_by_id = {role.role_id: role for role in outline_value.roles}
    try:
        selection_value, _raw_selection, selection_repair = await call_model_exact_v2(
            llm_cfg=llm_cfg,
            completion=completion,
            function_name="submit_domain_catalog_selection_v2",
            description="从完整实时目录提交紧凑板块 ID 绑定。",
            model=DomainCatalogSelectionV2,
            system_prompt=_CATALOG_SELECTION_PROMPT,
            semantic_context={
                "requested_topic": requested_topic,
                "benefit_outline": outline_value.model_dump(mode="json"),
                "result_selection": _selection_payload(selection),
                "catalog_snapshot_id": snapshot_id,
                "project_boards": [
                    {
                        "board_id": item["board_id"],
                        "name": item["name"],
                    }
                    for item in boards
                ],
            },
            node_id=task.task_id,
            value_validator=lambda value: _selection_issues(
                value,
                board_ids=set(board_by_id),
                roles=role_by_id,
                selection=selection,
            ),
            progress_observer=mapping_heartbeat,
            provider_error_code=AgentErrorCode.SYNTHESIS_FAILED,
            schema_error_code=AgentErrorCode.PLANNER_SCHEMA_INVALID,
            # The provider may count visible analysis tokens against the
            # same output budget as the strict tool payload. Scale only
            # from the program-owned cardinality contract.
            max_tokens=_selection_output_budget(selection),
            contract_transport="json_content",
        )
        assert isinstance(selection_value, DomainCatalogSelectionV2)
        if selection_repair is not None:
            repairs.append(selection_repair.model_dump(mode="json"))
    except OrchestratorV2Error as exc:
        await _report(
            progress,
            completed=2,
            total=4,
            stage="catalog_mapping",
            status="failed",
            summary=_public_error_summary(exc),
            error_code=exc.code.value,
        )
        failure = _failure_result(
            catalog_total=len(boards),
            catalog_supplied=len(boards),
            snapshot_id=snapshot_id,
            selection=selection,
            error=exc,
        )
        failure["repairs"] = [
            *repairs,
            *failure["repairs"],
        ]
        return failure

    await _report(
        progress,
        completed=3,
        total=4,
        stage="catalog_mapping",
        status="succeeded",
        summary=f"模型返回 {len(selection_value.items)} 个紧凑板块 ID",
    )
    await _report(
        progress,
        completed=3,
        total=4,
        stage="result_validation",
        status="started",
        summary="正在校验目录快照、板块 ID、受益角色和结果数量",
    )

    role_order = {role.role_id: index for index, role in enumerate(outline_value.roles)}
    selected_items = sorted(
        selection_value.items,
        key=lambda item: (
            item.tier,
            role_order[item.role_id],
            board_by_id[item.board_id]["name"],
            item.board_id,
        ),
    )
    if selection.mode != ResultSelectionMode.ALL_RELEVANT:
        selected_items = selected_items[: int(selection.max_items or 0)]

    bindings: list[DomainBoardBindingV2] = []
    legacy_items: list[dict[str, Any]] = []
    for selected in selected_items:
        board = board_by_id[selected.board_id]
        role = role_by_id[selected.role_id]
        rationale = f"{board['name']}对应“{role.label}”环节；" f"{role.benefit_mechanism}"
        binding = DomainBoardBindingV2(
            board_id=selected.board_id,
            board_name=board["name"],
            role_id=selected.role_id,
            role_label=role.label,
            tier=selected.tier,
            rationale=rationale,
            main_flow_rank=board["main_flow_rank"],
            main_net_inflow=board["main_net_inflow"],
            main_net_inflow_pct=board["main_net_inflow_pct"],
            pct_chg=board["pct_chg"],
        )
        bindings.append(binding)
        legacy_items.append(
            {
                "label": binding.board_name,
                "board_name": binding.board_name,
                "board_code": binding.board_id,
                "board_queries": [binding.board_name],
                "mapping_type": "catalog_binding",
                "unresolved_parts": [],
                "role_id": binding.role_id,
                "role_label": binding.role_label,
                "tier": binding.tier,
                "rationale": binding.rationale,
                "main_flow_rank": binding.main_flow_rank,
                "main_net_inflow": binding.main_net_inflow,
                "main_net_inflow_pct": binding.main_net_inflow_pct,
                "pct_chg": binding.pct_chg,
                "source_name": str(catalog.get("source") or "项目实时板块目录"),
                "source_date": str(catalog.get("data_time") or ""),
                "selection_basis": "complete_catalog_id_binding_v2",
            }
        )

    raw_assumptions = task.parameters.get("_assumptions")
    assumptions = tuple(
        DomainSelectionAssumptionV2.model_validate(value)
        for value in (raw_assumptions if isinstance(raw_assumptions, list) else [])
        if isinstance(value, Mapping)
    )
    result_selection = DomainResultSelectionV2.model_validate(_selection_payload(selection))
    collection = DomainCollectionV2(
        catalog_snapshot_id=snapshot_id,
        requested_topic=requested_topic,
        benefit_outline=outline_value,
        boards=tuple(bindings),
        result_selection=result_selection,
        assumptions=assumptions,
        coverage=DomainCollectionCoverageV2(
            catalog_total=len(boards),
            catalog_supplied=len(boards),
            selected_count=len(bindings),
            binding_complete=True,
        ),
        source_name=str(catalog.get("source") or "项目实时板块目录"),
        source_date=str(catalog.get("data_time") or ""),
        lineage=(
            f"catalog:{snapshot_id}",
            "model:submit_industry_benefit_outline_v2",
            "model:submit_domain_catalog_selection_v2",
            "program:validated_domain_binding",
        ),
    )
    collection_payload = collection.model_dump(mode="json")
    resource_domains = [
        {
            "label": item.board_name,
            "board_queries": [item.board_name],
            "mapping_type": "catalog_binding",
            "rationale": item.rationale,
            "unresolved_parts": [],
        }
        for item in bindings
    ]
    result = {
        "success": True,
        "partial": bool(catalog.get("partial")),
        "errors": list(catalog.get("errors") or []),
        "warnings": list(catalog.get("warnings") or []),
        "items": legacy_items,
        "semantic_artifacts": [collection_payload],
        "resource_outputs": {
            "domain_collection": resource_domains,
        },
        "source_scope": "project_live_board_catalog",
        "source_name": collection.source_name,
        "source_date": collection.source_date,
        "catalog_snapshot_id": snapshot_id,
        "catalog_count": len(boards),
        "catalog_total": len(boards),
        "catalog_supplied": len(boards),
        "selected_count": len(bindings),
        "binding_complete": True,
        "coverage_complete": True,
        "ranking_complete": True,
        "result_selection": result_selection.model_dump(mode="json"),
        "benefit_outline": outline_value.model_dump(mode="json"),
        "coverage": collection.coverage.model_dump(mode="json"),
        "repairs": repairs,
        "cache_hit": False,
    }
    _save_cache(cache_key, result)
    await _report(
        progress,
        completed=4,
        total=4,
        stage="result_validation",
        status="succeeded",
        summary=f"{len(bindings)} 个板块全部通过 ID 和资源契约校验",
    )
    await _report(
        progress,
        completed=4,
        total=4,
        stage="resource_published",
        status="succeeded",
        summary="DomainCollectionV2 已发布，可供后续限定集合找股",
    )
    return result
