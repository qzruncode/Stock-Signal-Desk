# -*- coding: utf-8 -*-
"""Deterministic professional-buy renderers."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from src.services.buy_criteria.professional_analysis import DIMENSION_DEFINITIONS
from src.tools.evaluate_multi_stock_buy_criteria import public_buy_analysis_error


def _build_professional_decision_fallback(
    batch: Dict[str, Any],
    *,
    decision_requested: Optional[bool] = None,
) -> str:
    """Report evidence coverage when semantic synthesis is unavailable."""
    del decision_requested
    rows: List[str] = []
    for item in batch.get("items") or []:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "—")
        name = str(item.get("name") or symbol)
        coverage = item.get("evidence_coverage") if isinstance(item.get("evidence_coverage"), dict) else {}
        missing = "、".join(str(value) for value in coverage.get("missing") or []) or "无"
        rows.append(f"| {name} ({symbol}) | {'完整' if coverage.get('complete') else '不完整'} | {missing} |")
    table = "\n".join(rows) if rows else "| — | 未返回 | 全部证据 |"
    return (
        "## 深度研究证据已获取，但语义综合未完成\n\n"
        "本轮模型没有形成可校验的结构化分析，程序不会用固定阈值代替分析师下结论。\n\n"
        "| 公司/代码 | 证据覆盖 | 缺失项 |\n"
        "|---|---|---|\n" + table + "\n\n请重试本轮分析；证据可以复用，未形成结构化结论前不输出买入或规避判断。"
    )


_PROFESSIONAL_BUY_DIMENSION_IDS = tuple(dimension_id for dimension_id, _title in DIMENSION_DEFINITIONS)


def _build_professional_buy_decision_answer(
    evidence: Optional[List[Dict[str, Any]]],
) -> Optional[str]:
    """Render only the validated eight-dimension Boolean state machine."""
    packets = [
        packet
        for packet in evidence or []
        if isinstance(packet, dict) and packet.get("tool") == "evaluate_multi_stock_buy_criteria"
    ]
    if not packets:
        return None

    requested: List[str] = []
    items_by_code: Dict[str, Dict[str, Any]] = {}
    execution_failures: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []
    mainline_strategies: List[str] = []
    for packet in packets:
        arguments = packet.get("arguments") if isinstance(packet.get("arguments"), dict) else {}
        packet_codes: List[str] = []
        for code in re.split(r"[,，、;；]+", str(arguments.get("symbols") or "")):
            code = code.strip()
            if re.fullmatch(r"\d{6}", code) and code not in requested:
                requested.append(code)
            if re.fullmatch(r"\d{6}", code):
                packet_codes.append(code)
        result = packet.get("result")
        if not isinstance(result, dict):
            for code in packet_codes:
                execution_failures[code] = {
                    "executed": packet.get("executed") is not False,
                    "error": "工具没有返回结构化结果",
                }
            continue
        result_errors = [public_buy_analysis_error(value) for value in result.get("errors") or [] if value]
        errors.extend(result_errors)
        strategy = str(result.get("mainline_strategy") or "").strip()
        if strategy and strategy not in mainline_strategies:
            mainline_strategies.append(strategy)
        if not result.get("items"):
            for code in packet_codes:
                execution_failures[code] = {
                    "executed": packet.get("executed") is not False,
                    "error": "；".join(result_errors) or "工具没有返回逐股八维结果",
                }
        for item in result.get("items") or []:
            if not isinstance(item, dict):
                continue
            code = str(item.get("symbol") or "").strip()
            if re.fullmatch(r"\d{6}", code):
                items_by_code[code] = item

    if not requested:
        requested = list(items_by_code)
    if not requested:
        return "## 八维专业买入判断未执行\n\n本轮没有取得可核验的股票代码。"

    rows: List[str] = []
    details: List[str] = []
    buyable: List[str] = []
    rejected: List[str] = []
    unavailable_items: List[str] = []
    failed_items: List[str] = []
    missing = [code for code in requested if code not in items_by_code]
    labels = {
        "pass": "通过",
        "fail": "不符合本次买入条件",
        "insufficient": "分析未完成",
    }
    for code in requested:
        item = items_by_code.get(code)
        if not isinstance(item, dict):
            failure = execution_failures.get(code) or {}
            executed = failure.get("executed") is not False
            conclusion = "执行失败" if executed else "未执行"
            reason = str(
                failure.get("error") or ("前置流程阻止了逐股调用" if not executed else "逐股工具未返回结构化结果")
            )
            rows.append(f"| {code} | **{conclusion}** | {reason[:120]} | — |")
            details.append(
                f"### {code}\n\n- **{conclusion}**：{reason}\n" "- 本轮没有形成任何八维判断，不得解释为某项不通过。"
            )
            continue
        name = str(item.get("name") or code)
        criteria = [value for value in item.get("criteria") or [] if isinstance(value, dict)]
        gate_ids = tuple(str(value.get("criterion_id") or "") for value in criteria)
        statuses = tuple(str(value.get("status") or "insufficient") for value in criteria)
        valid_prefix = gate_ids == _PROFESSIONAL_BUY_DIMENSION_IDS[: len(gate_ids)]
        all_pass = (
            gate_ids == _PROFESSIONAL_BUY_DIMENSION_IDS
            and all(status == "pass" for status in statuses)
            and item.get("gate_pass_complete") is True
            and item.get("final_decision") == "可买入"
        )
        analysis_status = str(item.get("analysis_status") or "").strip()
        if not valid_prefix:
            analysis_status = "execution_failed"
        elif not analysis_status:
            analysis_status = "source_unavailable" if "insufficient" in statuses else "completed"
        elif analysis_status == "evidence_insufficient":
            # Read-only compatibility for V8 persisted results.
            analysis_status = "source_unavailable"
        conclusion = (
            "分析失败"
            if analysis_status == "execution_failed"
            else (
                "分析未完成"
                if analysis_status == "source_unavailable"
                else "可买入" if all_pass else "不符合本次买入条件"
            )
        )
        if all_pass:
            buyable.append(f"{name} ({code})")
        elif conclusion == "不符合本次买入条件":
            rejected.append(f"{name} ({code})")
        elif conclusion == "分析未完成":
            unavailable_items.append(f"{name} ({code})")
        else:
            failed_items.append(f"{name} ({code})")
        stopped = next(
            (gate for gate, status in zip(criteria, statuses) if status != "pass"),
            None,
        )
        stop_name = (
            "模型或执行异常"
            if conclusion == "分析失败"
            else (
                "关键来源未完成"
                if conclusion == "分析未完成"
                else (
                    str((stopped or {}).get("criterion_name") or item.get("stopped_at_name") or "执行结构异常")
                    if not all_pass
                    else "八维全部通过"
                )
            )
        )
        executed_count = len(criteria)
        progress = (
            "8/8"
            if all_pass
            else (
                f"{executed_count}/8，分析异常"
                if conclusion == "分析失败"
                else (
                    f"{executed_count}/8，分析未完成"
                    if conclusion == "分析未完成"
                    else f"{executed_count}/8，首个阻断后停止"
                )
            )
        )
        rows.append(f"| {name} ({code}) | **{conclusion}** | {stop_name} | {progress} |")

        gate_lines: List[str] = []
        if conclusion == "分析失败":
            gate_lines.append(
                "- **分析失败**："
                + public_buy_analysis_error(
                    item.get("model_error") or item.get("stopped_verdict") or "本轮没有形成可校验的结构化结果"
                )
            )
        elif conclusion == "分析未完成":
            gate_lines.append("- **分析未完成**：关键来源未完成，本轮不对公司形成买入结论。")
        for gate, status in zip(criteria, statuses):
            index = int(gate.get("index") or 0) + 1
            title = gate.get("criterion_name") or gate.get("criterion_id")
            verdict = str(gate.get("verdict") or "未给出理由")
            rendered_verdict = verdict.replace(
                "证据不足",
                ("本轮分析未完成" if status == "insufficient" else "未达到本次准入证明"),
            )
            gate_lines.append(f"- {index}. **{title}：{labels.get(status, '分析未完成')}**。" f"{rendered_verdict}")
            gate_details = gate.get("details") if isinstance(gate.get("details"), dict) else {}
            classification = (
                gate_details.get("mainline_classification")
                if isinstance(
                    gate_details.get("mainline_classification"),
                    dict,
                )
                else {}
            )
            if classification:
                relation_label = {
                    "core": "主线核心",
                    "active_branch": "当前活跃分支",
                    "emerging_branch": "候选主线分支",
                    "long_term_only": "仅长期趋势",
                    "unrelated": "未形成主线关系",
                }.get(
                    str(classification.get("direction_relation") or ""),
                    "待核验",
                )
                lifecycle_label = {
                    "emerging": "酝酿期",
                    "validating": "验证期",
                    "confirmed": "确认期",
                    "expanding": "扩散期",
                    "fading": "退潮期",
                }.get(
                    str(classification.get("lifecycle") or ""),
                    "未确定",
                )
                matched_mainline = str(classification.get("matched_mainline") or "无")
                matched_branch = str(classification.get("matched_branch") or "核心方向")
                gate_lines.append(
                    "  - 主线归属："
                    f"{matched_mainline} / {matched_branch}；"
                    f"关系：{relation_label}；阶段：{lifecycle_label}。"
                )
            evidence_items = [str(value) for value in gate_details.get("key_evidence") or [] if str(value).strip()]
            counter_items = [str(value) for value in gate_details.get("counter_evidence") or [] if str(value).strip()]
            if evidence_items:
                gate_lines.append("  - 支持证据：" + "；".join(evidence_items))
            if counter_items:
                gate_lines.append("  - 主要反证：" + "；".join(counter_items))
        if not valid_prefix:
            gate_lines.append(
                "- **执行结构异常**：维度不符合八维契约顺序，" "本轮标记为分析失败，不得解释为某项事实性不通过。"
            )
        elif len(criteria) < len(_PROFESSIONAL_BUY_DIMENSION_IDS):
            gate_lines.append(
                f"- 后续 {len(_PROFESSIONAL_BUY_DIMENSION_IDS) - len(criteria)} 维未执行："
                + (
                    "模型或执行异常中断了本轮分析。"
                    if conclusion == "分析失败"
                    else (
                        "关键来源未完成，系统已中止且未形成公司结论。"
                        if conclusion == "分析未完成"
                        else "首个未达到正向准入条件的维度已经关闭该股买入闸门。"
                    )
                )
            )
        details.append(f"### {name} ({code})\n\n" + "\n".join(gate_lines))

    if missing:
        collection_result = "集合覆盖不完整，本轮不输出部分集合的最终买入名单。"
    else:
        result_parts: List[str] = []
        if buyable:
            result_parts.append("八维全部通过：**" + "、".join(buyable) + "**")
        if rejected:
            result_parts.append(f"不符合本次买入条件 {len(rejected)} 只")
        if unavailable_items:
            result_parts.append(f"分析未完成 {len(unavailable_items)} 只")
        if failed_items:
            result_parts.append(f"分析失败 {len(failed_items)} 只")
        collection_result = "；".join(result_parts) + "。"
    coverage = f"请求 {len(requested)} 只，返回 {len(items_by_code)} 只，缺失 {len(missing)} 只。"
    if errors:
        coverage += " 执行异常：" + "；".join(dict.fromkeys(errors)) + "。"
    strategy_labels = [
        {
            "confirmed_mainline": "确认型主线",
            "early_positioning": "前瞻布局型",
        }.get(value, value)
        for value in mainline_strategies
    ]
    strategy_rule = "\n- 本轮主线策略：" + "、".join(strategy_labels) + "。" if strategy_labels else ""
    return (
        "## 八维专业买入判断\n\n"
        + collection_result
        + "\n\n| 公司/代码 | 结论 | 首个停止项 | 进度 |\n"
        + "|---|---|---|---|\n"
        + "\n".join(rows)
        + "\n\n## 逐股闸门记录\n\n"
        + "\n\n".join(details)
        + "\n\n## 覆盖与规则\n\n- "
        + coverage
        + strategy_rule
        + "\n- 每只股票独立执行；首个 fail 表示未达到本次正向准入条件并立即停止。"
        + "\n- 关键来源或执行故障只标记分析未完成，不得改写成公司不符合。"
        + "\n- 只有八维按契约顺序全部 pass，且集合覆盖完整，程序才允许输出可买入。"
    )



__all__ = ["_build_professional_decision_fallback", "_build_professional_buy_decision_answer"]
