# -*- coding: utf-8 -*-
"""Finite-catalog industry selection with exact V2 model contracts.

The model performs two bounded semantic operations:
1. decompose the user's topic into value-chain benefit roles;
2. bind those roles to IDs from the complete live board catalog.

Catalog identity, cardinality, membership, resource publication and rendering
are program-owned.  No retrieval index, keyword route or partial catalog is
used by this module.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Awaitable, Callable, Mapping

from pydantic import BaseModel

from src.agent.orchestrator_v2.contracts import (
    AgentErrorCode,
    OrchestratorV2Error,
    RepairIssueV2,
)
from src.agent.orchestrator_v2.planner import (
    ExactContractValidationError,
    call_model_exact_v2,
)
from src.agent.result_contracts import (
    DomainBoardBindingV2,
    DomainCatalogSelectionV2,
    DomainCollectionCoverageV2,
    DomainCollectionV2,
    DomainResultSelectionV2,
    DomainSelectionAssumptionV2,
    IndustryBenefitOutlineV2,
)
from src.agent.task_workflows import (
    ResolvedTask,
    ResultSelectionMode,
    ResultSelectionSpec,
)
from src.services.domain_board_catalog import domain_board_catalog_snapshot_id


Completion = Callable[..., Awaitable[Any]]
ProcessorProgress = Callable[[int, int, dict[str, Any]], Awaitable[None]]

logger = logging.getLogger(__name__)

INDUSTRY_CATALOG_SELECTION_VERSION = "v10"


_BENEFIT_OUTLINE_PROMPT = """\
你是产业受益链拆解器。只根据用户明确提出的产业主题，形成可用于实时板块目录映射的紧凑产业链角色。

规则：
1. topic 保留用户主题；selection_objective 只复述本轮受益领域选择目标。
2. roles 描述具体产品、部件、软件、服务或基础设施环节，不得输出股票、公司或板块代码。
3. role_id 使用稳定、简短的小写英文标识；label 使用中文业务名称。
4. benefit_mechanism 只说明该环节如何受益，不得编造订单、收入、客户、市场份额或当前行情。
5. tier=1 表示受益最直接，tier=2 至 tier=4 依次降低。
6. 不得输出工具名、调用顺序、批次、超时、重试、缓存或数据源字段。
7. 必须按 submit_industry_benefit_outline_v2 的精确 Schema 返回唯一结构化结果。\
"""


_CATALOG_SELECTION_PROMPT = """\
你是有限实时板块目录选择器。输入 project_boards 是本轮完整目录，你只能返回其中真实存在的 board_id。

规则：
1. 必须在全部 project_boards 中完成语义比较，不得创建、改写或猜测 board_id。
2. 每个 item 只包含 board_id、role_id、tier；禁止返回板块名称、理由、置信度或额外字段。
3. role_id 必须来自 benefit_outline.roles；tier 必须与所引用 role 的 tier 完全一致。
4. 优先选择能直接表达具体受益环节的板块；名称相似但产业含义不相关的板块不得选择。
5. best_one 必须返回唯一一个；top_k 最多返回 max_items 个但不要求凑满；all_relevant 返回全部判断为相关的板块。
6. 若目录中没有直接表达某个 role 的板块，必须跳过该 role；不得用宽泛主题、政策概念或上下位领域替代具体受益环节。
7. 同一个 board_id 只能出现一次。若多个 role 只能映射到同一个 board_id，只保留 tier
   数字最小的 role；tier 相同时保留 benefit_outline.roles 中顺序最靠前的 role。
   不得因无法确定而返回目录之外的替代项。
