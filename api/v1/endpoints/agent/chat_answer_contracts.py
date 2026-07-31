# -*- coding: utf-8 -*-
"""Structural answer contract validation for final chat synthesis."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from api.v1.endpoints.agent.chat_decision_renderers import _build_professional_buy_decision_answer
from src.agent.result_contracts import (
    AnalysisPlaybook,
    INVESTMENT_DECISION,
    MARKET_OUTLOOK,
    STOCK_DEEP_RESEARCH,
    THEME_COMPANY_MAPPING,
)

def _unsupported_final_claims(
    content: str,
    evidence: Optional[List[Dict[str, Any]]],
) -> List[str]:
    """Validate objective date consistency without interpreting prose."""
    del evidence
    reasons: List[str] = []
    weekday_labels = "一二三四五六日"
    for match in re.finditer(
        r"(20\d{2})[-年](\d{1,2})[-月](\d{1,2})日?\s*[（(]周([一二三四五六日天])[）)]",
        content,
    ):
        try:
            stated_date = datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).date()
        except ValueError:
            continue
        stated_weekday = "日" if match.group(4) == "天" else match.group(4)
        actual_weekday = weekday_labels[stated_date.weekday()]
        if stated_weekday != actual_weekday:
            reasons.append(f"日期星期不一致：{stated_date.isoformat()} 应为周{actual_weekday}")
    return reasons


def _professional_answer_contract_issues(
    content: str,
    evidence: Optional[List[Dict[str, Any]]],
) -> List[str]:
    """Protect the exact Boolean result without keyword-scanning prose."""
    professional_buy_answer = _build_professional_buy_decision_answer(evidence)
    if professional_buy_answer is not None:
        return (
            []
            if content.strip() == professional_buy_answer.strip()
            else ["专业买入分析必须使用程序校验后的八维结果，不能由最终写作模型改写"]
        )
    return []


def _sanitize_mapping_answer(content: str) -> str:
    """Compatibility no-op; semantic validation uses typed model output."""
    return content


def _prepare_playbook_answer(playbook: Optional[AnalysisPlaybook], content: str) -> str:
    del playbook
    return content


def _playbook_answer_contract_issues(
    playbook: Optional[AnalysisPlaybook],
    content: str,
    evidence: Optional[List[Dict[str, Any]]],
) -> List[str]:
    """Validate the final prose against the runtime-selected Playbook.

    Tool routing alone is not sufficient: a model can retrieve the right
    evidence and still collapse the answer into a loose opinion.  These checks
    are deliberately structural and conservative; they do not pretend to
    judge investment correctness, but they prevent required dimensions from
    disappearing during synthesis.
    """
    if playbook is None:
        return []
    if playbook.id == INVESTMENT_DECISION.id:
        professional_buy_answer = _build_professional_buy_decision_answer(evidence)
        if professional_buy_answer is None:
            return ["八维专业买入分析结果未成功取得，必须停止买入判断"]
        return (
            []
            if content.strip() == professional_buy_answer.strip()
            else ["八维专业买入分析只能由程序按已校验结构生成，不能由最终写作模型改写"]
        )
    if playbook.id == STOCK_DEEP_RESEARCH.id:
        return _professional_answer_contract_issues(content, evidence)

    if playbook.id == MARKET_OUTLOOK.id:
        issues: List[str] = []
        if not any(term in content for term in ("主线排序", "候选排序", "基准情景")):
            issues.append("市场主线研判必须先给基准情景或候选主线排序")
        if "成立条件" not in content:
            issues.append("每个候选主线必须给出成立条件")
        if not any(term in content for term in ("失效信号", "失效条件")):
            issues.append("每个候选主线必须给出失效信号")
        if not any(term in content for term in ("乐观情景", "谨慎情景", "情景切换")):
            issues.append("市场主线研判必须说明情景切换")
        if "置信" not in content:
            issues.append("市场主线研判必须标注相对置信度")
        return issues

    if playbook.id == THEME_COMPANY_MAPPING.id:
        return []
    return []


def _generic_answer_contract_issues(
    content: str,
    evidence: Optional[List[Dict[str, Any]]],
) -> List[str]:
    """Reject structurally incomplete answers even without a Playbook.

    A provider can terminate normally after emitting only a Markdown table
    header.  That is not a valid answer, especially when a successful batch
    tool already returned every requested company.
    """
    issues: List[str] = []
    lines = content.splitlines()
    for index in range(len(lines) - 1):
        header = lines[index].strip()
        separator = lines[index + 1].strip()
        if not header.startswith("|") or not separator.startswith("|"):
            continue
        separator_cells = [cell.strip() for cell in separator.strip("|").split("|")]
        if not separator_cells or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator_cells):
            continue
        data_row_count = 0
        for row in lines[index + 2 :]:
            stripped = row.strip()
            if not stripped:
                break
            if not stripped.startswith("|"):
                break
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if cells and not all(re.fullmatch(r"[:\- ]+", cell or "") for cell in cells):
                data_row_count += 1
        if data_row_count == 0:
            issues.append("Markdown表格只有表头，没有任何数据行")

    batch_result = next(
        (
            packet.get("result")
            for packet in evidence or []
            if isinstance(packet, dict)
            and packet.get("tool") == "get_multi_stock_snapshot"
            and isinstance(packet.get("result"), dict)
            and packet["result"].get("success") is not False
        ),
        None,
    )
    batch_items = batch_result.get("items") if isinstance(batch_result, dict) else None
    if isinstance(batch_items, list):
        expected_codes = {
            str(item.get("symbol") or "")
            for item in batch_items
            if isinstance(item, dict) and re.fullmatch(r"\d{6}", str(item.get("symbol") or ""))
        }
        answer_codes = set(re.findall(r"(?<!\d)(\d{6})(?!\d)", content))
        missing_codes = sorted(expected_codes - answer_codes)
        if missing_codes:
            issues.append(
                f"多股快照返回{len(expected_codes)}家公司，最终答案遗漏{len(missing_codes)}家："
                + "、".join(missing_codes)
            )
    return issues


def _final_answer_contract_issues(
    playbook: Optional[AnalysisPlaybook],
    content: str,
    evidence: Optional[List[Dict[str, Any]]],
) -> List[str]:
    return (
        _unsupported_final_claims(content, evidence)
        + _generic_answer_contract_issues(content, evidence)
        + _playbook_answer_contract_issues(playbook, content, evidence)
    )



__all__ = ["_final_answer_contract_issues"]
