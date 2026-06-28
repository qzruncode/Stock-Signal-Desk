# -*- coding: utf-8 -*-
"""Report-level helpers: driver signal aggregation, detector merging, cycle phase / beneficiary inference,
streaming draft assembly, conflict detection and report usability checks."""

from __future__ import annotations

from typing import Any, Optional

from .llm_parse import (
    _extract_partial_json_number_field,
    _extract_partial_json_string_field,
)
from .normalization import (
    _DRIVER_KEYWORDS,
    _as_dict,
    _as_list,
    _contains_any,
    _first_dict,
    _list_of_dicts,
    _normalize_text,
)


def _build_driver_signals(texts: list[str]) -> dict[str, list[str]]:
    joined = " ".join(texts)
    signals: dict[str, list[str]] = {}
    for key, keywords in _DRIVER_KEYWORDS.items():
        hits = [word for word in keywords if _contains_any(joined, [word])]
        if hits:
            signals[key] = list(dict.fromkeys(hits))[:4]
    return signals


def _has_strong_driver_signals(signals: dict[str, list[str]]) -> bool:
    strong_hits = [
        hit
        for key, items in signals.items()
        for hit in items
        if not (key == "supply" and hit == "库存")
    ]
    return bool(strong_hits)


def _describe_driver_signals(signals: dict[str, list[str]]) -> str:
    if not signals:
        return "近端资讯中未聚合出足够强的产业驱动"
    label_map = {
        "policy": "政策",
        "technology": "技术",
        "demand": "需求",
        "supply": "供给",
    }
    parts = []
    for key in ("policy", "technology", "demand", "supply"):
        hits = [str(item).strip() for item in (signals.get(key) or []) if str(item).strip()]
        if hits:
            parts.append(f"{label_map.get(key, key)}：{' / '.join(hits[:3])}")
    return "；".join(parts) if parts else "近端资讯中未聚合出足够强的产业驱动"


def _format_pct_text(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}"


def _pick_failed_reason(checklist: list[dict[str, Any]]) -> str:
    for item in checklist:
        if not item.get("passed"):
            return _normalize_text(item.get("reason")) or _normalize_text(item.get("item")) or "存在未通过项"
    return ""


def _merge_detector_with_fallback(
    detector: dict[str, Any],
    fallback_detector: dict[str, Any],
) -> dict[str, Any]:
    primary_checklist = _list_of_dicts(detector.get("checklist"))
    fallback_checklist = _list_of_dicts(fallback_detector.get("checklist"))
    merged_checklist = primary_checklist or fallback_checklist
    passed = detector.get("passed")
    if not merged_checklist and passed in (None, ""):
        passed = fallback_detector.get("passed")
    elif not primary_checklist and fallback_checklist:
        passed = fallback_detector.get("passed")
    return {
        "passed": bool(passed),
        "conclusion": _normalize_text(detector.get("conclusion")) or _normalize_text(fallback_detector.get("conclusion")),
        "failed_reason": detector.get("failed_reason") or fallback_detector.get("failed_reason"),
        "checklist": merged_checklist,
    }


def _report_has_incomplete_detectors(payload: dict[str, Any]) -> bool:
    cycle = _as_dict(payload.get("industry_cycle"))
    if not cycle:
        return False
    mainline_detector = _as_dict(cycle.get("mainline_detector"))
    beta_detector = _as_dict(cycle.get("industry_beta_detector"))
    mainline_checklist = _list_of_dicts(mainline_detector.get("checklist"))
    beta_checklist = _list_of_dicts(beta_detector.get("checklist"))
    has_stream = bool(_normalize_text(payload.get("raw_stream_output")) or _normalize_text(payload.get("raw_response")))
    has_summary = bool(
        _normalize_text(cycle.get("beneficiary_reason"))
        or _normalize_text(cycle.get("core_logic"))
        or _normalize_text(cycle.get("prosperity_judgement"))
    )
    return has_stream and has_summary and (not mainline_checklist or not beta_checklist)


def _find_detector_item(detector: dict[str, Any], item_name: str) -> dict[str, Any]:
    for item in _list_of_dicts(detector.get("checklist")):
        if _normalize_text(item.get("item")) == item_name:
            return item
    return {}


