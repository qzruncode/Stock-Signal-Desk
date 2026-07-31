# -*- coding: utf-8 -*-
"""Domain ranking processors backed by project catalogs or public evidence."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Mapping

from pydantic import ValidationError

from src.agent.industry_catalog_selection import rank_project_board_domains_v2
from src.agent.result_processor_models import (
    _PROJECT_BOARD_RANKING_SYSTEM_PROMPT,
    _PROJECT_BOARD_RANKING_TOOL,
    _RANKED_DOMAIN_SYSTEM_PROMPT,
    _RANKED_DOMAIN_TOOL,
    Completion,
    ProcessorProgress,
    RankedDomainCandidate,
    RankedProjectBoard,
    apply_result_selection,
    catalog_result,
    domain_labels,
    load_project_board_ranking_cache,
    normalized_text,
    project_board_cache_key,
    project_board_snapshot_id,
    response_payload,
    result_selection,
    save_project_board_ranking_cache,
    selection_payload,
    semantic_failure_code,
)
from src.llm.anthropic_gateway import build_litellm_kwargs
from src.agent.task_workflows import ResolvedTask


logger = logging.getLogger(__name__)


async def rank_project_board_domains_v1(
    task: ResolvedTask,
    catalog: dict[str, Any],
    llm_cfg: dict[str, Any],
    completion: Completion,
    progress: ProcessorProgress | None = None,
) -> dict[str, Any]:
    selection = result_selection(task)
    boards = [
        {
            "name": str(item.get("name") or "").strip(),
            "sector_code": str(item.get("sector_code") or "").strip(),
            "main_flow_rank": item.get("main_flow_rank"),
            "main_net_inflow": item.get("main_net_inflow"),
            "main_net_inflow_pct": item.get("main_net_inflow_pct"),
            "pct_chg": item.get("pct_chg"),
        }
        for item in catalog.get("boards") or []
        if isinstance(item, Mapping) and str(item.get("name") or "").strip()
    ]
    if not boards:
        return {
            "success": False,
            "partial": False,
            "errors": ["项目实时板块目录为空，无法形成板块排序。"],
            "items": [],
            "semantic_artifacts": [],
            "resource_outputs": {"domain_collection": []},
            "catalog_count": 0,
            "batch_total": 0,
            "batch_completed": 0,
            "coverage_complete": False,
            "ranking_complete": False,
        }

    by_name = {item["name"]: item for item in boards}
    snapshot_id = project_board_snapshot_id(boards, catalog)
    cache_key = project_board_cache_key(task, llm_cfg, snapshot_id)
    cached_result = load_project_board_ranking_cache(cache_key)
    cached_names = {
        str(item.get("board_name") or item.get("label") or "").strip()
        for item in (cached_result or {}).get("items") or []
        if isinstance(item, Mapping)
    }
    if (
        cached_result is not None
        and cached_result.get("catalog_snapshot_id") == snapshot_id
        and bool(cached_names)
        and cached_names <= set(by_name)
    ):
        return cached_result

    async def request_ranking() -> list[RankedProjectBoard]:
        request = {
            "requested_topic": str(task.parameters.get("query") or task.candidate.objective),
            "root_topics": domain_labels(task),
            "result_selection": selection_payload(selection),
            "project_boards": [board["name"] for board in boards],
        }
        kwargs = build_litellm_kwargs(
            llm_cfg,
            stream=False,
            messages=[
                {"role": "system", "content": _PROJECT_BOARD_RANKING_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
            ],
            tools=[_PROJECT_BOARD_RANKING_TOOL],
            tool_choice={"type": "function", "function": {"name": "submit_ranked_project_boards"}},
            temperature=0,
            max_tokens=4_000,
        )
        response = await completion(**kwargs)
        payload = response_payload(response, "submit_ranked_project_boards")
        raw_boards = payload.get("boards")
        if not isinstance(raw_boards, list):
            raise ValueError("ranked project boards response is not a list")
        ranked: list[RankedProjectBoard] = []
        for raw in raw_boards:
            try:
                candidate = RankedProjectBoard.model_validate(raw)
            except ValidationError:
                continue
            if candidate.board_name in by_name and candidate.confidence >= 0.55:
                ranked.append(candidate)
        return ranked

    ranked_candidates: list[RankedProjectBoard] | None = None
    model_failure: BaseException | None = None
    logger.info(
        "[ResultProcessor] project-board direct model request snapshot=%s catalog_count=%s payload_count=%s",
        snapshot_id,
        len(boards),
        len(boards),
    )
    try:
        ranked_candidates = await request_ranking()
    except asyncio.CancelledError:
        raise
    except BaseException as exc:
        model_failure = exc
        logger.warning(
            "[ResultProcessor] project-board direct model request failed snapshot=%s error_code=%s",
            snapshot_id,
            semantic_failure_code(exc),
            exc_info=(type(exc), exc, exc.__traceback__),
        )

    if ranked_candidates is not None and progress is not None:
        try:
            await progress(1, 1, {"stage": "complete_catalog", "success": True})
        except Exception:
            logger.warning("[ResultProcessor] project-board progress callback failed", exc_info=True)

    selected: dict[str, dict[str, Any]] = {}
    for candidate in ranked_candidates or []:
        board = by_name.get(candidate.board_name)
        if board is None:
            continue
        item = {
            "label": candidate.board_name,
            "board_name": candidate.board_name,
            "board_code": board["sector_code"],
            "board_queries": [candidate.board_name],
            "mapping_type": "catalog_binding",
            "unresolved_parts": [],
            "tier": candidate.tier,
            "rationale": candidate.rationale,
            "confidence": candidate.confidence,
            "main_flow_rank": board.get("main_flow_rank"),
            "main_net_inflow": board.get("main_net_inflow"),
            "main_net_inflow_pct": board.get("main_net_inflow_pct"),
            "pct_chg": board.get("pct_chg"),
            "source_name": str(catalog.get("source") or "项目实时板块目录"),
            "source_date": str(catalog.get("data_time") or ""),
            "selection_basis": "project_live_board_catalog",
        }
        previous = selected.get(candidate.board_name)
        if previous is None or (int(item["tier"]), -float(item["confidence"])) < (
            int(previous["tier"]),
            -float(previous["confidence"]),
        ):
            selected[candidate.board_name] = item

    items = sorted(
        selected.values(),
        key=lambda item: (int(item["tier"]), -float(item["confidence"]), str(item["label"])),
    )
    items = apply_result_selection(items, selection)
    coverage_complete = ranked_candidates is not None
    failed_batches = (
        []
        if model_failure is None
        else [
            {"batch": 1, "error_code": semantic_failure_code(model_failure), "error_type": type(model_failure).__name__}
        ]
    )
    diagnostics = {
        "source_scope": "project_live_board_catalog",
        "catalog_snapshot_id": snapshot_id,
        "catalog_count": len(boards),
        "batch_total": 1,
        "batch_completed": 1 if coverage_complete else 0,
        "coverage_complete": coverage_complete,
        "ranking_complete": coverage_complete,
        "failed_batches": failed_batches,
        "cache_hit": False,
        "result_selection": selection_payload(selection),
    }
    if not coverage_complete:
        return {
            "success": False,
            "partial": bool(items),
            "errors": [
                "完整实时板块目录已经一次性交给模型，但本次模型调用未能返回有效结构化判断；"
                "因此没有发布可供后续找股使用的领域集合。"
            ],
            "warnings": [f"完整目录模型判断 {semantic_failure_code(model_failure)}"],
            "items": items,
            "semantic_artifacts": [],
            "resource_outputs": {},
            **diagnostics,
        }
    if not items:
        return {
            "success": False,
            "partial": False,
            "errors": ["完整目录中没有选出通过结构校验的相关板块。"],
            "items": [],
            "semantic_artifacts": [],
            "resource_outputs": {},
            **diagnostics,
        }

    groups = [
        {
            "tier": tier,
            "domains": [
                {
                    "label": item["label"],
                    "tier": item["tier"],
                    "board_queries": item["board_queries"],
                    "mapping_type": item["mapping_type"],
                    "rationale": item["rationale"],
                    "unresolved_parts": [],
                }
                for item in items
                if item["tier"] == tier
            ],
        }
        for tier in sorted({int(item["tier"]) for item in items})
    ]
    artifact = {
        "type": "ranked_domains",
        "topic": str(task.parameters.get("query") or task.candidate.objective),
        "root_topics": domain_labels(task),
        "source_scope": "project_live_board_catalog",
        "catalog_count": len(boards),
        "catalog_snapshot_id": snapshot_id,
        "batch_total": 1,
        "batch_completed": 1,
        "coverage_complete": True,
        "ranking_complete": True,
        "result_selection": selection_payload(selection),
        "groups": groups,
    }
    result = {
        "success": True,
        "partial": bool(catalog.get("partial")),
        "errors": list(catalog.get("errors") or []),
        "warnings": list(catalog.get("warnings") or []),
        "items": items,
        "semantic_artifacts": [artifact],
        "resource_outputs": {
            "domain_collection": [
                {
                    "label": item["label"],
                    "board_queries": item["board_queries"],
                    "mapping_type": item["mapping_type"],
                    "rationale": item["rationale"],
                    "unresolved_parts": [],
                }
                for item in items
            ]
        },
        "source_scope": "project_live_board_catalog",
        "result_selection": selection_payload(selection),
        "source_name": str(catalog.get("source") or "项目实时板块目录"),
        "source_date": str(catalog.get("data_time") or ""),
        **diagnostics,
    }
    save_project_board_ranking_cache(cache_key, result)
    return result


def industry_catalog_mapping_mode() -> str:
    value = str(os.getenv("AGENT_INDUSTRY_CATALOG_MAPPING_MODE", "v2")).strip().lower()
    return "v1" if value == "v1" else "v2"


async def rank_project_board_domains(
    task: ResolvedTask,
    catalog: dict[str, Any],
    llm_cfg: dict[str, Any],
    completion: Completion,
    progress: ProcessorProgress | None = None,
) -> dict[str, Any]:
    if industry_catalog_mapping_mode() == "v1":
        return await rank_project_board_domains_v1(task, catalog, llm_cfg, completion, progress)
    return await rank_project_board_domains_v2(task, catalog, llm_cfg, completion, progress)


async def rank_public_industry_domains(
    task: ResolvedTask,
    evidence: list[dict[str, Any]],
    llm_cfg: dict[str, Any],
    completion: Completion,
) -> dict[str, Any]:
    selection = result_selection(task)
    from src.agent.evidence_facts import collect_evidence_sources

    sources = collect_evidence_sources(evidence)[:10]
    if not sources:
        return {
            "success": False,
            "partial": False,
            "errors": ["产业研究来源为空，无法形成可复用的领域排序。"],
            "items": [],
            "semantic_artifacts": [],
            "resource_outputs": {"domain_collection": []},
        }
    source_batches = [sources[index : index + 2] for index in range(0, len(sources), 2)]

    async def rank_batch(batch: list[dict[str, Any]]) -> list[Any]:
        request = {
            "research_objective": task.candidate.objective,
            "subjects": task.parameters.get("subjects") or [],
            "result_selection": selection_payload(selection),
            "sources": batch,
        }
        kwargs = build_litellm_kwargs(
            llm_cfg,
            stream=False,
            messages=[
                {"role": "system", "content": _RANKED_DOMAIN_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
            ],
            tools=[_RANKED_DOMAIN_TOOL],
            tool_choice={"type": "function", "function": {"name": "submit_ranked_industry_domains"}},
            temperature=0,
            max_tokens=2_500,
        )
        response = await completion(**kwargs)
        payload = response_payload(response, "submit_ranked_industry_domains")
        domains = payload.get("domains")
        if not isinstance(domains, list):
            raise ValueError("ranked domain response is not a list")
        return domains

    batch_results = await asyncio.gather(
        *(asyncio.create_task(rank_batch(batch)) for batch in source_batches),
        return_exceptions=True,
    )
    raw_domains: list[Any] = []
    failures: list[BaseException] = []
    for result in batch_results:
        if isinstance(result, BaseException):
            failures.append(result)
        else:
            raw_domains.extend(result)
    source_map = {str(source["source_id"]): source for source in sources}
    best_by_label: dict[str, dict[str, Any]] = {}
    for raw in raw_domains:
        try:
            candidate = RankedDomainCandidate.model_validate(raw)
        except ValidationError:
            continue
        source = source_map.get(candidate.source_id)
        if source is None or candidate.confidence < 0.65:
            continue
        quote = candidate.support_quote.strip()
        if normalized_text(quote) not in normalized_text(str(source.get("text") or "")):
            continue
        identity = normalized_text(candidate.label)
        if not identity:
            continue
        item = {
            "label": candidate.label,
            "tier": candidate.tier,
            "rationale": candidate.rationale,
            "support_quote": quote,
            "source_id": candidate.source_id,
            "source_name": str(source.get("source_name") or "公开资料"),
            "source_url": str(source.get("source_url") or ""),
            "source_date": str(source.get("source_date") or ""),
            "confidence": candidate.confidence,
        }
        previous = best_by_label.get(identity)
        if previous is None or (int(item["tier"]), -float(item["confidence"])) < (
            int(previous["tier"]),
            -float(previous["confidence"]),
        ):
            best_by_label[identity] = item
    items = sorted(best_by_label.values(), key=lambda item: (int(item["tier"]), -float(item["confidence"]), item["label"]))
    items = apply_result_selection(items, selection)
    failed_batch_count = len(failures)
    if not items:
        return {
            "success": False,
            "partial": False,
            "errors": [
                "来源中没有通过原文校验的受益领域排序。"
                + (f" {failed_batch_count} 个语义批次未完成。" if failed_batch_count else "")
            ],
            "items": [],
            "semantic_artifacts": [],
            "resource_outputs": {"domain_collection": []},
        }
    groups = [
        {
            "tier": tier,
            "domains": [{"label": item["label"], "tier": item["tier"]} for item in items if item["tier"] == tier],
        }
        for tier in sorted({int(item["tier"]) for item in items})
    ]
    artifact = {
        "type": "ranked_domains",
        "topic": str(task.parameters.get("query") or task.candidate.objective),
        "root_topics": domain_labels(task),
        "result_selection": selection_payload(selection),
        "groups": groups,
    }
    return {
        "success": True,
        "partial": failed_batch_count > 0,
        "errors": [f"{failed_batch_count} 个语义批次未完成。"] if failed_batch_count else [],
        "items": items,
        "result_selection": selection_payload(selection),
        "semantic_artifacts": [artifact],
        "resource_outputs": {
            "domain_collection": [
                {"label": item["label"], "tier": item["tier"], "rationale": item["rationale"]} for item in items
            ]
        },
    }


async def rank_industry_domains(
    task: ResolvedTask,
    evidence: list[dict[str, Any]],
    llm_cfg: dict[str, Any],
    completion: Completion,
    progress: ProcessorProgress | None = None,
) -> dict[str, Any]:
    catalog = catalog_result(evidence)
    if catalog is not None:
        return await rank_project_board_domains(task, catalog, llm_cfg, completion, progress)
    return {
        "success": False,
        "partial": False,
        "error_code": "resource_unavailable",
        "errors": [
            "产业研究没有取得项目实时板块目录；程序未改用公开来源生成另一套不可复用的领域结果。"
        ],
        "warnings": [],
        "items": [],
        "semantic_artifacts": [],
        "resource_outputs": {},
        "source_scope": "project_live_board_catalog",
        "catalog_count": 0,
        "batch_total": 0,
        "batch_completed": 0,
        "coverage_complete": False,
        "ranking_complete": False,
        "coverage": {"catalog_total": 0, "catalog_supplied": 0, "selected_count": 0, "binding_complete": False},
        "failed_batches": [],
    }


__all__ = [
    "industry_catalog_mapping_mode",
    "rank_industry_domains",
    "rank_project_board_domains",
    "rank_project_board_domains_v1",
    "rank_public_industry_domains",
]