8. 必须按 submit_domain_catalog_selection_v2 的精确 Schema 返回唯一结构化结果。\
"""


def _normalized_text(value: str) -> str:
    return re.sub(r"[\s*_`#>]+", "", str(value or "")).replace("／", "/").casefold()


def _domain_labels(task: ResolvedTask) -> list[str]:
    values = task.parameters.get("domains")
    if not isinstance(values, list):
        return []
    labels: list[str] = []
    for value in values:
        label = (
            value
            if isinstance(value, str)
            else value.get("label")
            if isinstance(value, Mapping)
            else ""
        )
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
        "topic": _normalized_text(
            str(task.parameters.get("query") or task.candidate.objective)
        ),
        "root_topics": [
            _normalized_text(value)
            for value in _domain_labels(task)
        ],
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
    return (
        f"industry_catalog_selection:"
        f"{INDUSTRY_CATALOG_SELECTION_VERSION}:{digest}"
    )


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
        board_id = str(
            raw.get("sector_code") or raw.get("board_id") or ""
        ).strip()
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
        boards.append({
            "board_id": board_id,
            "name": name,
            "main_flow_rank": _integer(raw.get("main_flow_rank")),
            "main_net_inflow": _number(raw.get("main_net_inflow")),
            "main_net_inflow_pct": _number(raw.get("main_net_inflow_pct")),
            "pct_chg": _number(raw.get("pct_chg")),
        })
    boards.sort(key=lambda item: (item["board_id"], item["name"]))
    computed_snapshot = domain_board_catalog_snapshot_id([
        {"sector_code": item["board_id"], "name": item["name"]}
        for item in boards
    ])
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
        await progress(completed, total, {
            "stage": stage,
            "status": status,
            "summary": summary,
            "error_code": error_code,
        })
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
            issues.append(RepairIssueV2(
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
            ))
        else:
            first_board_index[item.board_id] = index
        if item.board_id not in board_ids:
            issues.append(RepairIssueV2(
                pointer=f"/items/{index}/board_id",
                code="unknown_board_id",
                expected="one of project_boards[].board_id",
                message=f"unknown board_id: {item.board_id}",
            ))
        role = roles.get(item.role_id)
        if role is None:
            issues.append(RepairIssueV2(
                pointer=f"/items/{index}/role_id",
                code="unknown_role_id",
                expected="one of benefit_outline.roles[].role_id",
                allowed=tuple(roles),
                message=f"unknown role_id: {item.role_id}",
            ))
        elif item.tier != role.tier:
            issues.append(RepairIssueV2(
                pointer=f"/items/{index}/tier",
                code="role_tier_mismatch",
                expected=f"integer equal to role tier {role.tier}",
                allowed=(role.tier,),
                message=(
                    f"tier {item.tier} does not match "
                    f"{item.role_id}.tier={role.tier}"
                ),
            ))
    count = len(value.items)
    if selection.mode == ResultSelectionMode.BEST_ONE and count != 1:
        issues.append(RepairIssueV2(
            pointer="/items",
            code="selection_cardinality",
            expected="exactly one item",
            message=f"best_one returned {count} items",
        ))
    if (
        selection.mode == ResultSelectionMode.TOP_K
        and count > int(selection.max_items or 0)
    ):
        issues.append(RepairIssueV2(
            pointer="/items",
            code="selection_cardinality",
            expected=f"at most {selection.max_items} items",
            message=f"top_k returned {count} items",
        ))
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
            issues.append(RepairIssueV2(
                pointer=f"/roles/{index}/role_id",
                code="duplicate_role_id",
                expected="a role_id not already used in roles",
                message=(
                    f"role_id {role.role_id} duplicates "
                    f"/roles/{previous_role}/role_id"
                ),
            ))
        else:
            first_role_index[role.role_id] = index
        label_key = role.label.casefold()
        previous_label = first_label_index.get(label_key)
        if previous_label is not None:
            issues.append(RepairIssueV2(
                pointer=f"/roles/{index}/label",
                code="duplicate_role_label",
                expected="a role label not already used in roles",
                message=(
                    f"role label {role.label} duplicates "
                    f"/roles/{previous_label}/label"
                ),
            ))
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
        return (
            "目录选择模型未返回完整结构化结果，单次定点修复仍未通过；"
            "本轮已停止且未发布部分集合"
        )
    if error.code == AgentErrorCode.SYNTHESIS_FAILED:
        return "上游模型调用失败；本轮已停止且未发布部分集合"
    if error.code == AgentErrorCode.RESOURCE_UNAVAILABLE:
        return "实时板块目录不可用；本轮已停止且未发布部分集合"
    return "内部执行未形成可发布的结构化结果；本轮已停止"


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

    requested_topic = str(
        task.parameters.get("query") or task.candidate.objective
    ).strip()
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

    role_by_id = {
        role.role_id: role
        for role in outline_value.roles
    }
    try:
        selection_value, _raw_selection, selection_repair = (
            await call_model_exact_v2(
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

    role_order = {
        role.role_id: index
        for index, role in enumerate(outline_value.roles)
    }
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
        rationale = (
            f"{board['name']}对应“{role.label}”环节；"
            f"{role.benefit_mechanism}"
        )
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
        legacy_items.append({
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
            "source_name": str(
                catalog.get("source") or "项目实时板块目录"
            ),
            "source_date": str(catalog.get("data_time") or ""),
            "selection_basis": "complete_catalog_id_binding_v2",
        })

    raw_assumptions = task.parameters.get("_assumptions")
    assumptions = tuple(
        DomainSelectionAssumptionV2.model_validate(value)
        for value in (
            raw_assumptions
            if isinstance(raw_assumptions, list)
            else []
        )
        if isinstance(value, Mapping)
    )
    result_selection = DomainResultSelectionV2.model_validate(
        _selection_payload(selection)
    )
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


__all__ = [
    "INDUSTRY_CATALOG_SELECTION_VERSION",
    "rank_project_board_domains_v2",
]
