# -*- coding: utf-8 -*-
"""Task status, deterministic result contracts, and answer guards."""

from __future__ import annotations

import json
import math
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from src.agent.collection_financial_renderers import build_collection_financial_filter_answer as _build_collection_financial_filter_answer
from src.agent.domain_renderers import (
    build_domain_candidate_answer as _build_domain_candidate_answer,
    build_ranked_domain_answer as _build_ranked_domain_answer,
    build_theme_business_evidence_answer as _build_theme_business_evidence_answer,
)
from api.v1.endpoints.agent.chat_decision_renderers import _build_professional_buy_decision_answer
from api.v1.endpoints.agent.chat_evidence_renderers import _build_quantitative_screen_answer
from api.v1.endpoints.agent.chat_research_renderers import _build_workflow_evidence_fallback
from src.agent.result_contracts import CollectionFinancialFilterSpec
from src.agent.rss_renderers import (
    build_rss_resource_answer as _build_rss_resource_answer,
)
from src.agent.task_executor import PlanExecutionResult
from src.agent.task_workflows import StandardTaskKind, TaskPlan, workflow_for
from src.tools.symbols import find_securities_in_text

def _task_status_evidence(
    plan: TaskPlan,
    execution: PlanExecutionResult,
) -> Dict[str, Any]:
    return {
        "tool": "runtime_standard_task_status",
        "arguments": {},
        "result": {
            "success": execution.success,
            "tasks": [
                {
                    "task_id": result.task.task_id,
                    "kind": result.task.kind.value,
                    "objective": result.task.candidate.objective,
                    "status": result.status,
                    "blocked_reason": result.blocked_reason,
                    "errors": result.errors,
                    "executed_calls": sum(1 for call in result.calls if call.executed),
                    "reused_calls": sum(1 for call in result.calls if call.reused),
                    "derived_result_count": len(result.derived_results),
                    "output_entities": [
                        {"symbol": entity.symbol, "name": entity.name} for entity in result.output_entities
                    ],
                }
                for result in execution.tasks
            ],
            "dependencies": {task.task_id: task.depends_on for task in plan.tasks},
            "final_entities": [{"symbol": entity.symbol, "name": entity.name} for entity in execution.final_entities],
        },
    }


def _blocked_task_answer(execution: PlanExecutionResult) -> Optional[str]:
    dependency_ids = {
        dependency_id
        for task in execution.tasks
        for dependency_id in task.task.candidate.depends_on
    }
    terminal_tasks = [
        task
        for task in execution.tasks
        if task.task.task_id not in dependency_ids
    ]
    blocked = [task for task in terminal_tasks if task.status == "blocked"]
    completed_terminal = any(
        task.status == "completed" and task.calls
        for task in terminal_tasks
    )
    if not blocked or completed_terminal:
        return None
    lines = ["## 本轮执行已由流程校验层停止", ""]
    for result in blocked:
        spec = workflow_for(result.task.kind)
        lines.append(f"- **{spec.title}**：{'；'.join(result.errors) or '不满足执行条件'}")
    lines.extend(
        [
            "",
            "没有调用任何越权工具，也没有执行账户、删除、通知或其他高影响操作。",
        ]
    )
    return "\n".join(lines)


def _numeric_evidence_values(value: Any) -> list[float]:
    numbers: list[float] = []
    if isinstance(value, bool) or value is None:
        return numbers
    if isinstance(value, (int, float)):
        number = float(value)
        if math.isfinite(number):
            numbers.append(number)
        return numbers
    if isinstance(value, dict):
        for nested in value.values():
            numbers.extend(_numeric_evidence_values(nested))
        return numbers
    if isinstance(value, (list, tuple)):
        for nested in value:
            numbers.extend(_numeric_evidence_values(nested))
    return numbers


