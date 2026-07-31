# -*- coding: utf-8 -*-
"""Data quality scoring, evidence gate assessment and the insufficient-evidence report builder."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from .cache import _current_report_as_of_date
from .normalization import (
    _as_dict,
    _as_list,
    _first_dict,
    _list_of_dicts,
    _normalize_text,
    _safe_int,
)


def _build_data_quality(
    *,
    board_rank: Optional[int],
    flow_rank: Optional[int],
    research_count: int,
    discussion_count: int,
    peer_sample_size: Optional[int],
    board_source_ok: bool = True,
    peer_source_ok: bool = True,
    financial_statements_available: bool = True,
    shareholder_available: bool = True,
    trading_snapshot_available: bool = True,
) -> dict[str, Any]:
    missing_fields: list[str] = []
    if board_rank is None:
        missing_fields.append("未找到可靠行业板块映射" if board_source_ok else "行业板块数据源暂时不可用")
    if flow_rank is None:
        missing_fields.append("未找到可靠行业资金流映射")
    if research_count <= 0:
        missing_fields.append("券商研报覆盖不足")
    if discussion_count <= 0:
        missing_fields.append("社交讨论覆盖不足")
    if peer_sample_size in (0, None):
        missing_fields.append("同行样本不足" if peer_source_ok else "同行样本数据源暂时不可用")
    if not financial_statements_available:
        missing_fields.append("三大财报明细覆盖不足")
    if not shareholder_available:
        missing_fields.append("股东结构覆盖不足")
    if not trading_snapshot_available:
        missing_fields.append("个股交易快照覆盖不足")
    return {
        "missing_fields": missing_fields,
        "summary": "；".join(missing_fields) if missing_fields else "当前行业映射证据完整度正常",
    }


def _assess_evidence_gate(evidence_pack: dict[str, Any]) -> dict[str, Any]:
    stock_profile = _as_dict(evidence_pack.get("stock_profile"))
    company_specific = _as_dict(evidence_pack.get("company_specific_evidence"))
    industry_beta = _as_dict(evidence_pack.get("industry_beta_evidence"))
    supporting = _as_dict(evidence_pack.get("supporting_judgement"))
    sector_snapshot = _as_dict(industry_beta.get("sector_snapshot"))
    fund_flow_snapshot = _as_dict(industry_beta.get("fund_flow_snapshot"))
    source_health = _as_dict(supporting.get("source_health"))
    board_available = any(
        (
            bool(source_health.get("ths_industry_summary_ok")),
            bool(source_health.get("sector_board_available")),
            sector_snapshot.get("rank") is not None,
        )
    )
    peer_available = bool(source_health.get("peer_source_ok")) or _safe_int(
        _as_dict(industry_beta.get("peer_snapshot")).get("sample_size")
    ) not in (0, None)
    direct_evidence_count = (
        len(_as_list(company_specific.get("announcements")))
        + len(_as_list(company_specific.get("news")))
        + len(_as_list(company_specific.get("research")))
    )

    blocking_reasons: list[str] = []
    warnings: list[str] = []

    if not _normalize_text(stock_profile.get("industry")):
        blocking_reasons.append("公司所属行业缺失")
    if not _normalize_text(stock_profile.get("main_business")):
        blocking_reasons.append("公司主营业务缺失")
    if not board_available and fund_flow_snapshot.get("rank") is None:
        blocking_reasons.append("行业板块强度与资金流排名都缺失")
    if not _as_list(company_specific.get("announcements")) and not _as_list(company_specific.get("news")):
        blocking_reasons.append("公司直接事件证据不足，公告和新闻同时缺失")
    if direct_evidence_count < 3:
        warnings.append(f"公司直接证据偏少，当前仅 {direct_evidence_count} 条样本")

    if not bool(source_health.get("stock_info_cninfo_ok")):
        if bool(source_health.get("stock_info_ths_business_ok")):
            warnings.append("巨潮公司资料源未取到，但主营已由 THS 补齐")
        else:
            warnings.append("巨潮公司资料源未取到")
    if not board_available:
        warnings.append("行业板块源不可用")
    if not peer_available:
        warnings.append("同行样本源不可用")
    if not bool(source_health.get("social_available")):
        warnings.append("社交情绪源不可用或无覆盖")
    if not bool(source_health.get("news_available")):
        warnings.append("新闻源不可用或无覆盖")
    if not bool(source_health.get("announcements_available")):
        warnings.append("公告源不可用或无覆盖")
    if not bool(source_health.get("research_available")):
        warnings.append("研报源不可用或无覆盖")
    if not bool(source_health.get("financial_statements_available")):
        warnings.append("三大财报明细源不可用或无覆盖")
    if not bool(source_health.get("shareholder_available")):
        warnings.append("股东结构源不可用或无覆盖")
    if not bool(source_health.get("trading_snapshot_available")):
        warnings.append("个股交易快照源不可用或无覆盖")

    return {
        "passed": not blocking_reasons,
        "blocking_reasons": blocking_reasons,
        "warnings": warnings,
    }


def _build_evidence_insufficient_report(
    *,
    symbol: str,
    evidence_pack: dict[str, Any],
    gate: dict[str, Any],
    system_prompt: str,
    user_prompt: str,
) -> dict[str, Any]:
    stock_name = str(evidence_pack.get("stock_name") or symbol)
    as_of_date = _current_report_as_of_date()
    industry_name = str(evidence_pack.get("industry_name") or "")
    supporting = _as_dict(evidence_pack.get("supporting_judgement"))
    quality = _as_dict(supporting.get("data_quality"))
    source_health = _as_dict(supporting.get("source_health"))
    reasons = [*(_as_list(gate.get("blocking_reasons"))), *(_as_list(gate.get("warnings")))]
    reason_text = "；".join(str(item) for item in reasons if str(item).strip()) or "核心证据未达标"
    return {
        "symbol": symbol,
        "industry_cycle": {
            "stock_name": stock_name,
            "industry_name": industry_name,
            "analysis_status": "观察",
            "beneficiary_level": "待验证",
            "beneficiary_reason": "核心证据不足，暂不进入模型分析。",
            "cycle_phase": "观察期",
            "cycle_phase_reason": "行业周期分析依赖的关键数据未收齐。",
            "prosperity_score": 0,
            "prosperity_judgement": "当前证据包未达到可研判标准，本次不输出行业周期结论。",
            "core_logic": "必须先补齐公司主营、行业映射、板块强度/资金流、公司直接事件等核心证据，再进入模型分析。",
            "killer_reason": reason_text,
            "observation_window": "等待核心证据补齐后重新生成",
            "catalysts": [],
            "risks": ["证据不足导致结论失真风险高"],
            "observation_points": [str(item) for item in reasons if str(item).strip()],
            "mainline_detector": {"passed": False, "conclusion": "", "failed_reason": reason_text, "checklist": []},
            "industry_beta_detector": {
                "passed": False,
                "conclusion": "",
                "failed_reason": reason_text,
                "checklist": [],
            },
            "evidence": {
                "stock_focus_snapshot": _as_dict(
                    _as_dict(evidence_pack.get("company_specific_evidence")).get("stock_focus_snapshot")
                ),
                "market_mainline": {
                    "report_pending": bool(_as_dict(evidence_pack.get("mainline_context")).get("report_pending")),
                    "market_stage": _as_dict(_as_dict(evidence_pack.get("mainline_context")).get("market_stage")),
                    "report_current_mainlines": _list_of_dicts(
                        _as_dict(evidence_pack.get("mainline_context")).get("current_mainlines")
                    ),
                    "report_future_mainlines": _list_of_dicts(
                        _as_dict(evidence_pack.get("mainline_context")).get("future_mainlines")
                    ),
                    "matched_current_mainlines": [],
                    "matched_future_mainlines": [],
                    "current_theme_detail": _first_dict(
                        _as_dict(evidence_pack.get("mainline_context")).get("current_theme_evidence")
                    ),
                    "future_theme_detail": _first_dict(
                        _as_dict(evidence_pack.get("mainline_context")).get("future_theme_evidence")
                    ),
                },
                "sector_snapshot": _as_dict(
                    _as_dict(evidence_pack.get("industry_beta_evidence")).get("sector_snapshot")
                ),
                "fund_flow": _as_dict(_as_dict(evidence_pack.get("industry_beta_evidence")).get("fund_flow_snapshot")),
                "peer_group": _as_dict(_as_dict(evidence_pack.get("industry_beta_evidence")).get("peer_snapshot")),
                "financial_snapshot": _as_dict(
                    _as_dict(evidence_pack.get("company_specific_evidence")).get("financial_snapshot")
                ),
                "valuation_snapshot": _as_dict(evidence_pack.get("valuation_snapshot")),
                "sentiment_snapshot": _as_dict(supporting.get("coverage_snapshot")),
                "risk_snapshot": _as_dict(supporting.get("risk_snapshot")),
                "data_quality": {
                    **quality,
                    "evidence_gate": gate,
                    "source_health": source_health,
                },
                "driver_signals": _as_dict(supporting.get("driver_clues")),
            },
        },
        "report_pending": False,
        "llm_used": False,
        "model_used": None,
        "raw_stream_output": "【证据闸门未通过】\n" + reason_text,
        "raw_response": "【证据闸门未通过】\n" + reason_text,
        "debug_input": {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "evidence_pack": evidence_pack,
        },
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
        "fallback_used": True,
    }
