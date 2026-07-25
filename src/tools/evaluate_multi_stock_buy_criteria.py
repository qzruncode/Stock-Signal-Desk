"""Eight-dimensional Boolean buy gates for a complete stock collection."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

from src.services.buy_criteria.orchestrator import (
    BUY_GATE_CONTRACT_VERSION,
    CriterionOrchestrator,
)
from src.services.buy_criteria.professional_analysis import (
    DIMENSION_DEFINITIONS,
    PROFESSIONAL_BUY_ANALYSIS_MODE,
)
from src.tools.base import ToolSpec, object_schema
from src.tools.symbols import resolve_securities_csv

DESCRIPTION = (
    "对完整A股集合逐只执行用户定义的八维串行布尔买入闸门。每一维由模型阅读该维证据"
    "后返回明确布尔结果；程序只控制顺序、证据校验和首个失败即停止。连续八维全部通过"
    "才可买入，不使用关键词命中、固定分数或跨关抵消。"
)


def _gate_contract() -> list[dict[str, Any]]:
    return [
        {
            "index": index,
            "criterion_id": dimension_id,
            "criterion_name": title,
        }
        for index, (dimension_id, title) in enumerate(DIMENSION_DEFINITIONS)
    ]


def _failed_item(
    entity: dict[str, Any],
    message: str,
    *,
    thesis: str,
) -> dict[str, Any]:
    first_dimension_id, first_dimension_name = DIMENSION_DEFINITIONS[0]
    blocking_criterion = {
        "criterion_id": first_dimension_id,
        "criterion_name": first_dimension_name,
        "index": 0,
        "passed": False,
        "status": "insufficient",
        "confidence": "",
        "verdict": message,
        "details": {},
    }
    return {
        "contract_version": BUY_GATE_CONTRACT_VERSION,
        "analysis_mode": PROFESSIONAL_BUY_ANALYSIS_MODE,
        "symbol": str(entity.get("symbol") or ""),
        "name": entity.get("name") or entity.get("symbol"),
        "thesis": thesis or None,
        "final_decision": "不可买入",
        "coverage_complete": False,
        "gate_pass_complete": False,
        "passed_count": 0,
        "failed_count": 0,
        "insufficient_count": 1,
        "not_evaluated_count": len(DIMENSION_DEFINITIONS) - 1,
        "total": len(DIMENSION_DEFINITIONS),
        "stopped_at": first_dimension_id,
        "stopped_at_name": first_dimension_name,
        "stopped_verdict": message,
        "blocking_reasons": [blocking_criterion],
        "criteria": [blocking_criterion],
        "position_advice": {
            "initial_position_pct": 0,
            "max_position_pct": 0,
        },
        "invalidation_conditions": [],
    }


def evaluate_multi_stock_buy_criteria(
    symbols: str,
    thesis: str = "",
    thesis_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved, unresolved = resolve_securities_csv(symbols)
    requested_count = len(resolved) + len(unresolved)
    clean_thesis = str(thesis or "").strip()
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
            "gate_order": _gate_contract(),
            "decision_rule": (
                "每只股票首项失败立即停止；连续八维全部通过才可买入"
            ),
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
            "gate_order": _gate_contract(),
            "decision_rule": (
                "每只股票首项失败立即停止；连续八维全部通过才可买入"
            ),
            "errors": ["没有可验证的A股公司名称或代码"],
            "warnings": [],
            "data_time": None,
            "is_stale": None,
        }

    results_by_symbol: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    def analyze(entity: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
        symbol = str(entity["symbol"])
        try:
            result = CriterionOrchestrator().analyze_for_agent(
                symbol,
                thesis=clean_thesis,
                thesis_context=thesis_context,
            )
            result["name"] = entity.get("name") or symbol
            return symbol, result, ""
        except Exception as exc:
            message = (
                f"{entity.get('name') or symbol}({symbol})严格买入判断失败："
                f"{type(exc).__name__}: {str(exc)[:240]}"
            )
            return (
                symbol,
                _failed_item(
                    entity,
                    message,
                    thesis=clean_thesis,
                ),
                message,
            )

    with ThreadPoolExecutor(
        max_workers=min(4, max(1, len(resolved))),
    ) as pool:
        futures = {
            pool.submit(analyze, entity): entity
            for entity in resolved
        }
        for future in as_completed(futures):
            symbol, result, error = future.result()
            results_by_symbol[symbol] = result
            if error:
                errors.append(error)

    items = [
        results_by_symbol[str(entity["symbol"])]
        for entity in resolved
    ]
    if unresolved:
        errors.append("无法解析：" + "、".join(unresolved))
    covered_count = len(items)
    coverage_complete = (
        covered_count == len(resolved)
        and not unresolved
    )
    now = datetime.now().astimezone().isoformat()
    return {
        "success": bool(items),
        "partial": bool(errors) or not coverage_complete,
        "contract_version": BUY_GATE_CONTRACT_VERSION,
        "playbook": PROFESSIONAL_BUY_ANALYSIS_MODE,
        "thesis": clean_thesis or None,
        "thesis_context": thesis_context,
        "items": items,
        "resolved_entities": resolved,
        "unresolved_entities": unresolved,
        "requested_count": requested_count,
        "covered_count": covered_count,
        "coverage_complete": coverage_complete,
        "gate_order": _gate_contract(),
        "decision_rule": (
            "每只股票逐关执行，首个fail或insufficient立即停止；"
            "连续八维全部pass且集合覆盖完整才可买入"
        ),
        "errors": errors,
        "warnings": [],
        "data_time": now,
        "is_stale": None,
        "source": (
            "内部同步股票池、市场证据、公司披露、财务、公告、"
            "行情及技术指标"
        ),
    }


TOOL = ToolSpec(
    name="evaluate_multi_stock_buy_criteria",
    description=DESCRIPTION,
    parameters=object_schema(
        {
            "symbols": {
                "type": "string",
                "description": (
                    "完整股票集合，代码或名称用逗号分隔；"
                    "不得只传集合的前几只"
                ),
            },
            "thesis": {
                "type": "string",
                "description": (
                    "结构化上下文中的投资逻辑摘要，"
                    "仅作为待核验命题，不作为事实证据"
                ),
                "default": "",
            },
            "thesis_context": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "summary": {
                        "type": "string",
                        "maxLength": 400,
                    },
                    "domains": {
                        "type": "array",
                        "maxItems": 12,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "label": {"type": "string"},
                                "board_queries": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "mapping_type": {
                                    "type": "string",
                                    "enum": [
                                        "catalog_binding",
                                        "unresolved",
                                    ],
                                },
                                "rationale": {"type": "string"},
                                "unresolved_parts": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": [
                                "label",
                                "board_queries",
                                "mapping_type",
                                "rationale",
                                "unresolved_parts",
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
