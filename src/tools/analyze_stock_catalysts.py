# -*- coding: utf-8 -*-
"""Evidence-bound 6–12 month catalyst research for verified A-share entities."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.services.buy_criteria.data_service import DataService, _clear_cache
from src.services.buy_criteria.evaluators.catalyst_events import CatalystEventsEvaluator
from src.tools.base import ToolSpec, object_schema
from src.tools.symbols import resolve_securities_csv


DESCRIPTION = (
    "核验最多8只A股未来6—12个月的公司催化事件。读取内部同步的完整公告目录、正式定期报告正文、"
    "财报预约披露日、公司新闻和券商研报，"
    "由模型识别事件含义，但每个事件必须绑定工具实际取得的证据编号和明确日历时间窗；来源、日期和"
    "链接由程序回填，不能由模型编造。该工具只研究催化，不执行完整九项买入判断，也不使用网络搜索。"
)


def _source_coverage(raw: dict[str, Any]) -> dict[str, Any]:
    dimensions = {
        "announcements": {
            "count": len(raw.get("announcement_events") or []),
            "error": raw.get("announcement_events_error"),
        },
        "formal_documents": {
            "count": len(raw.get("document_events") or []),
            "error": raw.get("document_events_error"),
        },
        "report_schedule": {
            "count": len(raw.get("schedule_events") or []),
            "error": raw.get("schedule_events_error"),
        },
        "news": {
            "count": len(raw.get("news_events") or []),
            "error": raw.get("news_events_error"),
        },
        "research": {
            "count": len(raw.get("research_events") or []),
            "error": raw.get("research_events_error"),
        },
    }
    return {
        "dimensions": dimensions,
        "available_count": sum(not value["error"] for value in dimensions.values()),
        "required_count": len(dimensions),
        "complete": not any(value["error"] for value in dimensions.values()),
    }


def analyze_stock_catalysts(symbols: str) -> dict[str, Any]:
    resolved, unresolved = resolve_securities_csv(symbols)
    if len(resolved) > 8:
        return {
            "success": False,
            "partial": False,
            "items": [],
            "resolved_entities": [],
            "unresolved_entities": unresolved,
            "errors": ["催化研究单次最多8只；本次没有静默截断，请由工作流分批执行"],
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
            "errors": ["没有可通过本地A股证券库核验的公司名称或代码"],
            "warnings": [],
            "data_time": None,
            "is_stale": None,
        }

    items: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []
    for entity in resolved:
        symbol = str(entity["symbol"])
        name = str(entity.get("name") or symbol)
        try:
            _clear_cache()
            stock_info = DataService().get_stock_info(symbol)
            result = CatalystEventsEvaluator().evaluate(symbol, stock_info)
            raw = result.evidence.raw_data
            coverage = _source_coverage(raw)
            if not coverage["complete"]:
                warnings.append(f"{name}({symbol})部分催化证据源获取失败")
            items.append({
                "symbol": symbol,
                "name": name,
                "horizon": "未来6—12个月",
                "passed": result.passed,
                "verdict": result.verdict,
                "catalysts": result.details.get("catalysts") or [],
                "missing_evidence": result.details.get("missing_evidence") or [],
                "source_coverage": coverage,
                "retrieved_evidence": {
                    "announcements": raw.get("announcement_events") or [],
                    "formal_documents": raw.get("document_events") or [],
                    "report_schedule": raw.get("schedule_events") or [],
                    "news": raw.get("news_events") or [],
                    "research": raw.get("research_events") or [],
                },
                "analyzed_at": result.analyzed_at,
            })
        except Exception as exc:
            message = f"{name}({symbol})催化研究失败：{type(exc).__name__}: {str(exc)[:240]}"
            errors.append(message)
            items.append({
                "symbol": symbol,
                "name": name,
                "horizon": "未来6—12个月",
                "passed": False,
                "verdict": message,
                "catalysts": [],
                "missing_evidence": ["本轮催化研究未完成"],
                "source_coverage": {
                    "dimensions": {},
                    "available_count": 0,
                    "required_count": 5,
                    "complete": False,
                },
                "retrieved_evidence": {},
                "analyzed_at": datetime.now().astimezone().isoformat(),
            })

    if unresolved:
        errors.append("无法解析：" + "、".join(unresolved))
    data_times = [str(item.get("analyzed_at") or "") for item in items if item.get("analyzed_at")]
    return {
        "success": bool(items),
        "partial": bool(errors or warnings),
        "playbook": "evidence_bound_catalyst_research",
        "items": items,
        "resolved_entities": resolved,
        "unresolved_entities": unresolved,
        "requested_count": len(resolved) + len(unresolved),
        "covered_count": len(items),
        "coverage_complete": len(items) == len(resolved) and not unresolved,
        "horizon": "未来6—12个月",
        "decision_boundary": "催化研究不等于买入判断；估值、股价透支和买入位置需另行核验",
        "source": "内部同步公告目录、正式定期报告正文、财报预约、公司新闻、券商研报",
        "errors": errors,
        "warnings": warnings,
        "data_time": max(data_times) if data_times else None,
        "is_stale": None,
    }


TOOL = ToolSpec(
    name="analyze_stock_catalysts",
    description=DESCRIPTION,
    parameters=object_schema(
        {
            "symbols": {
                "type": "string",
                "description": "A股代码或公司名称，多个用逗号分隔，单次最多8只",
            },
        },
        ["symbols"],
    ),
    executor=analyze_stock_catalysts,
    category="analysis",
)


__all__ = ["TOOL", "analyze_stock_catalysts"]
