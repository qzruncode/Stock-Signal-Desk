"""Function group 1 extracted from src/agent/industry_catalog_selection.py."""

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

__all__ = ['_normalized_text', '_domain_labels', '_selection_payload', '_selection_output_budget', '_cache_key', '_load_cache', '_save_cache', '_number', '_integer', '_normalized_boards', '_report', '_selection_issues', '_outline_issues', '_failure_result', '_public_error_summary']

def _normalized_text(value: str) -> str:
    return re.sub(r"[\s*_`#>]+", "", str(value or "")).replace("／", "/").casefold()

def _domain_labels(task: ResolvedTask) -> list[str]:
    values = task.parameters.get("domains")
    if not isinstance(values, list):
        return []
    labels: list[str] = []
    for value in values:
        label = value if isinstance(value, str) else value.get("label") if isinstance(value, Mapping) else ""
        text = str(label or "").strip()
        if text and text not in labels:
            labels.append(text)
    return labels

def _selection_payload(selection: ResultSelectionSpec) -> dict[str, Any]:
    return {
        "mode": selection.mode.value,
        "max_items": selection.max_items,
    }

def _selection_output_budget(selection: ResultSelectionSpec) -> int:
    if selection.mode == ResultSelectionMode.BEST_ONE:
        return 6_000
    if selection.mode == ResultSelectionMode.TOP_K:
        requested = int(selection.max_items or 16)
        return min(32_000, max(6_000, 3_000 + requested * 80))
    return 32_000

