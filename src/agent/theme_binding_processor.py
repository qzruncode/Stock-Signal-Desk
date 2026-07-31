# -*- coding: utf-8 -*-
"""Bind collected theme evidence to the selected company scope."""

from __future__ import annotations

import asyncio
from typing import Any, Mapping

from pydantic import ValidationError

from src.agent.evidence_facts import bind_company_evidence, collect_evidence_sources
from src.agent.result_contracts import MappingSelectionContext, ThemeEvidenceContext
from src.agent.result_processor_models import Completion, ProcessorProgress, domain_labels
from src.agent.task_workflows import ResolvedTask
from src.agent.theme_evidence_processor import analyze_candidate_companies
from src.tools.symbols import find_securities_in_text


async def bind_theme_companies(
    task: ResolvedTask,
    evidence: list[dict[str, Any]],
    llm_cfg: dict[str, Any],
    completion: Completion,
    progress: ProcessorProgress | None = None,
) -> dict[str, Any]:
    labels = domain_labels(task)
    candidate_scope = str(task.parameters.get("candidate_scope") or "").strip()
    allowed_symbols = {str(symbol).strip() for symbol in task.symbols if str(symbol).strip()}
    try:
        evidence_context = ThemeEvidenceContext.model_validate(task.parameters.get("evidence_context"))
    except ValidationError as exc:
        return _failure(f"公司举证任务缺少有效的上位产业命题：{exc}")

    source_warnings = [
        str(error)
        for packet in evidence
        if isinstance(packet.get("result"), Mapping) and packet["result"].get("success") is False
        for error in packet["result"].get("errors") or ["部分公开来源查询失败"]
        if error
    ]
    if not labels:
        return _failure("没有取得可执行的语义领域。")
    if candidate_scope not in {"candidate_collection", "public_fallback"}:
        return _failure("公司举证任务缺少有效的候选范围契约。")
    if candidate_scope == "candidate_collection" and not allowed_symbols:
        return _failure("上游结构化候选公司集合为空，已阻止公开来源自行补股票。")
    if candidate_scope == "candidate_collection":
        return await analyze_candidate_companies(
            task, evidence, evidence_context, labels, llm_cfg, completion, progress
        )

    model_call_semaphore = asyncio.Semaphore(12)

    async def limited_completion(**kwargs: Any) -> Any:
        async with model_call_semaphore:
            return await completion(**kwargs)

    packets_by_domain: dict[str, list[dict[str, Any]]] = {}
    observed_symbols_by_domain: dict[str, set[str]] = {}
    source_count_by_domain: dict[str, int] = {}
    for label in labels:
        packets = [
            packet
            for packet in evidence
            if label
            in [
                str(value or "").strip()
                for value in (
                    packet.get("arguments", {}).get("subjects") or []
                    if isinstance(packet.get("arguments"), Mapping)
                    else []
                )
            ]
        ]
        packets_by_domain[label] = packets
        sources = collect_evidence_sources(packets)
        source_count_by_domain[label] = len(sources)
        observed: set[str] = set()
        for source in sources:
            source_text = "\n".join((str(source.get("title") or ""), str(source.get("text") or "")))
            observed.update(
                str(item.get("symbol") or "")
                for item in find_securities_in_text(source_text, limit=100)
                if str(item.get("symbol") or "") in allowed_symbols
            )
        observed_symbols_by_domain[label] = observed

    target_topic = "、".join(evidence_context.target_topics)

    async def bind_domain(label: str) -> tuple[str, list[Any]]:
        packets = packets_by_domain[label]
        domain_thesis = evidence_context.thesis_for(label)
        rationale = domain_thesis.rationale if domain_thesis is not None else ""
        thesis_requirements = [
            (
                f"原文事实必须支持公司正在参与上位产业“{target_topic}”中的“{label}”方向；"
                "只证明同类产品用于其他终端或无关应用场景，不能视为满足当前命题"
            ),
            f"公司主体必须是“{label}”相关产品或业务的研发、生产、销售或应用方",
        ]
        if rationale:
            thesis_requirements.append(f"公司事实必须符合该板块的受益逻辑：{rationale}")
        intent = MappingSelectionContext(
            topic=f"{target_topic}中的{label}",
            objective=(
                task.candidate.objective
                + (
                    f"；只核验程序已绑定的 {len(allowed_symbols)} 家候选公司，不得从公开来源扩大候选范围"
                    if candidate_scope == "candidate_collection"
                    else "；项目结构化板块无法覆盖，本轮允许从公开来源发现公司"
                )
            ),
            selection_mode="ranked_shortlist",
            thesis_requirements=thesis_requirements,
            research_dimensions=[
                "产品布局",
                "商业应用",
                "送样定点与客户验证",
                "订单",
                "量产与批量交付",
                "相关业务收入",
                "否认与尚未形成收入边界",
            ],
        )
        facts = await bind_company_evidence(packets, intent, llm_cfg, completion=limited_completion)
        return label, facts

    semaphore = asyncio.Semaphore(4)

    async def bounded(label: str) -> tuple[str, list[Any]]:
        async with semaphore:
            return await bind_domain(label)

    domain_bindings = await asyncio.gather(*(bounded(label) for label in labels), return_exceptions=True)
    domain_results: list[dict[str, Any]] = []
    positive_items: list[dict[str, Any]] = []
    boundary_items: list[dict[str, Any]] = []
    errors: list[str] = []
    rejected_outside_candidate_count = 0
    source_observed_symbols: set[str] = set()
    for label, binding in zip(labels, domain_bindings):
        domain_observed = observed_symbols_by_domain.get(label, set())
        source_observed_symbols.update(domain_observed)
        if isinstance(binding, BaseException):
            errors.append(f"{label}：{binding}")
            domain_results.append(
                {
                    "domain": label,
                    "success": False,
                    "company_count": 0,
                    "source_item_count": source_count_by_domain.get(label, 0),
                    "source_observed_candidate_count": len(domain_observed),
                    "errors": [str(binding)],
                }
            )
            continue
        _resolved_label, facts = binding
        accepted = 0
        rejected = 0
        for fact in facts:
            item = {**fact.model_dump(), "domain": label, "matched_domains": [label]}
            if candidate_scope == "candidate_collection" and fact.symbol not in allowed_symbols:
                rejected += 1
                rejected_outside_candidate_count += 1
                continue
            if fact.stage == "boundary" or fact.thesis_fit != "exact":
                boundary_items.append(item)
                continue
            positive_items.append(item)
            accepted += 1
        domain_results.append(
            {
                "domain": label,
                "success": True,
                "company_count": accepted,
                "source_item_count": source_count_by_domain.get(label, 0),
                "source_observed_candidate_count": len(domain_observed),
                "rejected_outside_candidate_count": rejected,
                "errors": [],
            }
        )

    stage_rank = {"L3": 3, "L2": 2, "L1": 1}
    companies: dict[str, dict[str, Any]] = {}
    for item in positive_items:
        symbol = str(item.get("symbol") or "")
        company = companies.setdefault(
            symbol,
            {
                "symbol": symbol,
                "name": str(item.get("company_name") or symbol),
                "matched_domains": [],
                "strongest_stage": "L1",
            },
        )
        for domain in item.get("matched_domains") or []:
            if domain not in company["matched_domains"]:
                company["matched_domains"].append(domain)
        if stage_rank.get(str(item.get("stage")), 0) > stage_rank.get(str(company["strongest_stage"]), 0):
            company["strongest_stage"] = item["stage"]
    security_collection = sorted(
        companies.values(),
        key=lambda item: (-stage_rank.get(str(item["strongest_stage"]), 0), -len(item["matched_domains"]), item["symbol"]),
    )
    artifact = {
        "type": "theme_company_evidence",
        "domains": labels,
        "target_topics": evidence_context.target_topics,
        "companies": security_collection,
        "candidate_scope": candidate_scope,
    }
    candidate_count = len(allowed_symbols)
    source_observed_candidate_count = len(source_observed_symbols)
    candidate_coverage_complete = candidate_scope != "candidate_collection" or source_observed_candidate_count == candidate_count
    coverage_warning = (
        f"公开来源只逐字提及候选池中的 {source_observed_candidate_count}/{candidate_count} 家；当前结果是证据命中 shortlist，"
        "不是完整候选筛选后的唯一剩余公司。"
        if candidate_scope == "candidate_collection" and not candidate_coverage_complete
        else ""
    )
    return {
        "success": bool(positive_items) or not errors,
        "partial": bool(errors or source_warnings or not candidate_coverage_complete),
        "errors": errors,
        "warnings": list(dict.fromkeys([*source_warnings, *([coverage_warning] if coverage_warning else [])])),
        "candidate_scope": candidate_scope,
        "screening_mode": "source_hit_shortlist",
        "candidate_count": candidate_count,
        "source_observed_candidate_count": source_observed_candidate_count,
        "not_observed_candidate_count": max(0, candidate_count - source_observed_candidate_count),
        "candidate_coverage_complete": candidate_coverage_complete,
        "positive_company_count": len(security_collection),
        "rejected_outside_candidate_count": rejected_outside_candidate_count,
        "items": positive_items,
        "boundary_items": boundary_items,
        "domain_results": domain_results,
        "semantic_artifacts": [artifact],
        "resource_outputs": {"security_collection": [{"symbol": item["symbol"], "name": item["name"]} for item in security_collection]},
    }


def _failure(message: str) -> dict[str, Any]:
    return {
        "success": False,
        "partial": False,
        "errors": [message],
        "items": [],
        "domain_results": [],
        "semantic_artifacts": [],
        "resource_outputs": {"security_collection": []},
    }


__all__ = ["bind_theme_companies"]
