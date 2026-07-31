"""Aggregate and render typed financial filters over a candidate collection."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from src.agent.result_contracts import CollectionFinancialFilterSpec, FinancialFilterCondition


def evaluate_collection_financial_filter(
    evidence: Optional[List[Dict[str, Any]]],
    condition: FinancialFilterCondition,
) -> Optional[Dict[str, Any]]:
    """Aggregate all batches for one typed predicate without writing prose."""
    packets = [
        packet
        for packet in evidence or []
        if isinstance(packet, dict)
        and packet.get("tool") == "get_multi_stock_financials"
        and isinstance(packet.get("arguments"), dict)
        and packet["arguments"].get("metric") == condition.metric
        and packet["arguments"].get("period_basis") == condition.period_basis
        and packet["arguments"].get("fiscal_year") == condition.fiscal_year
    ]
    if not packets:
        return None

    requested: list[str] = []
    rows_by_code: Dict[str, Dict[str, Any]] = {}
    sources: list[str] = []
    data_times: list[str] = []
    for packet in packets:
        arguments = packet.get("arguments") if isinstance(packet.get("arguments"), dict) else {}
        for code in re.split(r"[,，、;；]+", str(arguments.get("symbols") or "")):
            code = code.strip()
            if re.fullmatch(r"\d{6}", code) and code not in requested:
                requested.append(code)
        result = packet.get("result")
        if not isinstance(result, dict) or result.get("success") is False:
            continue
        source = str(result.get("source") or "").strip()
        if source and source not in sources:
            sources.append(source)
        data_time = str(result.get("data_time") or "").strip()
        if data_time:
            data_times.append(data_time)
        for item in result.get("items") or []:
            if not isinstance(item, dict):
                continue
            code = str(item.get("symbol") or "").strip()
            value = item.get("financial_value")
            if not re.fullmatch(r"\d{6}", code) or not isinstance(value, (int, float)):
                continue
            rows_by_code[code] = item

    missing = [code for code in requested if code not in rows_by_code]
    operator_label = {
        "gt": "高于",
        "gte": "不低于",
        "lt": "低于",
        "lte": "不高于",
        "eq": "等于",
    }[condition.operator]
    ordered_rows = [rows_by_code[code] for code in requested if code in rows_by_code]
    matching = [row for row in ordered_rows if condition.matches(float(row["financial_value"]))]
    excluded = [row for row in ordered_rows if not condition.keeps(float(row["financial_value"]))]
    kept = [row for row in ordered_rows if condition.keeps(float(row["financial_value"]))]
    if condition.threshold_unit == "percent":
        threshold_text = f"{condition.threshold:g}%"
    elif condition.threshold_unit == "cny":
        if abs(condition.threshold) >= 100_000_000:
            threshold_text = f"{condition.threshold / 100_000_000:g} 亿元"
        elif abs(condition.threshold) >= 10_000:
            threshold_text = f"{condition.threshold / 10_000:g} 万元"
        else:
            threshold_text = f"{condition.threshold:g} 元"
    elif condition.threshold_unit == "wan_cny":
        threshold_text = f"{condition.threshold:g} 万元"
    else:
        threshold_text = f"{condition.threshold:g} 亿元"
    annual_years = sorted(
        {
            str(row.get("report_date") or "")[:4]
            for row in ordered_rows
            if re.fullmatch(r"\d{4}", str(row.get("report_date") or "")[:4])
        }
    )
    period_label = {
        "latest_report": "最新报告期",
        "ttm": "TTM",
        "previous_fiscal_year": (f"{annual_years[0]} 年报" if len(annual_years) == 1 else "去年完整年报"),
        "fiscal_year": f"{condition.fiscal_year} 年报",
    }[condition.period_basis]
    action_text = "筛除命中项" if condition.action == "exclude_matching" else "只保留命中项"
    return {
        "requested": requested,
        "rows_by_code": rows_by_code,
        "missing": missing,
        "matching": matching,
        "excluded": excluded,
        "kept": kept,
        "sources": sources,
        "data_times": data_times,
        "operator_label": operator_label,
        "threshold_text": threshold_text,
        "period_label": period_label,
        "action_text": action_text,
    }


def format_collection_financial_value(
    row: Dict[str, Any],
    condition: FinancialFilterCondition,
) -> str:
    value = float(row["financial_value"])
    if condition.metric == "debt_ratio":
        return f"{value:.2f}%"
    if abs(value) >= 100_000_000:
        return f"{value / 100_000_000:.2f} 亿元"
    if abs(value) >= 10_000:
        return f"{value / 10_000:.2f} 万元"
    return f"{value:.2f} 元"


def collection_financial_rule_text(
    condition: FinancialFilterCondition,
    evaluation: Dict[str, Any],
) -> str:
    return (
        f"{evaluation['period_label']}{condition.metric_label} "
        f"{evaluation['operator_label']} {evaluation['threshold_text']}，"
        f"{evaluation['action_text']}"
    )


def collection_source_boundary(
    evidence: Optional[List[Dict[str, Any]]],
) -> List[str]:
    """Render collection provenance supplied by an upstream source contract."""
    domain_result = next(
        (
            packet.get("result")
            for packet in evidence or []
            if isinstance(packet, dict)
            and isinstance(packet.get("result"), dict)
            and isinstance(packet["result"].get("domain_results"), list)
        ),
        None,
    )
    if not isinstance(domain_result, dict):
        return []

    coverage_lines: List[str] = []
    covered: List[str] = []
    unresolved: List[str] = []
    for item in domain_result.get("domain_results") or []:
        if not isinstance(item, dict):
            continue
        domain = str(item.get("domain") or "未命名领域")
        themes = [str(value) for value in item.get("lookup_themes") or [] if value]
        count = int(item.get("candidate_count") or 0)
        rationale = str(item.get("mapping_rationale") or "").strip()
        if item.get("mapping_type") == "unresolved" or not themes:
            unresolved.append(domain)
            coverage_lines.append(
                f"- **{domain}**：当前目录未解析，未纳入候选集合。" + (f" {rationale}" if rationale else "")
            )
            continue
        covered.append(domain)
        coverage_lines.append(f"- **{domain}**：结构化板块 `{'、'.join(themes)}`，" f"候选 **{count} 只**。")
    if not coverage_lines:
        return []

    lines = ["### 候选集合来源", "", *coverage_lines, ""]
    if unresolved:
        lines.append(
            "> 本轮财务筛选仅覆盖已解析领域"
            + (f"（{'、'.join(covered)}）" if covered else "")
            + f"；{'、'.join(unresolved)}未被近似板块替代。"
        )
    lines.append("> 候选仅证明结构化板块成员关系，不证明公司正在大力发展该业务，" "也不代表订单、收入兑现或投资建议。")
    return lines


def build_collection_financial_filter_answer(
    evidence: Optional[List[Dict[str, Any]]],
    spec: CollectionFinancialFilterSpec,
) -> Optional[str]:
    """Render one collection transform containing one or more predicates."""
    evaluated: list[tuple[FinancialFilterCondition, Dict[str, Any]]] = []
    for condition in spec.conditions:
        evaluation = evaluate_collection_financial_filter(evidence, condition)
        if evaluation is None:
            return None
        evaluated.append((condition, evaluation))

    requested = list(evaluated[0][1]["requested"])
    requested_set = set(requested)
    missing_by_rule: list[tuple[str, list[str]]] = []
    final_kept = set(requested)
    rows_by_code: Dict[str, Dict[str, Any]] = {}
    excluded_reasons: Dict[str, List[str]] = {}
    sources: list[str] = []
    data_times: list[str] = []

    for condition, evaluation in evaluated:
        rule_text = collection_financial_rule_text(condition, evaluation)
        rule_requested = set(evaluation["requested"])
        missing = sorted(set(evaluation["missing"]) | (requested_set - rule_requested))
        if missing:
            missing_by_rule.append((rule_text, missing))
        kept_codes = {str(row.get("symbol") or "") for row in evaluation["kept"]}
        final_kept &= kept_codes
        for code, row in evaluation["rows_by_code"].items():
            rows_by_code.setdefault(code, row)
        for row in evaluation["excluded"]:
            code = str(row.get("symbol") or "")
            if not code:
                continue
            reason = (
                f"{evaluation['period_label']}{condition.metric_label} "
                f"{format_collection_financial_value(row, condition)}"
            )
            excluded_reasons.setdefault(code, []).append(reason)
        for source in evaluation["sources"]:
            if source not in sources:
                sources.append(source)
        data_times.extend(evaluation["data_times"])

    final_excluded = [code for code in requested if code not in final_kept]
    final_kept_ordered = [code for code in requested if code in final_kept]
    if len(evaluated) == 1:
        condition, evaluation = evaluated[0]
        lines = [
            f"## 上文股票{evaluation['period_label']}{condition.metric_label}筛选",
            "",
            f"规则：{collection_financial_rule_text(condition, evaluation)}。",
            f"原集合 **{len(requested)} 只**，成功覆盖 "
            f"**{len(evaluation['rows_by_code'])} 只**，缺失 "
            f"**{len(evaluation['missing'])} 只**。",
        ]
    else:
        lines = [
            "## 上文股票复合财务筛选",
            "",
            f"原集合 **{len(requested)} 只**，本轮同时执行 **{len(evaluated)} 项**财务条件。",
            "",
            "### 筛选规则",
            "",
        ]
        lines.extend(
            f"{index}. {collection_financial_rule_text(condition, evaluation)}。"
            for index, (condition, evaluation) in enumerate(evaluated, 1)
        )
    source_boundary = collection_source_boundary(evidence)
    if source_boundary:
        lines.extend(["", *source_boundary])
    if missing_by_rule:
        lines.extend(
            [
                "",
                (
                    "> 本轮筛选未完成，不能把已覆盖的部分结果当作完整名单。"
                    if len(evaluated) == 1
                    else "> 本轮复合筛选未完成，不能把部分覆盖结果当作最终名单。"
                ),
            ]
        )
        for rule_text, missing in missing_by_rule:
            lines.append(f"> {rule_text}：缺失 {'、'.join(missing)}。")
    else:
        lines.extend(
            [
                "",
                (
                    f"完整筛选结果：筛除 **{len(final_excluded)} 只**，筛选后保留 "
                    f"**{len(final_kept_ordered)} 只**。"
                    if len(evaluated) == 1
                    else f"全部条件均完整覆盖 **{len(requested)} 只**；合并后筛除 "
                    f"**{len(final_excluded)} 只**，最终保留 **{len(final_kept_ordered)} 只**。"
                ),
            ]
        )

    lines.extend(["", "### 筛除项", ""])
    if not final_excluded:
        lines.append("无。")
    else:
        lines.extend(["| 公司/代码 | 命中或未满足的条件 |", "|---|---|"])
        for code in final_excluded:
            row = rows_by_code.get(code, {})
            reasons = excluded_reasons.get(code) or ["未满足全部保留条件"]
            lines.append(f"| {row.get('name') or '未命名'} ({code}) | {'；'.join(reasons)} |")

    kept_title = "最终保留项" if not missing_by_rule else "已覆盖范围内的暂定保留项"
    lines.extend(["", f"### {kept_title}", ""])
    if not final_kept_ordered:
        lines.append("无。")
    else:
        lines.extend(["| 公司/代码 | 结果 |", "|---|---|"])
        for code in final_kept_ordered:
            row = rows_by_code.get(code, {})
            lines.append(f"| {row.get('name') or '未命名'} ({code}) | 全部条件通过 |")

    lines.extend(
        [
            "",
            "> 数据来源："
            + ("；".join(sources) or "本地已同步财务库")
            + (f"；同步时间 {max(data_times)}" if data_times else "；同步时间未标明")
            + (
                "。多条件结果由程序按集合交集计算，未交给模型改写名单。"
                if len(evaluated) > 1
                else "。筛选集合由程序按强类型条件计算，未交给模型改写名单。"
            ),
        ]
    )
    return "\n".join(lines)


__all__ = [
    "build_collection_financial_filter_answer",
    "collection_financial_rule_text",
    "collection_source_boundary",
    "evaluate_collection_financial_filter",
    "format_collection_financial_value",
]
