# -*- coding: utf-8 -*-
"""Strict, sequential buy-gate evaluation for a bounded stock collection."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.services.buy_criteria.evaluators import EVALUATOR_CLASSES
from src.services.buy_criteria.orchestrator import CriterionOrchestrator
from src.tools.base import ToolSpec, object_schema
from src.tools.symbols import resolve_securities_csv


DESCRIPTION = (
    "对最多8只A股执行唯一的严格买入判断链：当前市场主线真实受益、产业竞争力、未来3年空间、"
    "景气上行、不过度内卷、未来6—12个月催化、无重大风险、估值与利好是否透支、当前买入位置"
    "与风险收益比。每只股票在首个未通过项立即停止，九项全部通过才返回可买入；分业务收入或利润"
    "未单独披露时，会使用订单、销量、客户、产能、量产和连续增速核验。结果含仓位和逻辑失效条件。"
)


def _gate_contract() -> list[dict[str, Any]]:
    return [
        {
            "index": evaluator.index,
            "criterion_id": evaluator.criterion_id,
            "criterion_name": evaluator.criterion_name,
        }
        for evaluator in EVALUATOR_CLASSES
    ]


def evaluate_multi_stock_buy_criteria(symbols: str, thesis: str = "") -> dict[str, Any]:
    resolved, unresolved = resolve_securities_csv(symbols)
    requested_count = len(resolved) + len(unresolved)
    if len(resolved) > 8:
        return {
            "success": False,
            "partial": False,
            "items": [],
            "resolved_entities": [],
            "unresolved_entities": unresolved,
            "requested_count": requested_count,
            "covered_count": 0,
            "coverage_complete": False,
            "gate_order": _gate_contract(),
            "decision_rule": "每只股票首项失败即停止；九项全部通过才可买入",
            "errors": ["单次最多分析8只股票，请由工作流分批调用；本次没有静默截断"],
            "warnings": [],
            "data_time": None,
            "is_stale": None,
        }
    if not resolved:
        return {
            "success": False,
            "partial": False,
            "items": [],
            "resolved_entities": [],
            "unresolved_entities": unresolved,
            "requested_count": requested_count,
            "covered_count": 0,
            "coverage_complete": False,
            "gate_order": _gate_contract(),
            "decision_rule": "每只股票首项失败即停止；九项全部通过才可买入",
            "errors": ["没有可验证的A股公司名称或代码"],
            "warnings": [],
            "data_time": None,
            "is_stale": None,
        }

    items: list[dict[str, Any]] = []
    errors: list[str] = []
    for entity in resolved:
        symbol = str(entity["symbol"])
        try:
            item = CriterionOrchestrator().analyze_for_agent(symbol, thesis=thesis.strip())
            item["name"] = entity.get("name") or symbol
            items.append(item)
        except Exception as exc:
            message = f"{entity.get('name') or symbol}({symbol})严格买入判断失败：{type(exc).__name__}: {str(exc)[:240]}"
            errors.append(message)
            items.append({
                "symbol": symbol,
                "name": entity.get("name") or symbol,
                "thesis": thesis.strip() or None,
                "final_decision": "不可买入",
                "coverage_complete": False,
                "passed_count": 0,
                "failed_count": 1,
                "not_evaluated_count": len(EVALUATOR_CLASSES),
                "total": len(EVALUATOR_CLASSES),
                "stopped_at": "analysis_error",
                "stopped_at_name": "数据或分析失败",
                "stopped_verdict": message,
                "criteria": [],
                "position_advice": {"initial_position_pct": 0, "max_position_pct": 0},
                "invalidation_conditions": [],
            })

    if unresolved:
        errors.append("无法解析：" + "、".join(unresolved))
    covered_count = len(items)
    coverage_complete = covered_count == requested_count and not unresolved
    now = datetime.now().astimezone().isoformat()
    return {
        "success": bool(items),
        "partial": bool(errors) or not coverage_complete,
        "playbook": "strict_sequential_buy_decision",
        "thesis": thesis.strip() or None,
        "items": items,
        "resolved_entities": resolved,
        "unresolved_entities": unresolved,
        "requested_count": requested_count,
        "covered_count": covered_count,
        "coverage_complete": coverage_complete,
        "gate_order": _gate_contract(),
        "decision_rule": "每只股票按顺序执行；首项失败即停止；九项全部通过且集合覆盖完整才可买入",
        "substitute_business_evidence": ["订单", "销量", "客户", "产能", "产量或量产", "连续增速"],
        "errors": errors,
        "warnings": [],
        "data_time": now,
        "is_stale": None,
        "source": "内部同步股票池、市场主题、公司披露、财务、公告、行情及技术指标",
    }


TOOL = ToolSpec(
    name="evaluate_multi_stock_buy_criteria",
    description=DESCRIPTION,
    parameters=object_schema(
        {
            "symbols": {
                "type": "string",
                "description": "逗号分隔的A股代码或公司名称，单次最多8只；工作流会自动分批",
            },
            "thesis": {
                "type": "string",
                "description": "从上一轮继承的产业方向或投资逻辑，用于核验真实受益关系",
                "default": "",
            },
        },
        ["symbols"],
    ),
    executor=evaluate_multi_stock_buy_criteria,
    category="analysis",
)