def _display_number_matches_evidence(
    number_text: str,
    unit: str,
    evidence_numbers: list[float],
) -> bool:
    try:
        displayed = Decimal(number_text.replace(",", ""))
    except InvalidOperation:
        return False
    decimal_places = max(0, -displayed.as_tuple().exponent)
    quantum = Decimal(1).scaleb(-decimal_places)

    for raw_number in evidence_numbers:
        raw = Decimal(str(raw_number))
        candidates = [raw]
        if unit == "亿元":
            candidates.append(raw / Decimal("100000000"))
        elif unit in {"万元", "万台"}:
            candidates.append(raw / Decimal("10000"))
        elif unit == "%" and abs(raw) <= 1:
            candidates.append(raw * Decimal("100"))
        if any(
            candidate.quantize(quantum, rounding=ROUND_HALF_UP)
            == displayed
            for candidate in candidates
        ):
            return True
    return False


def _normalized_material_claim_value(
    number_text: str,
    unit: str,
) -> tuple[str, Decimal] | None:
    """Normalize textual evidence claims without losing their dimension."""
    try:
        value = Decimal(number_text.replace(",", ""))
    except InvalidOperation:
        return None
    if unit == "亿元":
        return "currency", value * Decimal("100000000")
    if unit == "万元":
        return "currency", value * Decimal("10000")
    if unit == "元":
        return "currency", value
    if unit == "万台":
        return "count:台", value * Decimal("10000")
    if unit == "台":
        return "count:台", value
    if unit == "%":
        return "ratio", value
    return f"count:{unit}", value


def _exact_result_contract_answer(
    plan: TaskPlan,
    execution: PlanExecutionResult,
) -> Optional[str]:
    """Render only workflows whose exhaustive or safety result is machine-owned."""
    dependency_ids = {dependency_id for task in plan.tasks for dependency_id in task.depends_on}
    terminal_tasks = [task for task in plan.tasks if task.task_id not in dependency_ids]
    if len(terminal_tasks) != 1:
        return None
    task = terminal_tasks[0]
    result = next(
        (item for item in execution.tasks if item.task.task_id == task.task_id),
        None,
    )
    if result is None:
        return None
    lineage_ids: set[str] = set()
    tasks_by_id = {item.task_id: item for item in plan.tasks}

    def include_lineage(task_id: str) -> None:
        if task_id in lineage_ids:
            return
        lineage_ids.add(task_id)
        for dependency_id in tasks_by_id[task_id].depends_on:
            include_lineage(dependency_id)

    include_lineage(task.task_id)
    evidence = [packet for item in execution.tasks if item.task.task_id in lineage_ids for packet in item.evidence]
    result_contract = workflow_for(task.kind).result_contract
    if result_contract == "industry_ranked_domains":
        return _build_ranked_domain_answer(evidence) or (
            "## 产业受益领域排序未完成\n\n"
            "结构化领域处理没有成功，因此本轮不再由自由写作模型另行生成一套梯队。"
            + ("\n\n执行信息：" + "；".join(result.errors) if result.errors else "")
        )
    if result_contract == "theme_stock_discovery":
        return _build_domain_candidate_answer(evidence)
    if result_contract == "theme_business_evidence":
        return _build_theme_business_evidence_answer(evidence) or (
            "## 领域公司核验未完成\n\n"
            "公司证据绑定没有成功，因此本轮没有用概念板块、网页名单或模型记忆补股票。"
            + ("\n\n执行信息：" + "；".join(result.errors) if result.errors else "")
        )
    if result_contract == "collection_financial_filter":
        try:
            spec = CollectionFinancialFilterSpec.model_validate(task.parameters)
        except Exception:
            return None
        return _build_collection_financial_filter_answer(evidence, spec)
    if result_contract == "stock_screening":
        return _build_quantitative_screen_answer(evidence)
    if result_contract == "investment_decision":
        return _build_professional_buy_decision_answer(evidence) or (
            "## 专业买入分析未完成\n\n" "本轮没有成功取得八维专业分析结果，因此没有输出任何买入结论。请重试本轮问题。"
        )
    if result_contract in {
        "rss_source_discovery",
        "rss_feed_read",
        "rss_article_read",
    }:
        if (
            result_contract == "rss_article_read"
            and str(task.parameters.get("response_mode") or "")
            != "resource_delivery"
        ):
            return None
        return _build_rss_resource_answer(evidence)
    if task.kind in {
        StandardTaskKind.WATCHLIST_QUERY,
        StandardTaskKind.WATCHLIST_MUTATION,
        StandardTaskKind.WATCHLIST_GROUP_MANAGEMENT,
        StandardTaskKind.FORMAL_ANALYSIS,
        StandardTaskKind.ANALYSIS_HISTORY,
        StandardTaskKind.ANALYSIS_TEMPLATE_MANAGEMENT,
        StandardTaskKind.BATCH_ANALYSIS,
        StandardTaskKind.BATCH_RUN_MANAGEMENT,
        StandardTaskKind.ANALYSIS_SCHEDULE_MANAGEMENT,
        StandardTaskKind.NOTIFICATION,
    }:
        return _build_workflow_evidence_fallback(evidence)
    return None


