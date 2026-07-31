# -*- coding: utf-8 -*-
"""Per-company theme evidence validation and semantic analysis."""

from __future__ import annotations

import asyncio
import re
from typing import Any, Mapping

from pydantic import BaseModel, ValidationError

from src.agent.orchestrator_v2.contracts import AgentErrorCode, RepairIssueV2
from src.agent.orchestrator_v2.planner import ExactContractValidationError, call_model_exact_v2
from src.agent.result_contracts import ThemeEvidenceContext
from src.agent.result_processor_models import (
    _COMPANY_THEME_ANALYSIS_SYSTEM_PROMPT,
    CompanyThemeAnalysisCandidate,
    Completion,
    ProcessorProgress,
    domain_labels,
    normalized_text,
)
from src.agent.task_workflows import ResolvedTask


def deterministic_evidence_quote(document: Mapping[str, Any], *, labels: list[str]) -> str:
    text = str(document.get("text") or "").strip()
    if not text:
        return ""
    chunks = [chunk.strip() for chunk in re.split(r"(?<=[。！？!?；;])|\n+", text) if chunk.strip()]
    if not chunks:
        return text[:800]
    development_terms = (
        "减速器",
        "量产",
        "批量",
        "供货",
        "交付",
        "订单",
        "客户",
        "收入",
        "营收",
        "产能",
        "投资",
        "布局",
        "研发",
        "产品",
    )

    def score(chunk: str) -> tuple[int, int]:
        normalized = normalized_text(chunk)
        label_score = sum(5 for label in labels if normalized_text(label) in normalized)
        term_score = sum(1 for term in development_terms if normalized_text(term) in normalized)
        return label_score + term_score, -len(chunk)

    return max(chunks, key=score)[:800]


