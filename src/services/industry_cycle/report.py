# -*- coding: utf-8 -*-
"""Streaming draft assembly and structural report consistency checks."""

from __future__ import annotations

from typing import Any, Optional

from .llm_parse import (
    _extract_partial_json_number_field,
    _extract_partial_json_string_field,
)
from .normalization import (
    _as_dict,
    _as_list,
    _first_dict,
    _list_of_dicts,
    _normalize_text,
)


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


def _is_usable_report_payload(payload: Optional[dict[str, Any]]) -> bool:
    if not payload or not isinstance(payload, dict):
        return False
    cycle = _as_dict(payload.get("industry_cycle"))
    if not cycle:
        return False
    if _report_has_incomplete_detectors(payload):
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

    if not any(
        (
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
        )
    ):
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
        "as_of_date": evidence_pack.get("as_of_date"),
        "debug_input": {
            "system_prompt": None,
            "user_prompt": None,
            "evidence_pack": evidence_pack,
        },
        "_cached": False,
    }