def _standard_task_answer_issues(
    content: str,
    evidence: Optional[List[Dict[str, Any]]],
) -> List[str]:
    """Reject securities and material numeric claims absent from task evidence."""
    evidence_text = json.dumps(evidence or [], ensure_ascii=False, default=str)
    issues: List[str] = []

    evidence_codes = set(re.findall(r"(?<!\d)(\d{6})(?!\d)", evidence_text))
    answer_codes = set(re.findall(r"(?<!\d)(\d{6})(?!\d)", content))
    unsupported_codes = sorted(answer_codes - evidence_codes)
    if unsupported_codes:
        issues.append("最终答案出现本轮证据未提供的证券代码：" + "、".join(unsupported_codes[:12]))
    evidence_symbols = {
        item["symbol"] for item in find_securities_in_text(evidence_text, limit=300) if item.get("symbol")
    }
    answer_entities = find_securities_in_text(content, limit=300)
    unsupported_entities = [item for item in answer_entities if item.get("symbol") not in evidence_symbols]
    if unsupported_entities:
        issues.append(
            "最终答案出现本轮证据未提供的证券实体："
            + "、".join(f"{item.get('name')}({item.get('symbol')})" for item in unsupported_entities[:12])
        )

    display_number_pattern = (
        r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
    )
    material_claim_pattern = re.compile(
        rf"(?<![\d.,])(?P<first>{display_number_pattern})"
        rf"(?:\s*[-—~至]\s*(?P<second>{display_number_pattern}))?\s*"
        r"(?P<unit>%|亿元|万元|元|万台|台|个|倍|家)"
    )
    evidence_numbers = _numeric_evidence_values(evidence or [])
    evidence_text_claims = {
        normalized
        for evidence_match in material_claim_pattern.finditer(evidence_text)
        for number in (
            evidence_match.group("first"),
            evidence_match.group("second"),
        )
        if number is not None
        if (
            normalized := _normalized_material_claim_value(
                number,
                evidence_match.group("unit"),
            )
        )
        is not None
    }
    missing_claims: List[str] = []
    for match in material_claim_pattern.finditer(content):
        claim = match.group(0)
        numbers = [
            number
            for number in (
                match.group("first"),
                match.group("second"),
            )
            if number is not None
        ]
        unit = match.group("unit")
        if all(
            _display_number_matches_evidence(number, unit, evidence_numbers)
            or _normalized_material_claim_value(number, unit)
            in evidence_text_claims
            for number in numbers
        ):
            continue
        normalized = re.sub(r"\s+", "", claim)
        if normalized not in missing_claims:
            missing_claims.append(normalized)
    if missing_claims:
        issues.append("最终答案出现本轮证据未提供的数量或比例：" + "、".join(missing_claims[:12]))
    return issues



__all__ = ["_task_status_evidence", "_blocked_task_answer", "_exact_result_contract_answer", "_standard_task_answer_issues"]
