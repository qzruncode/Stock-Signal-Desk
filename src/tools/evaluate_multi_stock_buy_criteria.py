# -*- coding: utf-8 -*-
"""Professional eight-dimension buy analysis for a bounded stock collection."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.services.buy_criteria.professional_analysis import (
    DIMENSION_DEFINITIONS,
    PROFESSIONAL_BUY_CONTRACT_VERSION,
    analyze_professional_buy,
)
from src.tools.base import ToolSpec, object_schema
from src.tools.symbols import resolve_securities_csv


DESCRIPTION = (
    "对完整A股集合逐只执行资深分析师八维买入分析：当前市场主线、产业竞争力、行业周期、"
    "价格战/内卷、政策技术需求供给驱动、未来6—12个月催化、估值赔率、重大风险。"
    "八项全部分析，不因单项较弱提前停止；每项返回通过/半通过/不通过/取证未完成，"
    "并给出正反证据、最终判断、看多链条、风险链条和后续监控指标。"
)


def _dimension_contract() -> list[dict[str, Any]]:
    return [
        {
            "index": index,
            "dimension_id": dimension_id,
            "title": title,
        }
        for index, (dimension_id, title) in enumerate(DIMENSION_DEFINITIONS, 1)
    ]


def evaluate_multi_stock_buy_criteria(
    symbols: str,
    thesis: str = "",
    thesis_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved, unresolved = resolve_securities_csv(symbols)
    requested_count = len(resolved) + len(unresolved)
    if len(resolved) > 300:
        return {
            "success": False,
            "partial": False,
            "items": [],
            "resolved_entities": [],
            "unresolved_entities": unresolved,
            "requested_count": requested_count,
            "covered_count": 0,
            "coverage_complete": False,
            "dimension_order": _dimension_contract(),
            "decision_rule": "每只股票完整分析八维，不提前停止；程序按通过=1、半通过=0.5统一计分",
            "errors": ["单轮最多分析300只股票；本次没有静默截断"],
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
            "dimension_order": _dimension_contract(),
            "decision_rule": "每只股票完整分析八维，不提前停止；程序按通过=1、半通过=0.5统一计分",
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
            item = analyze_professional_buy(
                symbol,
                thesis=thesis.strip(),
                thesis_context=thesis_context,
            )
            item["name"] = entity.get("name") or symbol
            items.append(item)
        except Exception as exc:
            message = f"{entity.get('name') or symbol}({symbol})八维专业买入分析失败：{type(exc).__name__}: {str(exc)[:240]}"
            errors.append(message)
            items.append({
                "contract_version": PROFESSIONAL_BUY_CONTRACT_VERSION,
                "analysis_mode": "professional_eight_dimension_buy_analysis",
                "symbol": symbol,
                "name": entity.get("name") or symbol,
                "thesis": thesis.strip() or None,
                "investment_profile": "分析进程失败",
                "overall_summary": "本轮没有形成可验证的八维完整分析。",
                "core_thesis": "待重新分析",
                "biggest_issue": message,
                "recommendation_code": "evidence_insufficient",
                "recommendation": "关键取证未完成，暂停判断",
                "recommendation_reason": message,
                "score": 0,
                "score_total": 8,
                "counts": {
                    "pass": 0,
                    "partial": 0,
                    "fail": 0,
                    "insufficient": 8,
                },
                "dimensions": [],
                "coverage_complete": False,
                "bull_case_chain": "分析失败，暂不构造看多链条",
                "risk_chain": "分析进程失败 → 证据无法复核 → 暂停买入判断",
                "monitoring_points": ["重新运行完整八维分析"],
                "evidence_gaps": [message],
                "source_links": [],
                "model_error": message,
            })

    if unresolved:
        errors.append("无法解析：" + "、".join(unresolved))
    covered_count = len(items)
    coverage_complete = covered_count == requested_count and not unresolved
    now = datetime.now().astimezone().isoformat()
    return {
        "success": bool(items),
        "partial": bool(errors) or not coverage_complete,
        "contract_version": PROFESSIONAL_BUY_CONTRACT_VERSION,
        "playbook": "professional_eight_dimension_buy_analysis",
        "thesis": thesis.strip() or None,
        "thesis_context": thesis_context,
        "items": items,
        "resolved_entities": resolved,
        "unresolved_entities": unresolved,
        "requested_count": requested_count,
        "covered_count": covered_count,
        "coverage_complete": coverage_complete,
        "dimension_order": _dimension_contract(),
        "decision_rule": (
            "每只股票完整分析八维，不提前停止；程序按通过=1、半通过=0.5统一计分，"
            "重大风险或估值赔率不满足时不得输出条件买入"
        ),
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
                "description": "逗号分隔的A股代码或公司名称，单轮最多300只；执行器内部逐家公司完成八维分析并校验完整覆盖",
            },
            "thesis": {
                "type": "string",
                "description": "从上一轮继承的产业方向或投资逻辑，用于核验主线、真实受益、周期和驱动关系",
                "default": "",
            },
            "thesis_context": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "summary": {"type": "string", "maxLength": 400},
                    "domains": {
                        "type": "array",
                        "maxItems": 12,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "label": {"type": "string"},
                                "board_queries": {"type": "array", "items": {"type": "string"}},
                                "mapping_type": {
                                    "type": "string",
                                    "enum": ["exact_board", "proxy_board", "unresolved"],
                                },
                                "rationale": {"type": "string"},
                                "unresolved_parts": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": [
                                "label", "board_queries", "mapping_type", "rationale", "unresolved_parts",
                            ],
                        },
                    },
                },
            },
        },
        ["symbols"],
    ),
    executor=evaluate_multi_stock_buy_criteria,
    category="analysis",
)