def validated_company_theme_result(
    raw: Any,
    *,
    symbol: str,
    name: str,
    labels: list[str],
    documents: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        candidate = CompanyThemeAnalysisCandidate.model_validate(raw)
    except ValidationError as exc:
        return {
            "symbol": symbol,
            "company_name": name,
            "verdict": "error",
            "theme_fit": "none",
            "development_level": "none",
            "matched_domains": [],
            "reason": f"模型结果未通过结构校验：{exc}",
            "evidence": [],
            "confidence": 0.0,
        }
    if candidate.symbol != symbol:
        return {
            "symbol": symbol,
            "company_name": name,
            "verdict": "error",
            "theme_fit": "none",
            "development_level": "none",
            "matched_domains": [],
            "reason": "模型返回的证券代码与当前单股任务不一致。",
            "evidence": [],
            "confidence": 0.0,
        }

    source_map = {
        str(document.get("source_id") or ""): document for document in documents if isinstance(document, Mapping)
    }
    valid_evidence: list[dict[str, Any]] = []
    for reference in candidate.evidence:
        source = source_map.get(reference.source_id)
        if source is None or reference.domain not in labels:
            continue
        quote = deterministic_evidence_quote(source, labels=labels)
        if not quote:
            continue
        valid_evidence.append(
            {
                "source_id": reference.source_id,
                "domain": reference.domain,
                "support_quote": quote,
                "source_type": str(source.get("source_type") or ""),
                "source_name": str(source.get("source_name") or "项目数据源"),
                "source_url": str(source.get("source_url") or ""),
                "source_date": str(source.get("source_date") or ""),
                "official": bool(source.get("official")),
            }
        )

    matched_domains = [label for label in candidate.matched_domains if label in labels]
    verdict = candidate.verdict
    reason = candidate.reason
    if verdict == "pass" and (
        candidate.theme_fit != "exact"
        or candidate.development_level == "none"
        or not valid_evidence
        or not matched_domains
    ):
        verdict = "insufficient"
        reason = "模型给出通过结论，但没有同时满足精确主题、发展阶段、子领域和逐字证据校验，程序已降级为证据不足。"
    return {
        "symbol": symbol,
        "company_name": name,
        "verdict": verdict,
        "theme_fit": candidate.theme_fit,
        "development_level": candidate.development_level,
        "matched_domains": list(dict.fromkeys(matched_domains)),
        "reason": reason,
        "evidence": valid_evidence,
        "confidence": candidate.confidence,
    }


def validate_company_theme_contract(
    value: BaseModel,
    *,
    symbol: str,
    name: str,
    labels: list[str],
    documents: list[dict[str, Any]],
) -> None:
    candidate = CompanyThemeAnalysisCandidate.model_validate(value)
    issues: list[RepairIssueV2] = []
    if candidate.symbol != symbol:
        issues.append(
            RepairIssueV2(
                pointer="/symbol",
                code="company_symbol_mismatch",
                expected=f"const={symbol}",
                allowed=(symbol,),
                message="symbol must match requested_company.symbol",
            )
        )
    if candidate.company_name != name:
        issues.append(
            RepairIssueV2(
                pointer="/company_name",
                code="company_name_mismatch",
                expected=f"const={name}",
                allowed=(name,),
                message="company_name must match requested_company.name",
            )
        )

    for index, domain in enumerate(candidate.matched_domains):
        if domain not in labels:
            issues.append(
                RepairIssueV2(
                    pointer=f"/matched_domains/{index}",
                    code="unknown_domain",
                    expected="one supplied domain",
                    allowed=tuple(labels),
                    message="matched domain was not supplied by the program",
                )
            )

    source_map = {
        str(document.get("source_id") or ""): document for document in documents if isinstance(document, Mapping)
    }
    valid_evidence_count = 0
    for index, reference in enumerate(candidate.evidence):
        source = source_map.get(reference.source_id)
        if source is None:
            issues.append(
                RepairIssueV2(
                    pointer=f"/evidence/{index}/source_id",
                    code="unknown_source",
                    expected="source_id from evidence_documents",
                    allowed=tuple(source_map),
                    message="evidence source was not supplied by the program",
                )
            )
            continue
        if reference.domain not in labels:
            issues.append(
                RepairIssueV2(
                    pointer=f"/evidence/{index}/domain",
                    code="unknown_domain",
                    expected="one supplied domain",
                    allowed=tuple(labels),
                    message="evidence domain was not supplied by the program",
                )
            )
            continue
        if not str(source.get("text") or "").strip():
            issues.append(
                RepairIssueV2(
                    pointer=f"/evidence/{index}/source_id",
                    code="empty_source",
                    expected="a source containing non-empty evidence text",
                    message="selected evidence source contains no usable text",
                )
            )
            continue
        valid_evidence_count += 1

    if candidate.verdict == "pass":
        if candidate.theme_fit != "exact":
            issues.append(
                RepairIssueV2(
                    pointer="/theme_fit",
                    code="pass_requires_exact_theme",
                    expected="const=exact",
                    allowed=("exact",),
                    message="pass requires exact theme fit",
                )
            )
        if candidate.development_level == "none":
            issues.append(
                RepairIssueV2(
                    pointer="/development_level",
                    code="pass_requires_development",
                    expected="a proven development stage other than none",
                    allowed=("layout", "investment", "customer_validation", "order", "mass_production", "revenue"),
                    message="pass requires a proven development stage",
                )
            )
        if not candidate.matched_domains:
            issues.append(
                RepairIssueV2(
                    pointer="/matched_domains",
                    code="pass_requires_domain",
                    expected="at least one supplied domain",
                    allowed=tuple(labels),
                    message="pass requires a matched supplied domain",
                )
            )
        if valid_evidence_count == 0:
            issues.append(
                RepairIssueV2(
                    pointer="/evidence",
                    code="pass_requires_bound_evidence",
                    expected="at least one valid program-bound evidence source",
                    message="pass requires program-verifiable source binding",
                )
            )

    if issues:
        raise ExactContractValidationError(tuple(issues))


async def analyze_candidate_companies(
    task: ResolvedTask,
    evidence: list[dict[str, Any]],
    evidence_context: ThemeEvidenceContext,
    labels: list[str],
    llm_cfg: dict[str, Any],
    completion: Completion,
    progress: ProcessorProgress | None = None,
) -> dict[str, Any]:
    names = dict(task.entity_names)
    packets_by_symbol: dict[str, dict[str, Any]] = {}
    duplicate_packets: set[str] = set()
    for packet in evidence:
        if (
            not isinstance(packet, Mapping)
            or packet.get("tool") != "get_company_theme_evidence"
            or not isinstance(packet.get("arguments"), Mapping)
            or not isinstance(packet.get("result"), Mapping)
        ):
            continue
        symbol = str(packet["arguments"].get("symbol") or "").strip()
        if symbol in packets_by_symbol:
            duplicate_packets.add(symbol)
            continue
        packets_by_symbol[symbol] = dict(packet)

    domain_theses = [
        {
            "label": label,
            "rationale": evidence_context.thesis_for(label).rationale if evidence_context.thesis_for(label) else "",
            "tier": evidence_context.thesis_for(label).tier if evidence_context.thesis_for(label) else None,
        }
        for label in labels
    ]
    semaphore = asyncio.Semaphore(8)

    async def analyze(symbol: str) -> dict[str, Any]:
        name = names.get(symbol, symbol)
        packet = packets_by_symbol.get(symbol)
        if packet is None:
            return {
                "symbol": symbol,
                "company_name": name,
                "verdict": "error",
                "theme_fit": "none",
                "development_level": "none",
                "matched_domains": [],
                "reason": "Executor 没有为该候选公司生成单股证据任务。",
                "evidence": [],
                "confidence": 0.0,
                "source_status": {},
                "fallback_used": False,
            }
        result = dict(packet["result"])
        documents = [dict(document) for document in result.get("evidence_documents") or [] if isinstance(document, Mapping)]
        base = {
            "source_status": result.get("source_status") or {},
            "project_source_coverage_complete": bool(result.get("project_source_coverage_complete")),
            "fallback_attempted": bool(result.get("fallback_attempted")),
            "fallback_used": bool(result.get("fallback_used")),
            "tool_errors": [str(error) for error in result.get("errors") or [] if error],
        }
        if not documents:
            return {
                "symbol": symbol,
                "company_name": name,
                "verdict": "insufficient",
                "theme_fit": "none",
                "development_level": "none",
                "matched_domains": [],
                "reason": "该公司的项目数据源及逐股网络兜底均未形成可分析文本。",
                "evidence": [],
                "confidence": 0.0,
                **base,
            }

        request = {
            "requested_company": {"symbol": symbol, "name": name},
            "target_topics": evidence_context.target_topics,
            "supplied_domains": labels,
            "domain_theses": domain_theses,
            "selection_objective": task.candidate.objective,
            "evidence_documents": documents,
            "source_coverage": {
                "project_source_count": result.get("project_source_count"),
                "project_source_success_count": result.get("project_source_success_count"),
                "project_source_coverage_complete": result.get("project_source_coverage_complete"),
                "fallback_used": result.get("fallback_used"),
            },
        }
        try:
            async with semaphore:
                candidate, _, repair = await call_model_exact_v2(
                    llm_cfg=llm_cfg,
                    completion=completion,
                    function_name="submit_company_theme_analysis",
                    description="Submit the independent theme-development verdict for exactly one company.",
                    model=CompanyThemeAnalysisCandidate,
                    system_prompt=_COMPANY_THEME_ANALYSIS_SYSTEM_PROMPT,
                    semantic_context=request,
                    node_id=f"company_theme_analysis:{symbol}",
                    value_validator=lambda value: validate_company_theme_contract(
                        value, symbol=symbol, name=name, labels=labels, documents=documents
                    ),
                    provider_error_code=AgentErrorCode.SYNTHESIS_FAILED,
                    schema_error_code=AgentErrorCode.SYNTHESIS_FAILED,
                    max_tokens=4_000,
                )
            validated = validated_company_theme_result(
                candidate.model_dump(mode="json"), symbol=symbol, name=name, labels=labels, documents=documents
            )
            if repair is not None:
                validated["model_repair"] = repair.model_dump(mode="json")
            return {**validated, **base}
        except Exception as exc:
            return {
                "symbol": symbol,
                "company_name": name,
                "verdict": "error",
                "theme_fit": "none",
                "development_level": "none",
                "matched_domains": [],
                "reason": f"单股模型判断失败：{type(exc).__name__}: {exc}",
                "evidence": [],
                "confidence": 0.0,
                **base,
            }

    completed_count = 0
    progress_lock = asyncio.Lock()

    async def analyze_with_progress(symbol: str) -> dict[str, Any]:
        nonlocal completed_count
        result = await analyze(symbol)
        if progress is not None:
            async with progress_lock:
                completed_count += 1
                await progress(completed_count, len(task.symbols), result)
        return result

    company_results = await asyncio.gather(*(analyze_with_progress(symbol) for symbol in task.symbols))
    counts = {status: sum(1 for item in company_results if item.get("verdict") == status) for status in ("pass", "fail", "insufficient", "error")}
    passed = [item for item in company_results if item.get("verdict") == "pass"]
    missing_symbols = [symbol for symbol in task.symbols if symbol not in packets_by_symbol]
    coverage_complete = len(company_results) == len(task.symbols) and not missing_symbols and not duplicate_packets
    domain_results = [
        {
            "domain": label,
            "analyzed_company_count": len(task.symbols),
            "passed_company_count": sum(1 for item in passed if label in item.get("matched_domains", [])),
        }
        for label in labels
    ]
    security_collection = [{"symbol": str(item["symbol"]), "name": str(item["company_name"])} for item in passed]
    warnings: list[str] = []
    if counts["insufficient"]:
        warnings.append(f"{counts['insufficient']} 家完成了独立分析但证据不足，未纳入通过名单。")
    if counts["error"]:
        warnings.append(f"{counts['error']} 家单股分析发生错误，未静默计入排除结果。")
    if duplicate_packets:
        warnings.append("检测到重复单股证据任务：" + "、".join(sorted(duplicate_packets)))
    artifact = {
        "type": "per_security_theme_analysis",
        "target_topics": evidence_context.target_topics,
        "domains": labels,
        "candidate_count": len(task.symbols),
        "analyzed_candidate_count": len(company_results),
        "candidate_coverage_complete": coverage_complete,
        "verdict_counts": counts,
        "companies": company_results,
    }
    return {
        "success": coverage_complete,
        "partial": not coverage_complete or counts["insufficient"] > 0 or counts["error"] > 0,
        "errors": [f"缺少 {len(missing_symbols)} 家候选公司的单股证据任务：" + "、".join(missing_symbols[:20])]
        if missing_symbols
        else [],
        "warnings": warnings,
        "candidate_scope": "candidate_collection",
        "screening_mode": "per_security_full_analysis",
        "candidate_count": len(task.symbols),
        "analyzed_candidate_count": len(company_results),
        "candidate_coverage_complete": coverage_complete,
        "verdict_counts": counts,
        "positive_company_count": len(passed),
        "items": passed,
        "company_results": company_results,
        "domain_results": domain_results,
        "semantic_artifacts": [artifact],
        "resource_outputs": {"security_collection": security_collection},
    }


__all__ = [
    "analyze_candidate_companies",
    "deterministic_evidence_quote",
    "validate_company_theme_contract",
    "validated_company_theme_result",
]