def _report_has_conflicting_conclusion(payload: dict[str, Any]) -> bool:
    cycle = _as_dict(payload.get("industry_cycle"))
    if not cycle:
        return False

    analysis_status = _normalize_text(cycle.get("analysis_status"))
    beneficiary_level = _normalize_text(cycle.get("beneficiary_level"))
    mainline_detector = _as_dict(cycle.get("mainline_detector"))
    beta_detector = _as_dict(cycle.get("industry_beta_detector"))
    evidence = _as_dict(cycle.get("evidence"))
    market_mainline = _as_dict(evidence.get("market_mainline"))
    matched_current_mainlines = _list_of_dicts(market_mainline.get("matched_current_mainlines"))

    current_mapping_item = _find_detector_item(mainline_detector, "当前属于市场主线 / 分支主线")
    concept_item = _find_detector_item(mainline_detector, "不是单纯蹭概念")
    real_benefit_item = _find_detector_item(mainline_detector, "主营业务能实际受益")

    if analysis_status in {"主线", "分支主线"} and not bool(mainline_detector.get("passed")):
        return True
    if analysis_status in {"主线", "分支主线"} and market_mainline.get("report_pending") and not matched_current_mainlines:
        return True
    if beneficiary_level in {"核心受益", "直接受益"} and real_benefit_item and not bool(real_benefit_item.get("passed")):
        return True
    if beneficiary_level in {"核心受益", "直接受益"} and concept_item and not bool(concept_item.get("passed")):
        return True
    if bool(beta_detector.get("passed")) and _find_detector_item(beta_detector, "行业处于上升周期") and not bool(_find_detector_item(beta_detector, "行业处于上升周期").get("passed")):
        return True
    if current_mapping_item and not bool(current_mapping_item.get("passed")) and matched_current_mainlines:
        return True
    return False


def _is_usable_report_payload(payload: Optional[dict[str, Any]]) -> bool:
    if not payload or not isinstance(payload, dict):
        return False
    cycle = _as_dict(payload.get("industry_cycle"))
    if not cycle:
        return False
    if _report_has_incomplete_detectors(payload):
        return False
    if _report_has_conflicting_conclusion(payload):
        return False
    return True