def _cache_key(
    task: ResolvedTask,
    llm_cfg: Mapping[str, Any],
    snapshot_id: str,
    selection: ResultSelectionSpec,
) -> str | None:
    if not llm_cfg.get("api_base"):
        return None
    payload = {
        "version": INDUSTRY_CATALOG_SELECTION_VERSION,
        "model": str(llm_cfg.get("model") or ""),
        "snapshot_id": snapshot_id,
        "topic": _normalized_text(str(task.parameters.get("query") or task.candidate.objective)),
        "root_topics": [_normalized_text(value) for value in _domain_labels(task)],
        "result_selection": _selection_payload(selection),
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"industry_catalog_selection:" f"{INDUSTRY_CATALOG_SELECTION_VERSION}:{digest}"

def _load_cache(
    cache_key: str | None,
    *,
    snapshot_id: str,
    board_ids: set[str],
) -> dict[str, Any] | None:
    if not cache_key:
        return None
    try:
        from src.storage.manager import DatabaseManager

        cached = DatabaseManager.get_instance().get_tool_cache(cache_key)
        if not cached:
            return None
        payload = json.loads(bytes(cached["payload"]).decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("success") is not True:
            return None
        artifacts = payload.get("semantic_artifacts")
        if not isinstance(artifacts, list) or len(artifacts) != 1:
            return None
        resource = DomainCollectionV2.model_validate(artifacts[0])
        if resource.catalog_snapshot_id != snapshot_id:
            return None
        if {item.board_id for item in resource.boards} - board_ids:
            return None
        if not resource.coverage.binding_complete:
            return None
        return {**payload, "cache_hit": True}
    except Exception:
        logger.warning(
            "[IndustryCatalogSelectionV2] ignored invalid cache",
            exc_info=True,
        )
        return None

def _save_cache(cache_key: str | None, result: Mapping[str, Any]) -> None:
    if not cache_key or result.get("success") is not True:
        return
    try:
        artifacts = result.get("semantic_artifacts")
        if not isinstance(artifacts, list) or len(artifacts) != 1:
            return
        resource = DomainCollectionV2.model_validate(artifacts[0])
        if not resource.coverage.binding_complete:
            return
        from src.storage.manager import DatabaseManager

        DatabaseManager.get_instance().save_tool_cache(
            cache_key,
            json.dumps(
                dict(result),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8"),
        )
    except Exception:
        logger.warning(
            "[IndustryCatalogSelectionV2] failed to persist cache",
            exc_info=True,
        )

def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def _integer(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

def _normalized_boards(
    catalog: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    raw_boards = catalog.get("boards")
    if not isinstance(raw_boards, list) or not raw_boards:
        raise OrchestratorV2Error(
            AgentErrorCode.RESOURCE_UNAVAILABLE,
            "项目实时板块目录为空，无法形成板块集合。",
        )
    boards: list[dict[str, Any]] = []
    by_id: dict[str, str] = {}
    for index, raw in enumerate(raw_boards):
        if not isinstance(raw, Mapping):
            raise OrchestratorV2Error(
                AgentErrorCode.RESOURCE_UNAVAILABLE,
                f"项目实时板块目录第 {index + 1} 项不是结构化对象。",
            )
        board_id = str(raw.get("sector_code") or raw.get("board_id") or "").strip()
        name = str(raw.get("name") or "").strip()
        if not board_id or not name:
            raise OrchestratorV2Error(
                AgentErrorCode.RESOURCE_UNAVAILABLE,
                f"项目实时板块目录第 {index + 1} 项缺少板块 ID 或名称。",
            )
        previous_name = by_id.get(board_id)
        if previous_name is not None and previous_name != name:
            raise OrchestratorV2Error(
                AgentErrorCode.RESOURCE_UNAVAILABLE,
                f"实时板块 ID {board_id} 同时绑定多个名称。",
            )
        if previous_name is not None:
            continue
        by_id[board_id] = name
        boards.append(
            {
                "board_id": board_id,
                "name": name,
                "main_flow_rank": _integer(raw.get("main_flow_rank")),
                "main_net_inflow": _number(raw.get("main_net_inflow")),
                "main_net_inflow_pct": _number(raw.get("main_net_inflow_pct")),
                "pct_chg": _number(raw.get("pct_chg")),
            }
        )
    boards.sort(key=lambda item: (item["board_id"], item["name"]))
    computed_snapshot = domain_board_catalog_snapshot_id(
        [{"sector_code": item["board_id"], "name": item["name"]} for item in boards]
    )
    supplied_snapshot = str(catalog.get("catalog_snapshot_id") or "").strip()
    if supplied_snapshot and supplied_snapshot != computed_snapshot:
        raise OrchestratorV2Error(
            AgentErrorCode.RESOURCE_UNAVAILABLE,
            "实时板块目录快照指纹与目录内容不一致。",
        )
    return boards, computed_snapshot

async def _report(
    progress: ProcessorProgress | None,
    *,
    completed: int,
    total: int,
    stage: str,
    status: str,
    summary: str,
    error_code: str | None = None,
) -> None:
    if progress is None:
        return
    try:
        await progress(
            completed,
            total,
            {
                "stage": stage,
                "status": status,
                "summary": summary,
                "error_code": error_code,
            },
        )
    except Exception:
        logger.warning(
            "[IndustryCatalogSelectionV2] progress callback failed",
            exc_info=True,
        )

def _selection_issues(
    value: BaseModel,
    *,
    board_ids: set[str],
    roles: Mapping[str, Any],
    selection: ResultSelectionSpec,
) -> None:
    assert isinstance(value, DomainCatalogSelectionV2)
    issues: list[RepairIssueV2] = []
    first_board_index: dict[str, int] = {}
    for index, item in enumerate(value.items):
        previous_index = first_board_index.get(item.board_id)
        if previous_index is not None:
            issues.append(
                RepairIssueV2(
                    pointer=f"/items/{index}/board_id",
                    code="duplicate_board_id",
                    expected=(
                        f"delete the entire /items/{index} object and keep "
                        f"/items/{previous_index}; do not invent a replacement "
                        "board_id"
                    ),
                    message=(
                        f"remove /items/{index} because board_id {item.board_id} "
                        f"duplicates /items/{previous_index}/board_id"
                    ),
                )
            )
        else:
            first_board_index[item.board_id] = index
        if item.board_id not in board_ids:
            issues.append(
                RepairIssueV2(
                    pointer=f"/items/{index}/board_id",
                    code="unknown_board_id",
                    expected="one of project_boards[].board_id",
                    message=f"unknown board_id: {item.board_id}",
                )
            )
        role = roles.get(item.role_id)
        if role is None:
            issues.append(
                RepairIssueV2(
                    pointer=f"/items/{index}/role_id",
                    code="unknown_role_id",
                    expected="one of benefit_outline.roles[].role_id",
                    allowed=tuple(roles),
                    message=f"unknown role_id: {item.role_id}",
                )
            )
        elif item.tier != role.tier:
            issues.append(
                RepairIssueV2(
                    pointer=f"/items/{index}/tier",
                    code="role_tier_mismatch",
                    expected=f"integer equal to role tier {role.tier}",
                    allowed=(role.tier,),
                    message=(f"tier {item.tier} does not match " f"{item.role_id}.tier={role.tier}"),
                )
            )
    count = len(value.items)
    if selection.mode == ResultSelectionMode.BEST_ONE and count != 1:
        issues.append(
            RepairIssueV2(
                pointer="/items",
                code="selection_cardinality",
                expected="exactly one item",
                message=f"best_one returned {count} items",
            )
        )
    if selection.mode == ResultSelectionMode.TOP_K and count > int(selection.max_items or 0):
        issues.append(
            RepairIssueV2(
                pointer="/items",
                code="selection_cardinality",
                expected=f"at most {selection.max_items} items",
                message=f"top_k returned {count} items",
            )
        )
    if issues:
        raise ExactContractValidationError(tuple(issues))

def _outline_issues(value: BaseModel) -> None:
    assert isinstance(value, IndustryBenefitOutlineV2)
    issues: list[RepairIssueV2] = []
    first_role_index: dict[str, int] = {}
    first_label_index: dict[str, int] = {}
    for index, role in enumerate(value.roles):
        previous_role = first_role_index.get(role.role_id)
        if previous_role is not None:
            issues.append(
                RepairIssueV2(
                    pointer=f"/roles/{index}/role_id",
                    code="duplicate_role_id",
                    expected="a role_id not already used in roles",
                    message=(f"role_id {role.role_id} duplicates " f"/roles/{previous_role}/role_id"),
                )
            )
        else:
            first_role_index[role.role_id] = index
        label_key = role.label.casefold()
        previous_label = first_label_index.get(label_key)
        if previous_label is not None:
            issues.append(
                RepairIssueV2(
                    pointer=f"/roles/{index}/label",
                    code="duplicate_role_label",
                    expected="a role label not already used in roles",
                    message=(f"role label {role.label} duplicates " f"/roles/{previous_label}/label"),
                )
            )
        else:
            first_label_index[label_key] = index
    if issues:
        raise ExactContractValidationError(tuple(issues))

def _failure_result(
    *,
    catalog_total: int,
    catalog_supplied: int,
    snapshot_id: str,
    selection: ResultSelectionSpec,
    error: OrchestratorV2Error,
) -> dict[str, Any]:
    repair = error.metadata.get("repair")
    return {
        "success": False,
        "partial": False,
        "error_code": error.code.value,
        "errors": [str(error)],
        "warnings": [],
        "items": [],
        "semantic_artifacts": [],
        "resource_outputs": {},
        "source_scope": "project_live_board_catalog",
        "catalog_snapshot_id": snapshot_id,
        "catalog_count": catalog_total,
        "catalog_total": catalog_total,
        "catalog_supplied": catalog_supplied,
        "selected_count": 0,
        "binding_complete": False,
        "coverage_complete": False,
        "ranking_complete": False,
        "coverage": {
            "catalog_total": catalog_total,
            "catalog_supplied": catalog_supplied,
            "selected_count": 0,
            "binding_complete": False,
        },
        "result_selection": _selection_payload(selection),
        "repairs": [repair] if isinstance(repair, Mapping) else [],
        "cache_hit": False,
    }

def _public_error_summary(error: OrchestratorV2Error) -> str:
    if error.code == AgentErrorCode.PLANNER_SCHEMA_INVALID:
        return "目录选择模型未返回完整结构化结果，单次定点修复仍未通过；" "本轮已停止且未发布部分集合"
    if error.code == AgentErrorCode.SYNTHESIS_FAILED:
        return "上游模型调用失败；本轮已停止且未发布部分集合"
    if error.code == AgentErrorCode.RESOURCE_UNAVAILABLE:
        return "实时板块目录不可用；本轮已停止且未发布部分集合"
    return "内部执行未形成可发布的结构化结果；本轮已停止"
