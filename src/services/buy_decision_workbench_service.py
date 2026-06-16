# -*- coding: utf-8 -*-
"""Step-based buy decision workbench service."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime
import threading
import uuid
from typing import Any

from api.v1.endpoints.financials import (
    get_price_overdraft_signal,
    get_risk_events,
    get_sentiment,
    get_shareholder_structure,
    get_social_sentiment,
    get_valuation_ratios,
)
from api.v1.endpoints.macro import _fetch_sector_flow_industry
from api.v1.endpoints.sectors import get_sector_list
from api.v1.endpoints.stock_info import get_stock_info
from src.services.industry_cycle_service import IndustryCycleService

STEP_ORDER = (
    "profile_mapping",
    "industry_beta",
    "mainline_position",
    "company_benefit",
    "buy_constraints",
    "final_decision",
)

_SESSIONS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now().isoformat()


def _normalize_symbol(symbol: str) -> str:
    code = (symbol or "").strip().upper()
    if "." in code:
        code = code.split(".", 1)[0]
    for prefix in ("SH", "SZ", "BJ"):
        if code.startswith(prefix):
            return code[2:]
    return code


def _empty_step_state() -> dict[str, Any]:
    return {
        "status": "idle",
        "from_cache": False,
        "started_at": None,
        "finished_at": None,
        "summary": None,
        "error": None,
        "blocking": False,
        "next_step_enabled": False,
        "data": None,
    }


def _build_stock_info_snapshot(symbol: str) -> dict[str, Any]:
    data = get_stock_info(symbol=symbol, force=False)
    return data if isinstance(data, dict) else {}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _normalize_risk_level(value: Any, *, default: str = "medium") -> str:
    level = str(value or "").strip().lower()
    if level in {"low", "medium", "high"}:
        return level
    if level in {"watch", "uncertain"}:
        return "medium"
    return default


def _coerce_number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _pick_first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, dict)) and not value:
            continue
        return value
    return None


def _stringify_reason(value: Any, default: str) -> str:
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return "；".join(parts) if parts else default
    text = str(value or "").strip()
    return text or default


def _compact_text(value: Any) -> str:
    return "".join(str(value or "").strip().lower().split())


def _match_named_item(industry_name: str, items: list[dict[str, Any]]) -> tuple[dict[str, Any], int | None, int | None]:
    target = _compact_text(industry_name)
    if not target or not items:
        return {}, None, len(items) if items else None

    total = len(items)
    for index, item in enumerate(items, start=1):
        name = _compact_text(item.get("name"))
        if not name:
            continue
        if name == target or target in name or name in target:
            return item, index, total
    return {}, None, total


class BuyDecisionWorkbenchService:
    """Keeps a lightweight in-process step session for buy-decision flows."""

    def create_session(self, symbol: str, *, force_reset: bool = False) -> dict[str, Any]:
        code = _normalize_symbol(symbol)
        stock_info = _build_stock_info_snapshot(code)
        session_id = f"bd_{code.lower()}_{uuid.uuid4().hex[:12]}"
        session = {
            "session_id": session_id,
            "symbol": code,
            "stock_name": str(stock_info.get("short_name") or stock_info.get("name") or code),
            "industry_name": str(stock_info.get("industry") or ""),
            "current_step": STEP_ORDER[0],
            "steps": {step: _empty_step_state() for step in STEP_ORDER},
            "evidence": {},
            "report": None,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        with _LOCK:
            if force_reset:
                for existing_id, existing in list(_SESSIONS.items()):
                    if existing.get("symbol") == code:
                        _SESSIONS.pop(existing_id, None)
            _SESSIONS[session_id] = session
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict[str, Any]:
        with _LOCK:
            session = _SESSIONS.get(session_id)
            if not session:
                raise KeyError(session_id)
            return deepcopy(self._public_session(session))

    def run_step(self, session_id: str, step_key: str, *, force: bool = False) -> dict[str, Any]:
        with _LOCK:
            session = _SESSIONS.get(session_id)
            if not session:
                raise KeyError(session_id)
            if step_key not in STEP_ORDER:
                raise ValueError(f"unsupported step: {step_key}")
            self._ensure_step_prerequisites(session, step_key)
            step_state = session["steps"][step_key]
            step_state.update({
                "status": "running",
                "started_at": _now_iso(),
                "error": None,
            })

        result = self._execute_step(session_id, step_key, force=force)

        with _LOCK:
            session = _SESSIONS[session_id]
            step_state = session["steps"][step_key]
            step_state.update(result)
            step_state["status"] = "success"
            step_state["finished_at"] = _now_iso()
            session["updated_at"] = _now_iso()
            current_index = STEP_ORDER.index(step_key)
            if current_index + 1 < len(STEP_ORDER):
                next_key = STEP_ORDER[current_index + 1]
                step_state["next_step_enabled"] = True
                session["current_step"] = next_key
            else:
                session["current_step"] = step_key
            if step_key == "final_decision":
                session["report"] = result["data"]
            return deepcopy({
                "session_id": session_id,
                "step": step_key,
                **step_state,
            })

    def get_report(self, session_id: str) -> dict[str, Any]:
        with _LOCK:
            session = _SESSIONS.get(session_id)
            if not session:
                raise KeyError(session_id)
            report = session.get("report")
            if not report:
                raise ValueError("final_decision_not_ready")
            return deepcopy({
                "session_id": session_id,
                "symbol": session["symbol"],
                **report,
            })

    def _public_session(self, session: dict[str, Any]) -> dict[str, Any]:
        return {
            "session_id": session["session_id"],
            "symbol": session["symbol"],
            "stock_name": session["stock_name"],
            "industry_name": session["industry_name"],
            "current_step": session["current_step"],
            "steps": deepcopy(session["steps"]),
        }

    def _ensure_step_prerequisites(self, session: dict[str, Any], step_key: str) -> None:
        step_index = STEP_ORDER.index(step_key)
        for previous_step in STEP_ORDER[:step_index]:
            if session["steps"][previous_step]["status"] != "success":
                raise ValueError(f"previous step not completed: {previous_step}")

    def _fetch_auxiliary_snapshots(self, symbol: str, *, force: bool) -> dict[str, Any]:
        return {
            "valuation": _as_dict(get_valuation_ratios(symbol=symbol, with_history=True, force=force)),
            "price_overdraft": _as_dict(get_price_overdraft_signal(symbol=symbol, force=force)),
            "shareholder": _as_dict(get_shareholder_structure(symbol=symbol, force=force)),
            "sentiment": _as_dict(get_sentiment(symbol=symbol, days=90, force=force)),
            "social": _as_dict(get_social_sentiment(symbol=symbol, days=90, force=force)),
            "risk_events": _as_dict(get_risk_events(symbol=symbol, days=90, force=force)),
        }

    def _get_auxiliary_snapshots(self, session_id: str, symbol: str, *, force: bool) -> dict[str, Any]:
        with _LOCK:
            existing = _SESSIONS[session_id]["evidence"].get("_auxiliary_snapshots")
        if isinstance(existing, dict) and not force:
            return existing
        payload = self._fetch_auxiliary_snapshots(symbol, force=force)
        with _LOCK:
            _SESSIONS[session_id]["evidence"]["_auxiliary_snapshots"] = payload
        return payload

    def _collect_industry_beta_evidence(
        self,
        *,
        session_id: str,
        symbol: str,
        industry_name: str,
        legacy_cycle: dict[str, Any],
        legacy_evidence: dict[str, Any],
        force: bool,
    ) -> dict[str, Any]:
        auxiliary = self._get_auxiliary_snapshots(session_id, symbol, force=force)
        valuation_data = _as_dict(auxiliary.get("valuation"))
        risk_data = _as_dict(auxiliary.get("risk_events"))
        sector_items = _as_list(_as_dict(get_sector_list(type="industry", force=force)).get("items"))
        flow_items = _as_list(_fetch_sector_flow_industry())
        live_sector_item, live_sector_rank, live_sector_total = _match_named_item(industry_name, sector_items)
        live_flow_item, live_flow_rank, live_flow_total = _match_named_item(industry_name, flow_items)

        legacy_sector = _as_dict(legacy_evidence.get("sector_snapshot"))
        legacy_flow = _as_dict(legacy_evidence.get("fund_flow"))
        sector_source_hint = (
            "sector_list_industry"
            if live_sector_item
            else "sector_flow_industry"
            if live_flow_item
            else legacy_sector.get("source")
        )
        sector_snapshot = {
            "name": _pick_first_non_empty(live_sector_item.get("name"), legacy_sector.get("name"), industry_name),
            "change_pct": _pick_first_non_empty(
                live_sector_item.get("change_pct"),
                live_flow_item.get("pct_chg"),
                legacy_sector.get("change_pct"),
            ),
            "rank": _pick_first_non_empty(live_sector_rank, live_flow_rank, legacy_sector.get("rank")),
            "total": _pick_first_non_empty(live_sector_total, live_flow_total, legacy_sector.get("total")),
            "total_amount": _pick_first_non_empty(
                live_sector_item.get("total_amount"),
                live_flow_item.get("total_amount"),
                legacy_sector.get("total_amount"),
            ),
            "leading_stock": _pick_first_non_empty(
                live_sector_item.get("lead_stock"),
                live_flow_item.get("leading_stock"),
                legacy_sector.get("leading_stock"),
            ),
            "source": _pick_first_non_empty(live_sector_item.get("source"), sector_source_hint),
        }
        fund_flow_snapshot = {
            "net_inflow": _pick_first_non_empty(
                live_flow_item.get("main_net_inflow"),
                live_flow_item.get("net_inflow"),
                live_flow_item.get("net_flow"),
                legacy_flow.get("main_net_inflow"),
                legacy_flow.get("net_inflow"),
                legacy_flow.get("net_flow"),
            ),
            "rank": _pick_first_non_empty(live_flow_rank, legacy_flow.get("rank")),
            "total": _pick_first_non_empty(live_flow_total, legacy_flow.get("total")),
            "pct_chg": _pick_first_non_empty(live_flow_item.get("pct_chg"), legacy_flow.get("pct_chg"), sector_snapshot.get("change_pct")),
            "total_amount": _pick_first_non_empty(live_flow_item.get("total_amount"), legacy_flow.get("total_amount")),
            "leading_stock": _pick_first_non_empty(live_flow_item.get("leading_stock"), legacy_flow.get("leading_stock")),
            "source": _pick_first_non_empty(live_flow_item.get("source"), "sector_flow_industry" if live_flow_item else None, legacy_flow.get("source")),
        }
        legacy_peer = _as_dict(legacy_evidence.get("peer_group"))
        legacy_risk = _as_dict(legacy_evidence.get("risk_snapshot"))
        legacy_drivers = _as_dict(legacy_evidence.get("driver_signals"))
        legacy_quality = _as_dict(legacy_evidence.get("data_quality"))
        beta_detector = _as_dict(legacy_cycle.get("industry_beta_detector"))
        analysis = _as_dict(risk_data.get("analysis"))
        severity = _as_dict(analysis.get("severity_distribution"))
        has_sector_snapshot = (
            sector_snapshot.get("rank") is not None
            or sector_snapshot.get("change_pct") is not None
            or sector_snapshot.get("total_amount") is not None
        )
        has_fund_flow = (
            fund_flow_snapshot.get("rank") is not None
            or fund_flow_snapshot.get("net_inflow") is not None
            or fund_flow_snapshot.get("total_amount") is not None
        )
        sector_source = _pick_first_non_empty(
            sector_snapshot.get("source"),
            "行业周期服务" if has_sector_snapshot else None,
        )
        fund_flow_source = _pick_first_non_empty(
            fund_flow_snapshot.get("source"),
            "行业资金流服务" if has_fund_flow else None,
        )
        peer_sample_size = _pick_first_non_empty(legacy_peer.get("sample_size"), 0)
        missing_fields: list[str] = []
        if sector_snapshot.get("rank") is None:
            missing_fields.append("sector_rank")
        if sector_snapshot.get("change_pct") is None:
            missing_fields.append("sector_change_pct")
        if fund_flow_snapshot.get("net_inflow") is None:
            missing_fields.append("fund_flow_net_inflow")
        if fund_flow_snapshot.get("total_amount") is None:
            missing_fields.append("fund_flow_total_amount")
        if not int(peer_sample_size or 0) > 0:
            missing_fields.append("peer_sample")
        if not legacy_drivers:
            missing_fields.append("driver_signals")

        return {
            "industry_beta_evidence": {
                "sector_snapshot": {**sector_snapshot, "source": sector_source},
                "fund_flow": {**fund_flow_snapshot, "source": fund_flow_source},
                "peer_group": legacy_peer or {"sample_size": 0, "sample_names": []},
                "industry_space": {
                    "space_level": "clear" if beta_detector.get("passed") else "uncertain",
                    "reason": _stringify_reason(_pick_first_non_empty(
                        beta_detector.get("conclusion"),
                        _as_list(_as_dict(valuation_data.get("price_overdraft_signal")).get("reasoning")),
                        "行业中期空间仍待验证。",
                    ), "行业中期空间仍待验证。"),
                },
                "price_war_signal": {
                    "level": "high" if int(_pick_first_non_empty(severity.get("high"), legacy_risk.get("high_risk_count"), 0) or 0) > 0 else "low",
                    "reason": beta_detector.get("failed_reason") or "未发现明显价格战信号。",
                },
                "driver_signals": legacy_drivers,
                "prosperity_snapshot": {
                    "score": legacy_cycle.get("prosperity_score"),
                    "judgement": legacy_cycle.get("prosperity_judgement"),
                    "analysis_status": legacy_cycle.get("analysis_status"),
                    "source": "industry_cycle_report",
                },
            },
            "industry_beta_detector": {
                "passed": bool(beta_detector.get("passed")),
                "conclusion": str(beta_detector.get("conclusion") or "行业 beta 待进一步确认"),
                "failed_reason": beta_detector.get("failed_reason"),
                "checklist": _as_list(beta_detector.get("checklist")),
            },
            "data_quality": {
                "has_sector_snapshot": has_sector_snapshot,
                "has_fund_flow": has_fund_flow,
                "has_peer_group": bool(legacy_peer) and int(peer_sample_size or 0) > 0,
                "has_driver_signals": bool(legacy_drivers),
                "sector_source": sector_source,
                "fund_flow_source": fund_flow_source,
                "missing_fields": missing_fields,
                "summary": (
                    "行业景气、空间、价格战与驱动四组证据已整理完成"
                    if not missing_fields
                    else "行业 beta 证据已收集，但部分字段仍缺失"
                ),
                "legacy_summary": legacy_quality.get("summary"),
            },
        }

    def _collect_mainline_position_evidence(
        self,
        *,
        session_id: str,
        symbol: str,
        industry_name: str,
        legacy_cycle: dict[str, Any],
        legacy_evidence: dict[str, Any],
        force: bool,
    ) -> dict[str, Any]:
        auxiliary = self._get_auxiliary_snapshots(session_id, symbol, force=force)
        sentiment_data = _as_dict(auxiliary.get("sentiment"))
        social_data = _as_dict(auxiliary.get("social"))
        market_mainline = _as_dict(legacy_evidence.get("market_mainline"))
        mainline_detector = _as_dict(legacy_cycle.get("mainline_detector"))
        matched_current = _as_list(market_mainline.get("matched_current_mainlines"))
        matched_future = _as_list(market_mainline.get("matched_future_mainlines"))
        discussion_count = _pick_first_non_empty(
            social_data.get("total_discussion"),
            len(_as_list(sentiment_data.get("items"))),
            len(matched_current) + len(matched_future),
        )
        heat_level = (
            "high"
            if matched_current or (_coerce_number(social_data.get("overall_score")) or 0) >= 65
            else "medium"
            if matched_future or (_coerce_number(sentiment_data.get("sentiment_score")) or 0) >= 55
            else "low"
        )
        matched_theme = (
            _as_dict(matched_current[0]).get("name")
            if matched_current
            else _as_dict(matched_future[0]).get("name")
            if matched_future
            else industry_name or "行业映射"
        )
        return {
            "mainline_evidence": {
                "market_mainline": {
                    "current_status": legacy_cycle.get("analysis_status") or "观察",
                    "matched_theme": matched_theme,
                },
                "theme_matches": [str(_as_dict(item).get("name")) for item in matched_current if _as_dict(item).get("name")] or ([industry_name] if industry_name else []),
                "heat_snapshot": {
                    "heat_level": heat_level,
                    "discussion_count": discussion_count,
                },
                "concept_purity": {
                    "level": "high" if legacy_cycle.get("beneficiary_level") in {"核心受益", "直接受益"} else "medium",
                    "reason": legacy_cycle.get("beneficiary_reason") or "主营与主题映射仍需进一步验证。",
                },
                "driver_signals": _as_dict(legacy_evidence.get("driver_signals")),
                "catalyst_snapshot": {
                    "count": len(_as_list(legacy_cycle.get("catalysts"))),
                    "items": _as_list(legacy_cycle.get("catalysts")),
                },
            },
            "mainline_detector": {
                "passed": bool(mainline_detector.get("passed")),
                "conclusion": str(mainline_detector.get("conclusion") or "主线属性待进一步确认"),
                "failed_reason": mainline_detector.get("failed_reason"),
                "checklist": _as_list(mainline_detector.get("checklist")),
            },
        }

    def _collect_company_benefit_evidence(
        self,
        *,
        session_id: str,
        symbol: str,
        stock_name: str,
        industry_name: str,
        legacy_cycle: dict[str, Any],
        legacy_evidence: dict[str, Any],
        force: bool,
    ) -> dict[str, Any]:
        auxiliary = self._get_auxiliary_snapshots(session_id, symbol, force=force)
        valuation_data = _as_dict(auxiliary.get("valuation"))
        overdraft_data = _as_dict(auxiliary.get("price_overdraft"))
        shareholder_data = _as_dict(auxiliary.get("shareholder"))
        sentiment_data = _as_dict(auxiliary.get("sentiment"))
        social_data = _as_dict(auxiliary.get("social"))
        risk_data = _as_dict(auxiliary.get("risk_events"))
        legacy_financial = _as_dict(legacy_evidence.get("financial_snapshot"))
        legacy_valuation = _as_dict(legacy_evidence.get("valuation_snapshot"))
        legacy_focus = _as_dict(legacy_evidence.get("stock_focus_snapshot"))
        legacy_sentiment = _as_dict(legacy_evidence.get("sentiment_snapshot"))
        legacy_risk = _as_dict(legacy_evidence.get("risk_snapshot"))
        overdraft_signal = _as_dict(overdraft_data.get("price_overdraft_signal"))
        valuation_signal = _as_dict(valuation_data.get("price_overdraft_signal"))
        analysis = _as_dict(risk_data.get("analysis"))
        severity = _as_dict(analysis.get("severity_distribution"))

        valuation_snapshot = {
            "pe_ttm": _pick_first_non_empty(valuation_data.get("pe_ttm"), legacy_valuation.get("pe_ttm")),
            "pb": _pick_first_non_empty(valuation_data.get("pb"), legacy_valuation.get("pb")),
            "industry_pe": _pick_first_non_empty(_as_dict(valuation_data.get("industry_average")).get("pe"), legacy_valuation.get("industry_pe")),
            "industry_pb": _pick_first_non_empty(_as_dict(valuation_data.get("industry_average")).get("pb"), legacy_valuation.get("industry_pb")),
            "price_overdraft_status": _pick_first_non_empty(
                overdraft_signal.get("status"),
                valuation_signal.get("status"),
                legacy_valuation.get("price_overdraft_status"),
                legacy_valuation.get("valuation_status"),
            ),
            "price_overdraft_score": _pick_first_non_empty(
                overdraft_signal.get("score"),
                valuation_signal.get("score"),
                legacy_valuation.get("price_overdraft_score"),
            ),
            "reasoning": _pick_first_non_empty(
                _as_list(overdraft_signal.get("reasoning")),
                _as_list(valuation_signal.get("reasoning")),
                _as_list(legacy_valuation.get("reasoning")),
            ) or [],
        }

        sentiment_snapshot = {
            "sentiment_score": _pick_first_non_empty(sentiment_data.get("sentiment_score"), legacy_sentiment.get("sentiment_score")),
            "social_score": _pick_first_non_empty(social_data.get("overall_score"), legacy_sentiment.get("social_score")),
            "news_count": _pick_first_non_empty(
                len(_as_list(sentiment_data.get("items"))) if sentiment_data.get("items") is not None else None,
                legacy_sentiment.get("news_count"),
            ),
            "research_count": _pick_first_non_empty(len(_as_list(sentiment_data.get("research_items"))), legacy_sentiment.get("research_count")),
            "positive_research_count": _pick_first_non_empty(legacy_sentiment.get("positive_research_count"), _as_dict(legacy_evidence.get("sentiment_snapshot")).get("positive_research_count")),
            "discussion_count": _pick_first_non_empty(social_data.get("total_discussion"), legacy_sentiment.get("discussion_count")),
        }

        risk_snapshot = {
            "high_risk_count": _pick_first_non_empty(severity.get("high"), legacy_risk.get("high_risk_count"), 0),
            "medium_risk_count": _pick_first_non_empty(severity.get("medium"), legacy_risk.get("medium_risk_count"), 0),
            "top_risk_labels": _pick_first_non_empty(analysis.get("top_risk_labels"), legacy_risk.get("top_risk_labels")) or [],
        }

        holder_pct = _pick_first_non_empty(shareholder_data.get("institution_holding_pct"), legacy_focus.get("institution_holding_pct"))
        stock_focus_snapshot = {
            "focus_view": _pick_first_non_empty(
                legacy_focus.get("focus_view"),
                legacy_cycle.get("beneficiary_reason"),
                f"{stock_name} 与 {industry_name} 逻辑存在直接关联",
            ),
            "business_binding_strength": _pick_first_non_empty(legacy_focus.get("business_binding_strength"), "unknown"),
            "finance_state": _pick_first_non_empty(legacy_focus.get("finance_state"), "unknown"),
            "holder_state": _pick_first_non_empty(legacy_focus.get("holder_state"), "unknown"),
            "trading_state": _pick_first_non_empty(legacy_focus.get("trading_state"), "unknown"),
            "direct_evidence_strength": _pick_first_non_empty(legacy_focus.get("direct_evidence_strength"), "unknown"),
            "finance_points": _pick_first_non_empty(legacy_focus.get("finance_points"), []) or [],
            "holder_points": _pick_first_non_empty(legacy_focus.get("holder_points"), []) or [],
            "trading_points": _pick_first_non_empty(legacy_focus.get("trading_points"), []) or [],
        }

        return {
            "financial_snapshot": legacy_financial,
            "valuation_snapshot": valuation_snapshot,
            "stock_focus_snapshot": stock_focus_snapshot,
            "holder_snapshot": {
                "institution_holding_pct": holder_pct,
                "holder_count": shareholder_data.get("holder_count"),
                "actual_controller": shareholder_data.get("actual_controller"),
            },
            "sentiment_snapshot": sentiment_snapshot,
            "risk_snapshot": risk_snapshot,
            "benefit_alignment": legacy_cycle.get("beneficiary_reason") or stock_focus_snapshot["focus_view"],
            "elasticity_signals": _as_list(legacy_cycle.get("catalysts"))[:2] or ["后续催化验证"],
        }

    def _collect_buy_constraints(
        self,
        *,
        session_id: str,
        symbol: str,
        legacy_cycle: dict[str, Any],
        legacy_evidence: dict[str, Any],
        force: bool,
    ) -> dict[str, Any]:
        auxiliary = self._get_auxiliary_snapshots(session_id, symbol, force=force)
        valuation_data = _as_dict(auxiliary.get("valuation"))
        overdraft_data = _as_dict(auxiliary.get("price_overdraft"))
        sentiment_data = _as_dict(auxiliary.get("sentiment"))
        social_data = _as_dict(auxiliary.get("social"))
        risk_data = _as_dict(auxiliary.get("risk_events"))
        legacy_sentiment = _as_dict(legacy_evidence.get("sentiment_snapshot"))
        legacy_risk = _as_dict(legacy_evidence.get("risk_snapshot"))
        legacy_valuation = _as_dict(legacy_evidence.get("valuation_snapshot"))
        overdraft_signal = _as_dict(overdraft_data.get("price_overdraft_signal"))
        valuation_signal = _as_dict(valuation_data.get("price_overdraft_signal"))
        analysis = _as_dict(risk_data.get("analysis"))
        severity = _as_dict(analysis.get("severity_distribution"))

        risk_snapshot = {
            "high_risk_count": int(_pick_first_non_empty(severity.get("high"), legacy_risk.get("high_risk_count"), 0) or 0),
            "medium_risk_count": int(_pick_first_non_empty(severity.get("medium"), legacy_risk.get("medium_risk_count"), 0) or 0),
            "top_risk_labels": _pick_first_non_empty(analysis.get("top_risk_labels"), legacy_risk.get("top_risk_labels")) or [],
        }

        sentiment_snapshot = {
            "sentiment_score": _pick_first_non_empty(sentiment_data.get("sentiment_score"), legacy_sentiment.get("sentiment_score")),
            "social_score": _pick_first_non_empty(social_data.get("overall_score"), legacy_sentiment.get("social_score")),
            "news_count": _pick_first_non_empty(
                len(_as_list(sentiment_data.get("items"))) if sentiment_data.get("items") is not None else None,
                legacy_sentiment.get("news_count"),
            ),
            "discussion_count": _pick_first_non_empty(social_data.get("total_discussion"), legacy_sentiment.get("discussion_count")),
            "research_count": _pick_first_non_empty(legacy_sentiment.get("research_count"), 0),
            "positive_research_count": _pick_first_non_empty(legacy_sentiment.get("positive_research_count"), 0),
        }

        valuation_status = _normalize_risk_level(
            _pick_first_non_empty(
                overdraft_signal.get("status"),
                valuation_signal.get("status"),
                legacy_valuation.get("price_overdraft_status"),
                legacy_valuation.get("valuation_status"),
            ),
        )
        valuation_score = _coerce_number(_pick_first_non_empty(
            overdraft_signal.get("score"),
            valuation_signal.get("score"),
            legacy_valuation.get("price_overdraft_score"),
        ))
        return {
            "risk_snapshot": risk_snapshot,
            "sentiment_snapshot": sentiment_snapshot,
            "valuation_risk": valuation_status,
            "valuation_score": valuation_score,
            "position_risk": "high" if str(legacy_cycle.get("analysis_status") or "观察") in {"退潮", "非主线"} else "medium" if str(legacy_cycle.get("analysis_status") or "观察") == "观察" else "low" if _coerce_number(legacy_cycle.get("prosperity_score")) and _coerce_number(legacy_cycle.get("prosperity_score")) >= 75 else "medium",
            "chasing_risk": "high" if valuation_status == "high" or str(legacy_cycle.get("analysis_status") or "") == "退潮" else "medium" if valuation_status == "medium" else "low",
            "event_risk": "high" if risk_snapshot["high_risk_count"] > 0 else "medium" if risk_snapshot["medium_risk_count"] >= 3 else "low",
            "observation_points": _as_list(legacy_cycle.get("observation_points")) or ["等待回调承接", "跟踪后续催化兑现"],
        }

    def _build_final_decision_payload(
        self,
        *,
        stock_name: str,
        profile: dict[str, Any],
        industry_beta: dict[str, Any],
        mainline: dict[str, Any],
        constraints: dict[str, Any],
        legacy_cycle: dict[str, Any],
        legacy_report: dict[str, Any],
    ) -> dict[str, Any]:
        risk_data = _as_dict(constraints.get("buy_constraints"))
        mainline_passed = bool(_as_dict(mainline.get("mainline_detector")).get("passed"))
        beta_passed = bool(_as_dict(industry_beta.get("industry_beta_detector")).get("passed"))
        chasing_risk = _normalize_risk_level(risk_data.get("chasing_risk"))
        event_risk = _normalize_risk_level(risk_data.get("event_risk"), default="low")
        valuation_risk = _normalize_risk_level(risk_data.get("valuation_risk"))
        analysis_status = str(legacy_cycle.get("analysis_status") or "观察")
        beneficiary_level = str(legacy_cycle.get("beneficiary_level") or "待验证")
        killer_reason = str(legacy_cycle.get("killer_reason") or "").strip()
        prosperity_score = int(round(_coerce_number(legacy_cycle.get("prosperity_score")) or 0))
        observation_points = _as_list(risk_data.get("observation_points"))
        core_logic = str(legacy_cycle.get("core_logic") or "").strip()
        detector_gaps = []
        if not mainline_passed:
            detector_gaps.append(str(_as_dict(mainline.get("mainline_detector")).get("failed_reason") or "主线属性未完全成立"))
        if not beta_passed:
            detector_gaps.append(str(_as_dict(industry_beta.get("industry_beta_detector")).get("failed_reason") or "行业 beta 未完全成立"))

        if analysis_status in {"退潮", "非主线"} or (killer_reason and not (mainline_passed and beta_passed)):
            decision = "暂不买入"
            entry_type = "仅观察"
        elif mainline_passed and beta_passed and chasing_risk == "high":
            decision = "禁止追高"
            entry_type = "等待分歧"
        elif (
            mainline_passed
            and beta_passed
            and analysis_status in {"主线", "分支主线"}
            and beneficiary_level in {"核心受益", "直接受益"}
            and event_risk == "low"
            and valuation_risk == "low"
            and prosperity_score >= 70
        ):
            decision = "可买入"
            entry_type = "趋势跟随"
        elif mainline_passed or beta_passed or analysis_status == "观察" or prosperity_score >= 55:
            decision = "可跟踪等待"
            entry_type = "分歧低吸"
        else:
            decision = "暂不买入"
            entry_type = "仅观察"

        decision_reason_parts = []
        if core_logic:
            decision_reason_parts.append(core_logic)
        decision_reason_parts.append(f"当前周期标签为“{analysis_status}”，受益级别为“{beneficiary_level}”。")
        if prosperity_score > 0:
            decision_reason_parts.append(f"景气分为 {prosperity_score}。")
        if valuation_risk in {"medium", "high"}:
            decision_reason_parts.append(f"估值透支风险为 {valuation_risk}，买点节奏不能激进。")
        if event_risk in {"medium", "high"}:
            decision_reason_parts.append(f"风险事件压制级别为 {event_risk}。")
        if killer_reason:
            decision_reason_parts.append(f"当前核心约束是：{killer_reason}")
        if detector_gaps:
            decision_reason_parts.extend(detector_gaps[:2])

        not_buy_reasons = []
        if killer_reason:
            not_buy_reasons.append(killer_reason)
        if decision in {"可跟踪等待", "暂不买入", "禁止追高"}:
            if valuation_risk in {"medium", "high"}:
                not_buy_reasons.append(f"估值透支风险为 {valuation_risk}")
            if event_risk in {"medium", "high"}:
                not_buy_reasons.append(f"风险事件级别为 {event_risk}")
            not_buy_reasons.extend(detector_gaps[:2])
        not_buy_reasons = [item for item in not_buy_reasons if item]

        return {
            "buy_decision": {
                "decision": decision,
                "decision_reason": " ".join(decision_reason_parts) or "中期逻辑可跟踪，但当前更适合等待更优节奏。",
                "entry_type": entry_type,
                "not_buy_reasons": not_buy_reasons,
                "must_watch_points": observation_points,
            },
            "industry_cycle": {
                "analysis_status": analysis_status,
                "beneficiary_level": beneficiary_level,
                "cycle_phase": legacy_cycle.get("cycle_phase") or "观察期",
                "prosperity_score": prosperity_score or (68 if beta_passed else 45),
                "prosperity_judgement": legacy_cycle.get("prosperity_judgement") or "行业与个股证据已具备中期跟踪价值。",
                "core_logic": core_logic or profile.get("industry_mapping", {}).get("mapping_reason") or "主营与行业逻辑存在映射。",
            },
            "final_summary": (
                f"{stock_name} 当前具备买入条件，可以按趋势跟随处理。"
                if decision == "可买入"
                else f"{stock_name} 当前更适合跟踪等待，等分歧或回调再看。"
                if decision == "可跟踪等待"
                else f"{stock_name} 当前不适合追高，先等风险释放。"
                if decision == "禁止追高"
                else f"{stock_name} 当前暂不建议买入。"
            ),
            "raw_stream_output": str(legacy_report.get("raw_stream_output") or legacy_report.get("raw_response") or ""),
            "model_used": legacy_report.get("model_used") or "industry-cycle-report",
        }

    def _execute_step(self, session_id: str, step_key: str, *, force: bool = False) -> dict[str, Any]:
        with _LOCK:
            session = _SESSIONS[session_id]
            symbol = session["symbol"]
            stock_name = session["stock_name"]
            industry_name = session["industry_name"] or "未知行业"
            evidence = session["evidence"]

        legacy_report = self._get_legacy_report(session_id, symbol, force=force) if step_key != "profile_mapping" else None
        legacy_cycle = _as_dict(_as_dict(legacy_report).get("industry_cycle"))
        legacy_evidence = _as_dict(legacy_cycle.get("evidence"))

        if step_key == "profile_mapping":
            stock_info = _build_stock_info_snapshot(symbol)
            stock_name = str(stock_info.get("short_name") or stock_info.get("name") or symbol)
            industry_name = str(stock_info.get("industry") or "")
            profile = {
                "stock_profile": {
                    "symbol": symbol,
                    "stock_name": stock_name,
                    "industry_name": industry_name,
                    "main_business": str(stock_info.get("main_business") or ""),
                    "exchange": str(stock_info.get("market") or ""),
                    "as_of_date": datetime.now().date().isoformat(),
                },
                "industry_mapping": {
                    "is_industry_relevant": bool(industry_name and stock_info.get("main_business")),
                    "industry_match_level": "high" if industry_name and stock_info.get("main_business") else "low",
                    "mapping_reason": "主营描述与所属行业已建立映射。" if industry_name else "缺少所属行业信息。",
                    "business_keywords": [segment for segment in str(stock_info.get("main_business") or "").split("、") if segment][:3],
                    "industry_keywords": [industry_name] if industry_name else [],
                },
            }
            with _LOCK:
                session = _SESSIONS[session_id]
                session["stock_name"] = stock_name
                session["industry_name"] = industry_name
                session["evidence"][step_key] = profile
            return {
                "from_cache": False,
                "summary": "主营与行业归属已确认" if industry_name else "主营已获取，但行业归属仍缺失",
                "blocking": not bool(industry_name and stock_info.get("main_business")),
                "data": profile,
            }

        if step_key == "industry_beta":
            payload = self._collect_industry_beta_evidence(
                session_id=session_id,
                symbol=symbol,
                industry_name=industry_name,
                legacy_cycle=legacy_cycle,
                legacy_evidence=legacy_evidence,
                force=force,
            )
            with _LOCK:
                _SESSIONS[session_id]["evidence"][step_key] = payload
            quality = _as_dict(payload.get("data_quality"))
            has_sector_snapshot = bool(quality.get("has_sector_snapshot"))
            has_fund_flow = bool(quality.get("has_fund_flow"))
            summary = "行业 beta 证据已收集"
            if not has_sector_snapshot and not has_fund_flow:
                summary = "行业 beta 证据已收集，但板块与资金流字段不足"
            elif not has_sector_snapshot:
                summary = "行业 beta 证据已收集，但板块字段不足"
            elif not has_fund_flow:
                summary = "行业 beta 证据已收集，但资金流字段不足"
            return {
                "from_cache": False,
                "summary": summary,
                "blocking": False,
                "data": payload,
            }

        if step_key == "mainline_position":
            payload = self._collect_mainline_position_evidence(
                session_id=session_id,
                symbol=symbol,
                industry_name=industry_name,
                legacy_cycle=legacy_cycle,
                legacy_evidence=legacy_evidence,
                force=force,
            )
            with _LOCK:
                _SESSIONS[session_id]["evidence"][step_key] = payload
            return {
                "from_cache": False,
                "summary": "主线位置与催化证据已收集",
                "blocking": False,
                "data": payload,
            }

        if step_key == "company_benefit":
            payload = {
                "company_evidence": self._collect_company_benefit_evidence(
                    session_id=session_id,
                    symbol=symbol,
                    stock_name=stock_name,
                    industry_name=industry_name,
                    legacy_cycle=legacy_cycle,
                    legacy_evidence=legacy_evidence,
                    force=force,
                ),
            }
            with _LOCK:
                _SESSIONS[session_id]["evidence"][step_key] = payload
            return {
                "from_cache": False,
                "summary": "主营受益与弹性证据已收集",
                "blocking": False,
                "data": payload,
            }

        if step_key == "buy_constraints":
            payload = {
                "buy_constraints": self._collect_buy_constraints(
                    session_id=session_id,
                    symbol=symbol,
                    legacy_cycle=legacy_cycle,
                    legacy_evidence=legacy_evidence,
                    force=force,
                ),
            }
            with _LOCK:
                _SESSIONS[session_id]["evidence"][step_key] = payload
            return {
                "from_cache": False,
                "summary": "买点约束与风险证据已收集",
                "blocking": False,
                "data": payload,
            }

        if step_key == "final_decision":
            profile = evidence.get("profile_mapping", {})
            industry_beta = evidence.get("industry_beta", {})
            mainline = evidence.get("mainline_position", {})
            constraints = evidence.get("buy_constraints", {})
            payload = self._build_final_decision_payload(
                stock_name=stock_name,
                profile=profile,
                industry_beta=industry_beta,
                mainline=mainline,
                constraints=constraints,
                legacy_cycle=legacy_cycle,
                legacy_report=_as_dict(legacy_report),
            )
            with _LOCK:
                _SESSIONS[session_id]["evidence"][step_key] = payload
            return {
                "from_cache": False,
                "summary": "买入结论已生成",
                "blocking": False,
                "data": payload,
            }

        raise ValueError(f"unsupported step: {step_key}")

    def run_industry_beta_analysis_streaming(
        self,
        *,
        symbol: str,
        session_id: str,
    ) -> dict[str, Any]:
        """Submit industry_beta analysis as a streaming background task."""
        from src.services.task_queue import get_task_queue

        code = _normalize_symbol(symbol)
        task_queue = get_task_queue()
        task_id = f"industry-beta-{code.lower()}-{uuid.uuid4().hex[:8]}"

        def _run_task() -> dict[str, Any]:
            return self._execute_industry_beta_streaming(
                symbol=code,
                session_id=session_id,
                task_queue=task_queue,
                task_id=task_id,
            )

        return task_queue.submit_background_task(
            _run_task,
            stock_code=code,
            stock_name=f"INDUSTRY_BETA:{code}",
            report_type="industry_beta",
            message="行业beta分析任务已加入队列",
            task_id=task_id,
        )

    def _execute_industry_beta_streaming(
        self,
        *,
        symbol: str,
        session_id: str,
        task_queue,
        task_id: str,
    ) -> dict[str, Any]:
        """Phase 1: collect evidence with progress. Phase 2: LLM streaming."""
        collection_logs: list[dict[str, Any]] = []
        accumulated_llm_text = ""

        with _LOCK:
            session = _SESSIONS.get(session_id)
            if not session:
                raise KeyError(session_id)
            industry_name = session["industry_name"] or "未知行业"

        # === Phase 1: Evidence collection ===
        task_queue.update_task_progress(task_id, 5, "正在收集行业beta证据...")

        task_queue.update_task_progress(task_id, 10, "正在获取估值数据...")
        auxiliary = self._fetch_auxiliary_snapshots(symbol, force=True)
        valuation_data = _as_dict(auxiliary.get("valuation"))
        collection_logs.append({
            "source": "估值服务",
            "message": "估值数据获取完成",
            "detail": {
                "pe_ttm": valuation_data.get("pe_ttm"),
                "pb": valuation_data.get("pb"),
                "industry_pe": valuation_data.get("industry_pe"),
            },
        })

        task_queue.update_task_progress(task_id, 20, "正在获取板块数据...")
        sector_items = _as_list(_as_dict(get_sector_list(type="industry", force=True)).get("items"))
        n_sectors = len(sector_items)
        task_queue.update_task_progress(task_id, 25, f"板块数据获取完成: {n_sectors} 个行业")
        # Find the target industry's data
        target_sector = next((s for s in sector_items if industry_name in str(s.get("name", ""))), None)
        collection_logs.append({
            "source": "板块服务",
            "message": f"获取 {n_sectors} 个行业板块",
            "detail": {
                "total": n_sectors,
                "target_industry": target_sector.get("name") if target_sector else None,
                "target_rank": target_sector.get("rank") if target_sector else None,
                "target_change_pct": target_sector.get("change_pct") if target_sector else None,
                "target_amount": target_sector.get("total_amount") if target_sector else None,
                "target_leading_stock": target_sector.get("leading_stock") if target_sector else None,
            },
        })

        task_queue.update_task_progress(task_id, 30, "正在获取资金流数据...")
        flow_items = _as_list(_fetch_sector_flow_industry())
        n_flows = len(flow_items)
        task_queue.update_task_progress(task_id, 35, f"资金流数据获取完成: {n_flows} 个行业")
        target_flow = next((f for f in flow_items if industry_name in str(f.get("name", ""))), None)
        collection_logs.append({
            "source": "资金流服务",
            "message": f"获取 {n_flows} 个行业资金流",
            "detail": {
                "total": n_flows,
                "target_industry": target_flow.get("name") if target_flow else None,
                "target_rank": target_flow.get("rank") if target_flow else None,
                "target_net_inflow": target_flow.get("net_inflow") if target_flow else None,
                "target_amount": target_flow.get("total_amount") if target_flow else None,
            },
        })

        sentiment_data = _as_dict(auxiliary.get("sentiment"))
        news_items = _as_list(sentiment_data.get("items"))
        research_items = _as_list(sentiment_data.get("research_items"))
        news_count = len(news_items)
        research_count = len(research_items)
        task_queue.update_task_progress(task_id, 40, f"正在获取新闻和研报...")
        task_queue.update_task_progress(task_id, 45, f"新闻 {news_count} 条，研报 {research_count} 条")
        collection_logs.append({
            "source": "新闻/研报服务",
            "message": f"获取新闻 {news_count} 条，研报 {research_count} 条",
            "detail": {
                "news": [{"title": n.get("title"), "url": n.get("url")} for n in news_items[:5]],
                "research": [{"title": r.get("title")} for r in research_items[:3]],
            },
        })

        risk_data = _as_dict(auxiliary.get("risk_events"))
        risk_items = _as_list(risk_data.get("items"))
        risk_count = len(risk_items)
        collection_logs.append({
            "source": "风险事件服务",
            "message": f"获取风险事件 {risk_count} 条",
            "detail": {
                "total": risk_count,
                "items": [{"label": r.get("label"), "level": r.get("level"), "date": r.get("date")} for r in risk_items[:5]],
            },
        })

        live_sector_item, live_sector_rank, live_sector_total = _match_named_item(industry_name, sector_items)
        live_flow_item, live_flow_rank, live_flow_total = _match_named_item(industry_name, flow_items)

        legacy_report = self._get_legacy_report(session_id, symbol, force=True)
        legacy_cycle = _as_dict(_as_dict(legacy_report).get("industry_cycle"))
        legacy_evidence = _as_dict(legacy_cycle.get("evidence"))

        payload = self._collect_industry_beta_evidence(
            session_id=session_id,
            symbol=symbol,
            industry_name=industry_name,
            legacy_cycle=legacy_cycle,
            legacy_evidence=legacy_evidence,
            force=True,
        )

        task_queue.update_task_progress(task_id, 50, "证据收集完成，正在连接模型...")
        collection_logs.append({"source": "证据整理", "message": "四组证据已整理完成，准备调用模型"})

        # === Phase 2: LLM streaming ===
        evidence_pack = self._build_industry_beta_evidence_pack(
            symbol=symbol,
            industry_name=industry_name,
            sector_snapshot=payload["industry_beta_evidence"]["sector_snapshot"],
            fund_flow=payload["industry_beta_evidence"]["fund_flow"],
            peer_group=payload["industry_beta_evidence"]["peer_group"],
            news=news_items[:8],
            research=research_items[:6],
            risk_events=risk_items[:8],
            valuation=valuation_data,
        )

        system_prompt, user_prompt = self._build_industry_beta_llm_prompts(evidence_pack)

        last_emitted_len = 0

        def _stream_callback(_delta_text: str, full_text: str) -> None:
            nonlocal accumulated_llm_text, last_emitted_len
            accumulated_llm_text = full_text
            if len(full_text) - last_emitted_len >= 120 or len(full_text) < 120:
                last_emitted_len = len(full_text)
                task_queue.update_task_result(
                    task_id,
                    {
                        "phase": "llm_streaming",
                        "stream_text": full_text,
                        "collection_logs": collection_logs,
                    },
                    progress=min(95, 50 + max(1, len(full_text) // 100)),
                    message="模型正在分析...",
                )

        from src.ai_caller import call_ai_structured
        from src.analyzer import get_analyzer

        analyzer = get_analyzer()
        response_text, model_used, _usage = call_ai_structured(
            analyzer,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            call_type="industry_beta",
            temperature=0.2,
            max_tokens=4096,
            response_validator=lambda _text: None,
            stream=True,
            stream_text_callback=_stream_callback,
        )

        raw_text = accumulated_llm_text or response_text
        parsed = self._parse_industry_beta_json(raw_text)

        final_result = {
            "phase": "completed",
            "stream_text": raw_text,
            "collection_logs": collection_logs,
            "model_used": model_used,
            "result": {
                "industry_beta_evidence": payload["industry_beta_evidence"],
                "industry_beta_detector": parsed.get("industry_beta_detector", payload["industry_beta_detector"]),
                "price_war_analysis": parsed.get("price_war_analysis", {}),
                "driver_analysis": parsed.get("driver_analysis", {}),
                "data_quality": payload["data_quality"],
            },
        }

        with _LOCK:
            _SESSIONS[session_id]["evidence"]["industry_beta"] = payload
            _SESSIONS[session_id]["evidence"]["industry_beta_streaming"] = final_result

        return final_result

    def _build_industry_beta_evidence_pack(
        self,
        *,
        symbol: str,
        industry_name: str,
        sector_snapshot: dict,
        fund_flow: dict,
        peer_group: dict,
        news: list,
        research: list,
        risk_events: list,
        valuation: dict,
    ) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "industry_name": industry_name,
            "sector_snapshot": {
                "name": sector_snapshot.get("name"),
                "change_pct": sector_snapshot.get("change_pct"),
                "rank": sector_snapshot.get("rank"),
                "total": sector_snapshot.get("total"),
                "total_amount": sector_snapshot.get("total_amount"),
                "leading_stock": sector_snapshot.get("leading_stock"),
            },
            "fund_flow": {
                "net_inflow": fund_flow.get("net_inflow"),
                "rank": fund_flow.get("rank"),
                "pct_chg": fund_flow.get("pct_chg"),
                "total_amount": fund_flow.get("total_amount"),
            },
            "peer_group": {
                "sample_size": peer_group.get("sample_size"),
                "sample_names": peer_group.get("sample_names", [])[:10],
            },
            "news": [{"title": n.get("title"), "summary": n.get("summary")} for n in news[:8] if n.get("title")],
            "research": [{"title": r.get("title"), "summary": r.get("summary")} for r in research[:6] if r.get("title")],
            "risk_events": [{"label": r.get("label"), "level": r.get("level"), "date": r.get("date")} for r in risk_events[:8] if r.get("label")],
            "valuation": {
                "pe_ttm": valuation.get("pe_ttm"),
                "pb": valuation.get("pb"),
                "industry_pe": _as_dict(valuation.get("industry_average")).get("pe"),
            },
        }

    def _build_industry_beta_llm_prompts(self, evidence_pack: dict[str, Any]) -> tuple[str, str]:
        system_prompt = (
            "你是一个行业beta分析专家。你的职责是根据证据包判断股票所在行业的景气度、"
            "价格战风险、以及驱动因素是否充分。\n\n"
            "请按以下格式输出：\n"
            "1. 先用中文写一段分析过程（3-5句话），说明你的推理逻辑和关键发现。\n"
            "2. 然后在最后一行输出严格JSON（不要markdown代码块，不要额外文字）。\n\n"
            "分析顺序：\n"
            "1. 先看板块排名和资金流判断行业位置；\n"
            "2. 看新闻和研报判断行业驱动（政策/技术/需求/供给）；\n"
            "3. 看风险事件和新闻判断是否存在价格战/内卷；\n"
            "4. 综合判断行业beta是否通过。\n\n"
            "industry_beta_detector.passed 为 true 的条件：\n"
            "- 行业处于上升周期\n"
            "- 未来3年空间明确\n"
            "- 不是严重价格战/内卷行业\n"
            "- 有政策/技术/需求/供给变化驱动\n\n"
            "每个checklist项必须包含: item, passed(bool), reason, source。\n"
            "JSON结构必须且仅包含以下三个顶级字段：\n"
            "- industry_beta_detector: {passed, conclusion, failed_reason, checklist: [{item, passed, reason, source}]}\n"
            "- price_war_analysis: {detected(bool), severity(\"low\"|\"medium\"|\"high\"), evidence: [具体证据文本]}\n"
            "- driver_analysis: {policy: [具体驱动], technology: [具体驱动], demand: [具体驱动], supply: [具体驱动], sufficiency(\"insufficient\"|\"partial\"|\"sufficient\")}"
        )
        user_prompt = (
            f"请根据以下证据包判断行业beta：\n\n"
            f"证据包：{json.dumps(evidence_pack, ensure_ascii=False, default=str)}"
        )
        return system_prompt, user_prompt

    def _parse_industry_beta_json(self, raw_text: str) -> dict[str, Any]:
        raw_text = (raw_text or "").strip()
        # Extract JSON from end of text (after Chinese analysis)
        match = re.search(r'(\{[\s\S]*\})\s*$', raw_text)
        if match:
            try:
                return json.loads(match.group(1))
            except (json.JSONDecodeError, ValueError):
                pass
        # Try parsing entire text as JSON
        try:
            return json.loads(raw_text)
        except (json.JSONDecodeError, ValueError):
            pass
        # Strip markdown code fences
        cleaned = re.sub(r'^```(?:json)?\s*', '', raw_text, flags=re.MULTILINE)
        cleaned = re.sub(r'\s*```$', '', cleaned, flags=re.MULTILINE).strip()
        try:
            return json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            pass
        return {
            "industry_beta_detector": {"passed": False, "conclusion": "模型输出格式异常，无法解析", "failed_reason": "未能解析JSON", "checklist": []},
            "price_war_analysis": {"detected": False, "severity": "unknown", "evidence": []},
            "driver_analysis": {"policy": [], "technology": [], "demand": [], "supply": [], "sufficiency": "insufficient"},
        }

    def _get_legacy_report(self, session_id: str, symbol: str, *, force: bool) -> dict[str, Any]:
        with _LOCK:
            existing = _SESSIONS[session_id]["evidence"].get("_legacy_report")
        if isinstance(existing, dict) and not force:
            return existing
        report = IndustryCycleService().get_report(symbol=symbol, force=force)
        payload = report if isinstance(report, dict) else {}
        with _LOCK:
            _SESSIONS[session_id]["evidence"]["_legacy_report"] = payload
        return payload