def _build_streaming_industry_cycle_draft(
    raw_text: str,
    *,
    symbol: str,
    evidence_pack: dict[str, Any],
) -> dict[str, Any]:
    industry_name = str(evidence_pack.get("industry_name") or "")
    stock_name = str(evidence_pack.get("stock_name") or symbol)
    company_specific_evidence = _as_dict(evidence_pack.get("company_specific_evidence"))
    stock_focus_snapshot = _as_dict(company_specific_evidence.get("stock_focus_snapshot"))
    stock_focus_view = _normalize_text(stock_focus_snapshot.get("focus_view"))

    analysis_status = _extract_partial_json_string_field(raw_text, "analysis_status")
    beneficiary_level = _extract_partial_json_string_field(raw_text, "beneficiary_level")
    beneficiary_reason = _extract_partial_json_string_field(raw_text, "beneficiary_reason")
    cycle_phase = _extract_partial_json_string_field(raw_text, "cycle_phase")
    cycle_phase_reason = _extract_partial_json_string_field(raw_text, "cycle_phase_reason")
    prosperity_judgement = _extract_partial_json_string_field(raw_text, "prosperity_judgement")
    core_logic = _extract_partial_json_string_field(raw_text, "core_logic")
    killer_reason = _extract_partial_json_string_field(raw_text, "killer_reason")
    observation_window = _extract_partial_json_string_field(raw_text, "observation_window")
    prosperity_score = _extract_partial_json_number_field(raw_text, "prosperity_score")

    if not any((
        analysis_status,
        beneficiary_level,
        beneficiary_reason,
        cycle_phase,
        cycle_phase_reason,
        prosperity_judgement,
        core_logic,
        killer_reason,
        observation_window,
        prosperity_score is not None,
        stock_focus_view,
    )):
        return {}

    mainline_context = _as_dict(evidence_pack.get("mainline_context"))
    industry_beta_evidence = _as_dict(evidence_pack.get("industry_beta_evidence"))
    supporting_judgement = _as_dict(evidence_pack.get("supporting_judgement"))

    return {
        "symbol": symbol,
        "industry_cycle": {
            "stock_name": stock_name,
            "industry_name": industry_name,
            "analysis_status": analysis_status or "观察",
            "beneficiary_level": beneficiary_level or None,
            "beneficiary_reason": beneficiary_reason or stock_focus_view,
            "cycle_phase": cycle_phase or None,
            "cycle_phase_reason": cycle_phase_reason or "",
            "prosperity_score": int(round(prosperity_score)) if prosperity_score is not None else 0,
            "prosperity_judgement": prosperity_judgement or "",
            "core_logic": core_logic or stock_focus_view,
            "killer_reason": killer_reason or None,
            "observation_window": observation_window or "未来 6-12 个月",
            "catalysts": [],
            "risks": [],
            "observation_points": [],
            "mainline_detector": {
                "passed": False,
                "conclusion": "",
                "failed_reason": None,
                "checklist": [],
            },
            "industry_beta_detector": {
                "passed": False,
                "conclusion": "",
                "failed_reason": None,
                "checklist": [],
            },
            "evidence": {
                "stock_focus_snapshot": stock_focus_snapshot,
                "market_mainline": {
                    "report_pending": bool(mainline_context.get("report_pending")),
                    "market_stage": _as_dict(mainline_context.get("market_stage")),
                    "report_current_mainlines": _list_of_dicts(mainline_context.get("current_mainlines")),
                    "report_future_mainlines": _list_of_dicts(mainline_context.get("future_mainlines")),
                    "matched_current_mainlines": [],
                    "matched_future_mainlines": [],
                    "current_theme_detail": _first_dict(mainline_context.get("current_theme_evidence")),
                    "future_theme_detail": _first_dict(mainline_context.get("future_theme_evidence")),
                },
                "sector_snapshot": _as_dict(industry_beta_evidence.get("sector_snapshot")),
                "fund_flow": _as_dict(industry_beta_evidence.get("fund_flow_snapshot")),
                "peer_group": _as_dict(industry_beta_evidence.get("peer_snapshot")),
                "financial_snapshot": _as_dict(company_specific_evidence.get("financial_snapshot")),
                "valuation_snapshot": {},
                "sentiment_snapshot": _as_dict(supporting_judgement.get("coverage_snapshot")),
                "risk_snapshot": _as_dict(supporting_judgement.get("risk_snapshot")),
                "data_quality": _as_dict(supporting_judgement.get("data_quality")),
                "driver_signals": _as_dict(supporting_judgement.get("driver_clues")),
            },
        },
        "report_pending": True,
        "llm_used": True,
        "raw_stream_output": raw_text,
        "raw_response": raw_text,
        "as_of_date": as_of_date,
        "debug_input": {
            "system_prompt": None,
            "user_prompt": None,
            "evidence_pack": evidence_pack,
        },
        "_cached": False,
    }


def _infer_beneficiary_level(
    *,
    matched_current: Optional[dict[str, Any]],
    matched_future: Optional[dict[str, Any]],
) -> tuple[str, str]:
    if matched_current or matched_future:
        return "待验证", "本地不再用关键词判断主营受益路径，需要由模型结合主营、产品、公告、研报和市场主线报告综合确认。"
    return "待验证", "本地不再用关键词做主线映射，尚未建立模型确认后的主营受益路径。"


def _infer_cycle_phase(
    *,
    analysis_status: str,
    current_theme_detail: Optional[dict[str, Any]],
    matched_current: Optional[dict[str, Any]],
    sector_change: Optional[float],
    flow_amount: Optional[float],
    news_count: int,
) -> tuple[str, str]:
    detail_stage = _normalize_text((current_theme_detail or {}).get("stage"))
    detail_reason = _normalize_text((current_theme_detail or {}).get("stage_reason"))
    if detail_stage:
        return detail_stage, detail_reason or f"当前主线阶段显示为 {detail_stage}。"

    report_stage = _normalize_text((matched_current or {}).get("stage"))
    if report_stage:
        return report_stage, f"市场主线报告把该方向归为 {report_stage}。"

    if analysis_status == "退潮":
        return "退潮期", "行业景气和资金反馈同时转弱，更接近退潮而非分歧强化。"
    if sector_change is not None and sector_change >= 4 and flow_amount is not None and flow_amount > 0:
        return "加速期", "板块涨幅和资金净流入形成共振，属于一致性最强阶段。"
    if sector_change is not None and sector_change > 0 and news_count >= 4:
        return "发酵期", "行业已经开始扩散，资金和消息面都在持续强化。"
    if sector_change is not None and sector_change < 0:
        return "分歧期", "产业逻辑仍在，但价格反馈转弱，资金开始分化。"
    return "观察期", "行业还在积累证据，尚未进入明确的上行加速阶段。"
