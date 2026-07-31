# -*- coding: utf-8 -*-
"""Stable facade for typed semantic result processors.

Implementation is split by responsibility so callers keep the historical import
path while each module remains small enough to review independently.
"""

from __future__ import annotations

from typing import Any

from src.agent.domain_result_processor import (
    industry_catalog_mapping_mode as _industry_catalog_mapping_mode,
    rank_industry_domains as _rank_industry_domains,
    rank_project_board_domains as _rank_project_board_domains,
    rank_project_board_domains_v1 as _rank_project_board_domains_v1,
    rank_public_industry_domains as _rank_public_industry_domains,
)
from src.agent.result_processor_models import (
    CompanyThemeAnalysisCandidate,
    Completion,
    ProcessorProgress,
    RankedDomainCandidate,
    RankedProjectBoard,
    RankedProjectBoardSubmission,
    _COMPANY_THEME_ANALYSIS_SYSTEM_PROMPT,
    _PROJECT_BOARD_RANKING_SYSTEM_PROMPT,
    _PROJECT_BOARD_RANKING_TOOL,
    _RANKED_DOMAIN_SYSTEM_PROMPT,
    _RANKED_DOMAIN_TOOL,
    apply_result_selection as _apply_result_selection,
    catalog_result as _catalog_result,
    domain_labels as _domain_labels,
    load_project_board_ranking_cache as _load_project_board_ranking_cache,
    normalized_text as _normalized_text,
    project_board_cache_key as _project_board_cache_key,
    project_board_snapshot_id as _project_board_snapshot_id,
    response_payload as _response_payload,
    result_selection as _result_selection,
    save_project_board_ranking_cache as _save_project_board_ranking_cache,
    selection_payload as _selection_payload,
    semantic_failure_code as _semantic_failure_code,
)
from src.agent.task_workflows import ResolvedTask
from src.agent.theme_binding_processor import bind_theme_companies as _bind_theme_companies
from src.agent.theme_evidence_processor import (
    analyze_candidate_companies as _analyze_candidate_companies,
    deterministic_evidence_quote as _deterministic_evidence_quote,
    validate_company_theme_contract as _validate_company_theme_contract,
    validated_company_theme_result as _validated_company_theme_result,
)


_PROCESSORS = {
    "ranked_domain_selection": _rank_industry_domains,
    "company_evidence_binding": _bind_theme_companies,
}


async def process_task_result(
    processor_name: str,
    task: ResolvedTask,
    evidence: list[dict[str, Any]],
    llm_cfg: dict[str, Any],
    *,
    completion: Completion,
    progress: ProcessorProgress | None = None,
) -> dict[str, Any]:
    processor = _PROCESSORS.get(processor_name)
    if processor is None:
        raise ValueError(f"unknown result processor: {processor_name}")
    result = await processor(task, evidence, llm_cfg, completion, progress)
    if not isinstance(result, dict):
        raise ValueError(f"{processor_name} returned a non-object result")
    return result


__all__ = ["process_task_result"]
