# -*- coding: utf-8 -*-
"""IndustryCycleService orchestrates evidence collection, LLM analysis, prompt building and report assembly."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Callable, Optional

from .cache import (
    _cache_get,
    _current_report_as_of_date,
    _report_cache_get,
    uuid4_hex,
)
from .data_quality import (
    _assess_evidence_gate,
    _build_data_quality,
    _build_evidence_insufficient_report,
)
from .industry_data import (
    _build_stock_focus_snapshot,
    _build_trading_signals,
    _build_trading_snapshot,
    _fallback_sector_item_from_flow,
    _fetch_lhb_snapshot,
    _fetch_peer_snapshot,
    _fetch_stock_flow_snapshot,
    _fetch_ths_industry_summary,
    _find_industry_board,
    _find_sector_flow,
)
from .llm_parse import (
    _parse_llm_json_payload,
    _pick_theme_detail,
)
from .normalization import (
    _as_dict,
    _as_list,
    _first_dict,
    _list_of_dicts,
    _normalize_symbol,
    _normalize_text,
    _prune_none,
    _safe_float,
    _safe_int,
    _summarize_text_items,
)
from .report import (
    _build_streaming_industry_cycle_draft,
    _is_usable_report_payload,
)

logger = logging.getLogger(__name__)

_MAINLINE_CRITERION_IDS = (
    "market_mainline_membership",
    "market_attention",
    "substantive_business_link",
    "actual_business_benefit",
    "structural_drivers",
    "medium_term_catalysts",
)
_INDUSTRY_BETA_CRITERION_IDS = (
    "industry_upcycle",
    "three_year_space",
    "competition_quality",
    "structural_drivers",
)


def _validated_detector(
    value: Any,
    expected_ids: tuple[str, ...],
    *,
    label: str,
) -> dict[str, Any]:
    detector = _as_dict(value)
    items = _list_of_dicts(detector.get("checklist"))
    by_id = {
        _normalize_text(item.get("criterion_id")): item for item in items if _normalize_text(item.get("criterion_id"))
    }
    missing = [criterion_id for criterion_id in expected_ids if criterion_id not in by_id]
    extra = [criterion_id for criterion_id in by_id if criterion_id not in expected_ids]
    ordered = [by_id[criterion_id] for criterion_id in expected_ids if criterion_id in by_id]
    if missing or extra or len(ordered) != len(expected_ids):
        return {
            "passed": False,
            "conclusion": "",
            "failed_reason": (f"{label}结构不完整；missing={missing} extra={extra}。" "未使用关键词或固定分数补判。"),
            "checklist": ordered,
        }
    return {
        "passed": bool(detector.get("passed")) and all(bool(item.get("passed")) for item in ordered),
        "conclusion": _normalize_text(detector.get("conclusion")),
        "failed_reason": detector.get("failed_reason"),
        "checklist": ordered,
    }


def _persist_cache(symbol: str, payload: dict[str, Any]) -> None:
    """Route cache writes through the shell module so monkeypatches in tests still take effect."""
    from src.services import industry_cycle_service as _shell

    _shell._cache_put(symbol, payload)


def _persist_report_cache(symbol: str, payload: dict[str, Any]) -> None:
    from src.services import industry_cycle_service as _shell

    _shell._report_cache_put(symbol, payload)


class IndustryCycleService:
    REPORT_TYPE = "industry_cycle_report"

    def analyze(self, symbol: str, *, force: bool = False) -> dict[str, Any]:
        code = _normalize_symbol(symbol)
        if not force:
            cached = _cache_get(code)
            if _is_usable_report_payload(cached):
                cached["_cached"] = True
                return cached

        payload = self._analyze_semantically(code, force=force)
        _persist_cache(code, payload)
        return payload

    def _analyze_semantically(self, symbol: str, *, force: bool) -> dict[str, Any]:
        """Collect raw evidence and delegate every business judgment to the model."""
        evidence_bundle = self._collect_evidence_bundle(
            symbol=symbol,
            force=force,
        )
        evidence_pack = evidence_bundle["evidence_pack"]
        system_prompt, user_prompt = self._build_model_report_prompts(evidence_pack)
        gate = _assess_evidence_gate(evidence_pack)
        if not gate.get("passed"):
            return _build_evidence_insufficient_report(
                symbol=symbol,
                evidence_pack=evidence_pack,
                gate=gate,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        return self._build_llm_industry_cycle_report(
            symbol=symbol,
            evidence_bundle=evidence_bundle,
            evidence_pack=evidence_pack,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

    def get_report(self, symbol: str, *, force: bool = False) -> dict[str, Any]:
        code = _normalize_symbol(symbol)
        if force:
            payload = self.analyze(code, force=True)
            payload["report_pending"] = False
            _persist_report_cache(code, payload)
            return payload
        if not force:
            cached = _report_cache_get(code)
            if _is_usable_report_payload(cached):
                cached["_cached"] = True
                cached["report_pending"] = False
                return cached
            if cached:
                logger.info("industry cycle report cache is stale or incomplete for %s", code)

            day_snapshot = _cache_get(code)
            if _is_usable_report_payload(day_snapshot):
                day_snapshot["_cached"] = True
                day_snapshot["report_pending"] = False
                _persist_report_cache(code, day_snapshot)
                return day_snapshot
            if day_snapshot:
                logger.info("industry cycle day snapshot is stale or incomplete for %s", code)
        return self._build_minimal_report(code)

    def submit_report_task(self, symbol: str, *, force: bool = True):
        from src.services.task_queue import get_task_queue

        code = _normalize_symbol(symbol)
        task_queue = get_task_queue()
        task_id = f"industry-cycle-{code.lower()}-{uuid4_hex()}"

        def _run_task() -> dict[str, Any]:
            return self._generate_report_stream(symbol=code, force=force, task_queue=task_queue, task_id=task_id)

        return task_queue.submit_background_task(
            _run_task,
            stock_code=code,
            stock_name=f"INDUSTRY_CYCLE:{code}",
            report_type=self.REPORT_TYPE,
            message="行业周期分析任务已加入队列",
            task_id=task_id,
        )

    def _build_minimal_report(self, symbol: str) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "as_of_date": _current_report_as_of_date(),
            "industry_cycle": None,
            "report_pending": True,
            "llm_used": False,
            "model_used": None,
            "raw_stream_output": "",
            "debug_input": None,
            "_fetched_at": datetime.now().isoformat(),
            "_cached": False,
            "fallback_used": False,
        }

    def _generate_report_stream(
        self,
        *,
        symbol: str,
        force: bool,
        task_queue: Any,
        task_id: str,
    ) -> dict[str, Any]:
        task_queue.update_task_progress(task_id, 5, "正在收集行业周期证据")
        collection_logs: list[str] = []

        def _push_collection_update(
            partial_evidence_pack: dict[str, Any],
            progress: int,
            message: str,
        ) -> None:
            collection_logs.append(f"- {message}")
            task_queue.update_task_result(
                task_id,
                {
                    "phase": "collecting",
                    "stream_text": "【证据采集中】\n" + "\n".join(collection_logs),
                    "debug_input": {
                        "system_prompt": None,
                        "user_prompt": None,
                        "evidence_pack": partial_evidence_pack,
                    },
                },
                progress=progress,
                message=message,
            )

        evidence_bundle = self._collect_evidence_bundle(
            symbol=symbol,
            force=force,
            on_progress=_push_collection_update,
        )
        evidence_pack = evidence_bundle["evidence_pack"]
        system_prompt, user_prompt = self._build_model_report_prompts(evidence_pack)
        evidence_gate = _assess_evidence_gate(evidence_pack)

        task_queue.update_task_result(
            task_id,
            {
                "phase": "collecting",
                "stream_text": "【证据采集中】\n" + "\n".join(collection_logs),
                "report_draft": {
                    "symbol": symbol,
                    "as_of_date": _current_report_as_of_date(),
                    "industry_cycle": {
                        "stock_name": str(evidence_pack.get("stock_name") or symbol),
                        "industry_name": str(evidence_pack.get("industry_name") or ""),
                        "analysis_status": "观察",
                        "beneficiary_level": None,
                        "beneficiary_reason": _normalize_text(
                            _as_dict(
                                _as_dict(evidence_pack.get("company_specific_evidence")).get("stock_focus_snapshot")
                            ).get("focus_view")
                        ),
                        "cycle_phase": None,
                        "cycle_phase_reason": "",
                        "prosperity_score": 0,
                        "prosperity_judgement": "",
                        "core_logic": _normalize_text(
                            _as_dict(
                                _as_dict(evidence_pack.get("company_specific_evidence")).get("stock_focus_snapshot")
                            ).get("focus_view")
                        ),
                        "killer_reason": None,
                        "observation_window": "未来 6-12 个月",
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
                            "stock_focus_snapshot": _as_dict(
                                _as_dict(evidence_pack.get("company_specific_evidence")).get("stock_focus_snapshot")
                            ),
                            "market_mainline": {
                                "report_pending": bool(
                                    _as_dict(evidence_pack.get("mainline_context")).get("report_pending")
                                ),
                                "market_stage": _as_dict(
                                    _as_dict(evidence_pack.get("mainline_context")).get("market_stage")
                                ),
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
                            "fund_flow": _as_dict(
                                _as_dict(evidence_pack.get("industry_beta_evidence")).get("fund_flow_snapshot")
                            ),
                            "peer_group": _as_dict(
                                _as_dict(evidence_pack.get("industry_beta_evidence")).get("peer_snapshot")
                            ),
                            "financial_snapshot": _as_dict(
                                _as_dict(evidence_pack.get("company_specific_evidence")).get("financial_snapshot")
                            ),
                            "valuation_snapshot": {},
                            "sentiment_snapshot": _as_dict(
                                _as_dict(evidence_pack.get("supporting_judgement")).get("coverage_snapshot")
                            ),
                            "risk_snapshot": _as_dict(
                                _as_dict(evidence_pack.get("supporting_judgement")).get("risk_snapshot")
                            ),
                            "data_quality": _as_dict(
                                _as_dict(evidence_pack.get("supporting_judgement")).get("data_quality")
                            ),
                            "driver_signals": _as_dict(
                                _as_dict(evidence_pack.get("supporting_judgement")).get("driver_clues")
                            ),
                        },
                    },
                    "report_pending": True,
                    "llm_used": False,
                    "raw_stream_output": "【证据采集中】\n" + "\n".join(collection_logs),
                    "raw_response": "【证据采集中】\n" + "\n".join(collection_logs),
                    "debug_input": {
                        "system_prompt": system_prompt,
                        "user_prompt": user_prompt,
                        "evidence_pack": evidence_pack,
                    },
                    "_cached": False,
                },
                "debug_input": {
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                    "evidence_pack": evidence_pack,
                },
            },
            progress=30,
            message="证据包已准备完成，等待模型连接",
        )
        if not evidence_gate.get("passed"):
            return {
                "phase": "completed",
                "stream_text": "【证据闸门未通过】\n"
                + "\n".join(f"- {item}" for item in _as_list(evidence_gate.get("blocking_reasons"))),
                "report": _build_evidence_insufficient_report(
                    symbol=symbol,
                    evidence_pack=evidence_pack,
                    gate=evidence_gate,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                ),
                "debug_input": {
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                    "evidence_pack": evidence_pack,
                },
                "llm_used": False,
                "model_used": None,
            }
        task_queue.update_task_result(
            task_id,
            {
                "phase": "waiting_model",
                "debug_input": {
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                    "evidence_pack": evidence_pack,
                },
            },
            progress=36,
            message="正在连接模型服务",
        )

        result = self._build_llm_industry_cycle_report(
            symbol=symbol,
            evidence_bundle=evidence_bundle,
            evidence_pack=evidence_pack,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            on_text=lambda accumulated_text, draft: task_queue.update_task_result(
                task_id,
                {
                    "phase": "generating",
                    "stream_text": accumulated_text,
                    "report_draft": draft,
                    "debug_input": {
                        "system_prompt": system_prompt,
                        "user_prompt": user_prompt,
                        "evidence_pack": evidence_pack,
                    },
                },
                progress=min(92, 36 + max(1, len(accumulated_text) // 140)),
                message="模型已连接，正在生成行业周期分析",
            ),
        )

        return {
            "phase": "completed",
            "stream_text": result.get("raw_stream_output") or result.get("raw_response") or "",
            "report_draft": {
                "symbol": result.get("symbol"),
                "as_of_date": result.get("as_of_date"),
                "industry_cycle": result.get("industry_cycle"),
                "report_pending": False,
                "llm_used": result.get("llm_used", True),
                "model_used": result.get("model_used"),
                "raw_stream_output": result.get("raw_stream_output") or result.get("raw_response") or "",
                "raw_response": result.get("raw_response") or result.get("raw_stream_output") or "",
                "debug_input": result.get("debug_input"),
                "_cached": False,
                "fallback_used": result.get("fallback_used"),
            },
            "debug_input": result.get("debug_input"),
            "report": result,
            "llm_used": result.get("llm_used", True),
            "model_used": result.get("model_used"),
        }

    def _collect_evidence_bundle(
        self,
        *,
        symbol: str,
        force: bool,
        on_progress: Optional[Callable[[dict[str, Any], int, str], None]] = None,
    ) -> dict[str, Any]:
        from api.v1.endpoints.financials import (
            get_announcements,
            get_financials,
            get_financial_statements,
            get_research_report,
            get_risk_events,
            get_shareholder_structure,
            get_sentiment,
            get_social_sentiment,
            get_valuation_ratios,
            search_news,
        )
        from api.v1.endpoints.macro import _fetch_sector_flow_industry
        from api.v1.endpoints.sectors import get_sector_list
        from api.v1.endpoints.stock_info import get_stock_info
        from src.services.market_theme_service import MarketThemeService

        def _emit(progress: int, message: str, payload: dict[str, Any]) -> None:
            if on_progress:
                on_progress(payload, progress, message)

        generated_at = datetime.now().isoformat()
        evidence_pack: dict[str, Any] = {
            "symbol": symbol,
            "stock_name": "",
            "industry_name": "",
            "generated_at": generated_at,
        }

        stock_info = _as_dict(get_stock_info(symbol=symbol, force=force))
        if not stock_info.get("_ths_business_ok") and not _normalize_text(stock_info.get("product_type")):
            refreshed_stock_info = _as_dict(get_stock_info(symbol=symbol, force=True))
            if refreshed_stock_info:
                stock_info = refreshed_stock_info
        industry_name = _normalize_text(stock_info.get("industry"))
        stock_name = _normalize_text(stock_info.get("short_name") or stock_info.get("name") or symbol)
        main_business = _normalize_text(stock_info.get("main_business"))
        evidence_pack.update(
            {
                "stock_name": stock_name,
                "industry_name": industry_name,
                "stock_info": _prune_none(
                    {
                        "name": stock_name,
                        "industry": industry_name,
                        "market": stock_info.get("market"),
                        "listing_date": stock_info.get("listing_date"),
                        "main_business": main_business,
                        "product_type": stock_info.get("product_type"),
                        "product_name": stock_info.get("product_name"),
                        "profile": stock_info.get("profile"),
                    }
                ),
            }
        )
        _emit(10, "已获取公司资料与主营业务", dict(evidence_pack))

        market_theme_service = MarketThemeService()
        market_report = _as_dict(market_theme_service.get_model_report(force=False))
        market_evidence = _as_dict(market_theme_service.get_evidence(force=False))
        evidence_pack["market_mainline_report"] = {
            "report_pending": bool(market_report.get("report_pending")),
            "overview": market_report.get("overview"),
            "market_stage": market_report.get("market_stage"),
            "current_mainlines": (market_report.get("current_mainlines") or [])[:5],
            "future_mainlines": (market_report.get("future_mainlines") or [])[:5],
        }
        evidence_pack["market_mainline_evidence"] = {
            "market_stage": market_evidence.get("market_stage"),
            "current_themes": (market_evidence.get("current_themes") or [])[:5],
            "next_themes": (market_evidence.get("next_themes") or [])[:5],
            "policy_watchlist": (market_evidence.get("policy_watchlist") or [])[:8],
        }
        _emit(14, "已接入市场主线报告与主线证据层", dict(evidence_pack))

        news_data = _as_dict(search_news(symbol=symbol, days=90, source="all", force=force))
        evidence_pack["news_items"] = (news_data.get("items") or [])[:15]
        _emit(17, "已汇总相关新闻样本", dict(evidence_pack))

        announcements_data = _as_dict(get_announcements(symbol=symbol, days=180, type="all", force=force))
        evidence_pack["announcement_items"] = (announcements_data.get("items") or [])[:12]
        _emit(19, "已汇总公司公告样本", dict(evidence_pack))

        risk_data = _as_dict(get_risk_events(symbol=symbol, days=180, force=force))
        evidence_pack["risk_items"] = (risk_data.get("items") or [])[:10]
        _emit(21, "已汇总风险事件样本", dict(evidence_pack))

        research_data = _as_dict(get_research_report(symbol=symbol, days=1095, force=force))
        evidence_pack["research_items"] = (research_data.get("items") or [])[:12]
        _emit(24, "已汇总券商研报样本", dict(evidence_pack))

        financials_data = _as_dict(get_financials(symbol=symbol, periods=4, force=force))
        evidence_pack["financial_items"] = _list_of_dicts(financials_data.get("items"))[:4]
        latest_financial = _first_dict(evidence_pack["financial_items"])
        evidence_pack["financial_snapshot"] = _prune_none(
            {
                "latest_report_date": latest_financial.get("report_date"),
                "revenue": latest_financial.get("revenue"),
                "revenue_yoy": latest_financial.get("revenue_yoy"),
                "net_profit": latest_financial.get("net_profit"),
                "net_profit_yoy": latest_financial.get("net_profit_yoy"),
                "roe": latest_financial.get("roe"),
                "gross_margin": latest_financial.get("gross_margin"),
                "debt_ratio": latest_financial.get("debt_ratio"),
                "eps": latest_financial.get("eps"),
            }
        )
        _emit(26, "已汇总核心财务摘要", dict(evidence_pack))

        financial_statements = _as_dict(get_financial_statements(symbol=symbol, periods=8, force=force))
        latest_balance = _first_dict(financial_statements.get("balance_sheet"))
        latest_income = _first_dict(financial_statements.get("income_statement"))
        latest_cashflow = _first_dict(financial_statements.get("cashflow"))
        evidence_pack["financial_statements_snapshot"] = _prune_none(
            {
                "source": financial_statements.get("source"),
                "balance_sheet": {
                    "report_date": latest_balance.get("report_date"),
                    "contract_liabilities": latest_balance.get("contract_liabilities"),
                    "inventory": latest_balance.get("inventory"),
                    "accounts_receivable": latest_balance.get("accounts_receivable"),
                    "fixed_asset": latest_balance.get("fixed_asset"),
                    "short_loan": latest_balance.get("short_loan"),
                    "long_loan": latest_balance.get("long_loan"),
                    "debt_ratio": latest_balance.get("debt_ratio"),
                },
                "income_statement": {
                    "report_date": latest_income.get("report_date"),
                    "revenue": latest_income.get("revenue"),
                    "revenue_yoy": latest_income.get("revenue_yoy"),
                    "parent_net_profit": latest_income.get("parent_net_profit"),
                    "parent_net_profit_yoy": latest_income.get("parent_net_profit_yoy"),
                    "deducted_net_profit": latest_income.get("deducted_net_profit"),
                    "deducted_net_profit_yoy": latest_income.get("deducted_net_profit_yoy"),
                    "gross_margin": latest_income.get("gross_margin"),
                    "research_expense": latest_income.get("research_expense"),
                    "asset_impairment_loss": latest_income.get("asset_impairment_loss"),
                },
                "cashflow": {
                    "report_date": latest_cashflow.get("report_date"),
                    "operating_cf": latest_cashflow.get("operating_cf"),
                    "investing_cf": latest_cashflow.get("investing_cf"),
                    "financing_cf": latest_cashflow.get("financing_cf"),
                    "capex": latest_cashflow.get("capex"),
                    "free_cashflow": latest_cashflow.get("free_cashflow"),
                    "cf_quality": latest_cashflow.get("cf_quality"),
                },
            }
        )
        _emit(28, "已汇总三大财报明细摘要", dict(evidence_pack))

        shareholder_data = _as_dict(get_shareholder_structure(symbol=symbol, force=force))
        evidence_pack["shareholder_snapshot"] = _prune_none(
            {
                "actual_controller": shareholder_data.get("actual_controller"),
                "holder_count": shareholder_data.get("holder_count"),
                "holder_count_change_pct": shareholder_data.get("holder_count_change_pct"),
                "institution_holding_pct": shareholder_data.get("institution_holding_pct"),
                "top10_holders": _list_of_dicts(shareholder_data.get("top10_holders"))[:5],
                "major_holder_changes": _list_of_dicts(shareholder_data.get("major_holder_changes"))[:6],
                "source_chain": _as_list(shareholder_data.get("source_chain")),
                "errors": _as_list(shareholder_data.get("errors")),
            }
        )
        _emit(29, "已汇总股东结构与重要股东变动", dict(evidence_pack))

        lhb_snapshot = _fetch_lhb_snapshot(symbol)
        trading_snapshot = _build_trading_snapshot(symbol)
        stock_flow_snapshot = _fetch_stock_flow_snapshot(symbol, force=force)
        evidence_pack["market_trading_snapshot"] = trading_snapshot
        trading_signal_snapshot = _build_trading_signals(trading_snapshot, lhb_snapshot)
        evidence_pack["trading_signal_snapshot"] = trading_signal_snapshot
        evidence_pack["stock_flow_snapshot"] = stock_flow_snapshot
        stock_focus_snapshot = _build_stock_focus_snapshot(
            stock_name=stock_name,
            main_business=main_business,
            company_specific_evidence={
                "financial_snapshot": evidence_pack.get("financial_snapshot"),
                "financial_statements_snapshot": evidence_pack.get("financial_statements_snapshot"),
                "shareholder_snapshot": evidence_pack.get("shareholder_snapshot"),
                "trading_signal_snapshot": trading_signal_snapshot,
                "stock_flow_snapshot": stock_flow_snapshot,
                "announcements": announcements_data.get("items"),
                "news": news_data.get("items"),
                "research": research_data.get("items"),
                "product_type": stock_info.get("product_type"),
                "product_name": stock_info.get("product_name"),
            },
        )
        evidence_pack["stock_focus_snapshot"] = stock_focus_snapshot
        _emit(30, "已汇总实时行情与K线交易快照", dict(evidence_pack))

        # Raw company evidence is passed to the model unchanged.  The service
        # no longer promotes phrases into driver or competition conclusions.
        driver_clues: dict[str, list[str]] = {}
        competition_clues: list[str] = []

        sentiment_data = _as_dict(get_sentiment(symbol=symbol, days=90, force=force))
        social_data = _as_dict(get_social_sentiment(symbol=symbol, days=90, force=force))
        valuation_data = _as_dict(get_valuation_ratios(symbol=symbol, with_history=True, force=force))
        sector_data = _as_dict(get_sector_list(type="industry", force=force))
        flow_records = _list_of_dicts(_fetch_sector_flow_industry())
        ths_industry_summary = _fetch_ths_industry_summary(industry_name)
        board_item, board_rank, board_total = _find_industry_board(industry_name, sector_data.get("items") or [])
        flow_item, flow_rank, flow_total = _find_sector_flow(industry_name, flow_records)
        peer_snapshot = _fetch_peer_snapshot(
            industry_name,
            board_name=_normalize_text(ths_industry_summary.get("matched_name")),
            board_code=_normalize_text(ths_industry_summary.get("matched_code")),
        )
        ths_board_summary = _as_dict(ths_industry_summary.get("summary"))
        if ths_board_summary:
            board_item = {
                "name": ths_board_summary.get("name"),
                "code": ths_board_summary.get("code"),
                "change_pct": ths_board_summary.get("change_pct"),
                "lead_stock": ths_board_summary.get("lead_stock"),
                "lead_stock_price": ths_board_summary.get("lead_stock_price"),
                "lead_stock_change_pct": ths_board_summary.get("lead_stock_change_pct"),
                "up_count": ths_board_summary.get("up_count"),
                "down_count": ths_board_summary.get("down_count"),
                "total_amount": ths_board_summary.get("total_amount"),
                "net_flow": ths_board_summary.get("net_flow"),
                "_source": ths_board_summary.get("source"),
            }
            board_rank = _safe_int(ths_board_summary.get("rank"))
            board_total = _safe_int(ths_board_summary.get("total")) or board_total
            if flow_item is None:
                flow_item = {
                    "name": ths_board_summary.get("name"),
                    "pct_chg": ths_board_summary.get("change_pct"),
                    "main_net_inflow": ths_board_summary.get("net_flow"),
                    "super_large_net_inflow": None,
                    "large_net_inflow": None,
                    "total_amount": ths_board_summary.get("total_amount"),
                    "up_count": ths_board_summary.get("up_count"),
                    "down_count": ths_board_summary.get("down_count"),
                    "leading_stock": ths_board_summary.get("lead_stock"),
                    "_source": ths_board_summary.get("source"),
                }
                flow_rank = board_rank
                flow_total = board_total
        if board_item is None and flow_item is not None:
            board_item = _fallback_sector_item_from_flow(flow_item)
            board_rank = flow_rank
            board_total = flow_total

        valuation_signal = (valuation_data or {}).get("price_overdraft_signal") or {}
        industry_average = (valuation_data or {}).get("industry_average") or {}
        sentiment_snapshot = _prune_none(
            {
                "news_count": len(news_data.get("items") or []),
                "research_count": len(research_data.get("items") or []),
                "discussion_count": _safe_int(social_data.get("total_discussion")) or 0,
                "semantic_status": "model_required",
            }
        )
        risk_snapshot = _prune_none(
            {
                "item_count": len(_list_of_dicts(risk_data.get("items"))),
                "semantic_status": "model_required",
            }
        )
        evidence_pack["sentiment_snapshot"] = sentiment_snapshot
        evidence_pack["risk_snapshot"] = risk_snapshot
        _emit(31, "已完成舆情、社交情绪和风险摘要", dict(evidence_pack))

        sector_snapshot = _prune_none(
            {
                "rank": board_rank,
                "total": board_total,
                "name": (board_item or {}).get("name"),
                "code": (board_item or {}).get("code"),
                "change_pct": _safe_float((board_item or {}).get("change_pct")),
                "leading_stock": (board_item or {}).get("lead_stock"),
                "leading_stock_price": _safe_float((board_item or {}).get("lead_stock_price")),
                "leading_stock_change_pct": _safe_float((board_item or {}).get("lead_stock_change_pct")),
                "up_count": _safe_int((board_item or {}).get("up_count")),
                "down_count": _safe_int((board_item or {}).get("down_count")),
                "total_amount": _safe_float((board_item or {}).get("total_amount")),
                "net_flow": _safe_float((board_item or {}).get("net_flow")),
                "fallback_from_flow": bool((board_item or {}).get("_fallback_from_flow")),
                "source": (board_item or {}).get("_source")
                or ("ths_industry_summary" if ths_board_summary else "sector_endpoint"),
            }
        )
        fund_flow_source = (flow_item or {}).get("_source") or (
            "macro_sector_flow" if flow_item else ("ths_industry_summary" if ths_board_summary else "macro_sector_flow")
        )
        fund_flow_snapshot = _prune_none(
            {
                "rank": flow_rank,
                "total": flow_total,
                "name": (flow_item or {}).get("name"),
                "change_pct": _safe_float((flow_item or {}).get("pct_chg")),
                "net_flow": (
                    _safe_float((flow_item or {}).get("main_net_inflow"))
                    or _safe_float((flow_item or {}).get("net_flow"))
                    or _safe_float((board_item or {}).get("net_flow"))
                ),
                "super_large_net_inflow": _safe_float((flow_item or {}).get("super_large_net_inflow")),
                "large_net_inflow": _safe_float((flow_item or {}).get("large_net_inflow")),
                "total_amount": _safe_float((flow_item or {}).get("total_amount"))
                or _safe_float((board_item or {}).get("total_amount")),
                "up_count": _safe_int((flow_item or {}).get("up_count")),
                "down_count": _safe_int((flow_item or {}).get("down_count")),
                "leading_stock": (flow_item or {}).get("leading_stock") or (board_item or {}).get("lead_stock"),
                "source": fund_flow_source,
            }
        )
        evidence_pack["sector_snapshot"] = sector_snapshot
        evidence_pack["fund_flow"] = fund_flow_snapshot
        evidence_pack["peer_group"] = peer_snapshot
        _emit(33, "已完成行业板块、资金流和同行样本整理", dict(evidence_pack))

        valuation_snapshot = _prune_none(
            {
                "pe_ttm": _safe_float((valuation_data or {}).get("pe_ttm")),
                "pb": _safe_float((valuation_data or {}).get("pb")),
                "industry_name": industry_average.get("industry"),
                "industry_pe": _safe_float(industry_average.get("pe")),
                "industry_pb": _safe_float(industry_average.get("pb")),
                "industry_sample_size": _safe_int(industry_average.get("sample_size")),
                "pe_premium_vs_industry_pct": _safe_float(
                    (valuation_signal.get("metrics") or {}).get("pe_premium_vs_industry_pct")
                ),
                "pb_premium_vs_industry_pct": _safe_float(
                    (valuation_signal.get("metrics") or {}).get("pb_premium_vs_industry_pct")
                ),
                "forward_pe_change_vs_ttm_pct": _safe_float(
                    (valuation_signal.get("metrics") or {}).get("forward_pe_change_vs_ttm_pct")
                ),
                "semantic_status": "model_required",
            }
        )
        if _safe_int(peer_snapshot.get("sample_size")) in (0, None) and _safe_int(
            industry_average.get("sample_size")
        ) not in (0, None):
            peer_snapshot = {
                **peer_snapshot,
                "sample_size": _safe_int(industry_average.get("sample_size")),
                "source": peer_snapshot.get("source") or "valuation_industry_average_fallback",
            }
        evidence_pack["valuation_snapshot"] = valuation_snapshot
        data_quality = _build_data_quality(
            board_rank=board_rank,
            flow_rank=flow_rank,
            research_count=len(_as_list(research_data.get("items"))),
            discussion_count=_safe_int(social_data.get("total_discussion")) or 0,
            peer_sample_size=_safe_int(peer_snapshot.get("sample_size")),
            board_source_ok=bool(sector_data.get("items"))
            or flow_item is not None
            or bool(ths_industry_summary.get("source_ok")),
            peer_source_ok=bool(peer_snapshot.get("source_ok", True)),
            financial_statements_available=bool(
                financial_statements.get("balance_sheet")
                or financial_statements.get("income_statement")
                or financial_statements.get("cashflow")
            ),
            shareholder_available=bool(
                shareholder_data.get("actual_controller")
                or shareholder_data.get("top10_holders")
                or shareholder_data.get("major_holder_changes")
            ),
            trading_snapshot_available=bool(trading_snapshot.get("source_ok")),
        )
        source_health = {
            "stock_info_cninfo_ok": bool(stock_info.get("_cninfo_ok")),
            "stock_info_em_ok": bool(stock_info.get("_em_ok")),
            "stock_info_ths_business_ok": bool(stock_info.get("_ths_business_ok")),
            "sector_board_available": bool(sector_data.get("items"))
            or bool(ths_industry_summary.get("source_ok"))
            or sector_snapshot.get("rank") is not None,
            "ths_industry_summary_ok": bool(ths_industry_summary.get("source_ok")),
            "ths_industry_name": ths_industry_summary.get("matched_name"),
            "ths_industry_code": ths_industry_summary.get("matched_code"),
            "sector_snapshot_fallback_from_flow": bool(sector_snapshot.get("fallback_from_flow")),
            "fund_flow_available": flow_item is not None or fund_flow_snapshot.get("net_flow") is not None,
            "fund_flow_has_net_flow": fund_flow_snapshot.get("net_flow") is not None,
            "fund_flow_source": fund_flow_source,
            "peer_source_ok": bool(peer_snapshot.get("source_ok", True)),
            "peer_source": peer_snapshot.get("source"),
            "lhb_available": bool(lhb_snapshot.get("source_ok")),
            "lhb_matched_recent": bool(lhb_snapshot.get("matched")),
            "trading_snapshot_available": bool(trading_snapshot.get("source_ok")),
            "trading_quote_source": trading_snapshot.get("quote_source"),
            "trading_kline_source": trading_snapshot.get("kline_source"),
            "stock_flow_available": bool(stock_flow_snapshot.get("source_ok")),
            "stock_flow_source": stock_flow_snapshot.get("source"),
            "stock_flow_window": stock_flow_snapshot.get("window"),
            "stock_focus_ready": bool(stock_focus_snapshot.get("focus_view")),
            "financial_statements_available": bool(
                financial_statements.get("balance_sheet")
                or financial_statements.get("income_statement")
                or financial_statements.get("cashflow")
            ),
            "financial_statements_source": financial_statements.get("source"),
            "shareholder_available": bool(
                shareholder_data.get("actual_controller")
                or shareholder_data.get("top10_holders")
                or shareholder_data.get("major_holder_changes")
            ),
            "shareholder_source_chain": list(_as_list(shareholder_data.get("source_chain"))),
            "valuation_source_chain": list(_as_list(valuation_data.get("source_chain"))),
            "news_available": len(_list_of_dicts(news_data.get("items"))) > 0,
            "announcements_available": len(_list_of_dicts(announcements_data.get("items"))) > 0,
            "research_available": len(_list_of_dicts(research_data.get("items"))) > 0,
            "social_available": (_safe_int(social_data.get("total_discussion")) or 0) > 0,
            "news_count": len(_list_of_dicts(news_data.get("items"))),
            "announcement_count": len(_list_of_dicts(announcements_data.get("items"))),
            "research_count": len(_list_of_dicts(research_data.get("items"))),
            "social_discussion_count": _safe_int(social_data.get("total_discussion")) or 0,
            "news_is_stale": bool(news_data.get("is_stale")),
            "research_is_stale": bool(research_data.get("is_stale")),
            "social_is_stale": bool(social_data.get("is_stale")),
            "news_source_chain": list(_as_list(news_data.get("source_chain"))),
            "research_source_chain": list(_as_list(research_data.get("source_chain"))),
            "news_errors": list(_as_list(news_data.get("errors"))),
            "research_errors": list(_as_list(research_data.get("errors"))),
            "social_errors": list(_as_list(social_data.get("errors"))),
        }
        evidence_pack = _prune_none(
            {
                "symbol": symbol,
                "stock_name": stock_name,
                "industry_name": industry_name,
                "generated_at": generated_at,
                "analysis_framework": {
                    "analysis_status_options": ["主线", "分支主线", "观察", "退潮", "非主线"],
                    "mainline_detector_items": [
                        {"criterion_id": "market_mainline_membership", "question": "当前是否属于市场主线或分支主线"},
                        {"criterion_id": "market_attention", "question": "是否具有足够市场与机构跟踪证据"},
                        {"criterion_id": "substantive_business_link", "question": "是否不是单纯概念映射"},
                        {"criterion_id": "actual_business_benefit", "question": "主营业务是否能够实际受益"},
                        {"criterion_id": "structural_drivers", "question": "是否存在政策、技术、需求或供给变化驱动"},
                        {"criterion_id": "medium_term_catalysts", "question": "未来6至12个月是否仍有可验证催化"},
                    ],
                    "industry_beta_detector_items": [
                        {"criterion_id": "industry_upcycle", "question": "行业是否处于上升周期"},
                        {"criterion_id": "three_year_space", "question": "未来三年空间是否明确"},
                        {"criterion_id": "competition_quality", "question": "是否不存在严重价格战或内卷"},
                        {"criterion_id": "structural_drivers", "question": "是否存在政策、技术、需求或供给变化驱动"},
                    ],
                },
                "stock_profile": {
                    "name": stock_name,
                    "industry": industry_name,
                    "market": stock_info.get("market"),
                    "listing_date": stock_info.get("listing_date"),
                    "main_business": main_business,
                    "product_type": stock_info.get("product_type"),
                    "product_name": stock_info.get("product_name"),
                    "profile": stock_info.get("profile"),
                },
                "mainline_context": {
                    "report_pending": bool(market_report.get("report_pending")),
                    "market_stage": market_evidence.get("market_stage") or market_report.get("market_stage") or {},
                    "current_mainlines": _list_of_dicts(market_report.get("current_mainlines"))[:4],
                    "future_mainlines": _list_of_dicts(market_report.get("future_mainlines"))[:4],
                    "current_theme_evidence": _list_of_dicts(market_evidence.get("current_themes"))[:4],
                    "future_theme_evidence": _list_of_dicts(market_evidence.get("next_themes"))[:4],
                    "policy_watchlist": _as_list(market_evidence.get("policy_watchlist"))[:8],
                },
                "company_specific_evidence": {
                    "announcements": _summarize_text_items(
                        _list_of_dicts(announcements_data.get("items")), summary_key="content", limit=6
                    ),
                    "news": _summarize_text_items(_list_of_dicts(news_data.get("items")), limit=6),
                    "research": [
                        {
                            "title": _normalize_text(item.get("title")),
                            "rating": _normalize_text(item.get("rating")),
                            "industry": _normalize_text(item.get("industry")),
                        }
                        for item in _list_of_dicts(research_data.get("items"))[:6]
                        if _normalize_text(item.get("title"))
                    ],
                    "risk_events": [
                        {
                            "title": _normalize_text(item.get("title")),
                            "summary": _normalize_text(item.get("summary")),
                            "date": _normalize_text(item.get("date")),
                            "source_type": _normalize_text(item.get("source_type")),
                        }
                        for item in _list_of_dicts(risk_data.get("items"))[:6]
                        if _normalize_text(item.get("title")) or _normalize_text(item.get("summary"))
                    ],
                    "financial_snapshot": evidence_pack.get("financial_snapshot"),
                    "financial_statements_snapshot": evidence_pack.get("financial_statements_snapshot"),
                    "shareholder_snapshot": evidence_pack.get("shareholder_snapshot"),
                    "product_type": stock_info.get("product_type"),
                    "product_name": stock_info.get("product_name"),
                    "trading_snapshot": lhb_snapshot,
                    "market_trading_snapshot": trading_snapshot,
                    "trading_signal_snapshot": trading_signal_snapshot,
                    "stock_flow_snapshot": stock_flow_snapshot,
                    "stock_focus_snapshot": stock_focus_snapshot,
                },
                "industry_beta_evidence": {
                    "sector_snapshot": sector_snapshot,
                    "fund_flow_snapshot": fund_flow_snapshot,
                    "peer_snapshot": peer_snapshot,
                },
                "supporting_judgement": {
                    "driver_clues": driver_clues,
                    "competition_clues": competition_clues,
                    "coverage_snapshot": {
                        "announcement_count": len(_list_of_dicts(announcements_data.get("items"))),
                        "news_count": len(_list_of_dicts(news_data.get("items"))),
                        "research_count": len(_list_of_dicts(research_data.get("items"))),
                        "positive_research_count": sentiment_snapshot.get("positive_research_count"),
                        "discussion_count": sentiment_snapshot.get("discussion_count"),
                    },
                    "risk_snapshot": risk_snapshot,
                    "data_quality": data_quality,
                    "source_health": source_health,
                },
            }
        )
        _emit(35, "已完成估值背景与行业对比整理", dict(evidence_pack))

        return {
            "stock_info": stock_info,
            "market_report": market_report,
            "market_evidence": market_evidence,
            "news_data": news_data,
            "announcements_data": announcements_data,
            "risk_data": risk_data,
            "research_data": research_data,
            "financials_data": financials_data,
            "sentiment_data": sentiment_data,
            "social_data": social_data,
            "valuation_data": valuation_data,
            "sector_snapshot": sector_snapshot,
            "fund_flow_snapshot": fund_flow_snapshot,
            "peer_snapshot": peer_snapshot,
            "valuation_snapshot": valuation_snapshot,
            "sentiment_snapshot": sentiment_snapshot,
            "risk_snapshot": risk_snapshot,
            "evidence_pack": evidence_pack,
        }

    def _build_model_report_prompts(self, evidence_pack: dict[str, Any]) -> tuple[str, str]:
        company_specific = _as_dict(evidence_pack.get("company_specific_evidence"))
        ordered_evidence_pack = {
            "analysis_framework": _as_dict(evidence_pack.get("analysis_framework")),
            "stock_focus_snapshot": _as_dict(company_specific.get("stock_focus_snapshot")),
            "stock_profile": _as_dict(evidence_pack.get("stock_profile")),
            "company_specific_evidence": {
                "stock_focus_snapshot": _as_dict(company_specific.get("stock_focus_snapshot")),
                "financial_snapshot": _as_dict(company_specific.get("financial_snapshot")),
                "financial_statements_snapshot": _as_dict(company_specific.get("financial_statements_snapshot")),
                "shareholder_snapshot": _as_dict(company_specific.get("shareholder_snapshot")),
                "product_type": company_specific.get("product_type"),
                "product_name": company_specific.get("product_name"),
                "trading_signal_snapshot": _as_dict(company_specific.get("trading_signal_snapshot")),
                "stock_flow_snapshot": _as_dict(company_specific.get("stock_flow_snapshot")),
                "market_trading_snapshot": _as_dict(company_specific.get("market_trading_snapshot")),
                "trading_snapshot": _as_dict(company_specific.get("trading_snapshot")),
                "announcements": _list_of_dicts(company_specific.get("announcements")),
                "news": _list_of_dicts(company_specific.get("news")),
                "research": _list_of_dicts(company_specific.get("research")),
                "risk_events": _list_of_dicts(company_specific.get("risk_events")),
            },
            "mainline_context": _as_dict(evidence_pack.get("mainline_context")),
            "industry_beta_evidence": _as_dict(evidence_pack.get("industry_beta_evidence")),
            "supporting_judgement": _as_dict(evidence_pack.get("supporting_judgement")),
        }
        system_prompt = (
            "你是A股行业周期分析模型。你的职责不是编故事，而是根据给定证据判断个股所在行业的景气度、"
            "是否处于市场主线、个股是否真实受益、以及未来6-12个月催化是否足够。\n"
            "注意：这是个股分析页，不是行业研究报告。你的重心必须落在“这只股票为什么受益/不受益、"
            "受益路径是否真实、公司业务和产业催化如何映射、哪些证据直接指向公司本身”上。\n"
            "你会看到按判定器整理过的证据包：个股聚焦摘要、公司直接证据、主线上下文、行业β证据、驱动线索、竞争线索、数据缺口。"
            "阅读顺序必须固定：先看 stock_focus_snapshot，再看 stock_profile 和 company_specific_evidence，最后才看 mainline_context 与 industry_beta_evidence。"
            "行业板块、资金流、同行样本只能作为背景约束，不能喧宾夺主。\n"
            "不要引入这套框架之外的自定义维度；判断时优先使用公司公告、主营业务、财务变化、股东结构、交易状态和直接新闻来论证个股，而不是只复述行业板块表现。\n"
            "必须只基于输入证据分析，不允许自行补事实，不允许联网补充。\n"
            "你必须输出严格JSON，不要输出markdown，不要输出额外解释。\n"
            "分析状态只能是：主线、分支主线、观察、退潮、非主线。\n"
            "受益级别只能是：核心受益、直接受益、边际受益、概念映射、待验证。\n"
            "周期阶段优先结合市场主线阶段、板块强度、资金流和风险变化判断。\n"
            "beneficiary_reason 必须写成公司受益路径，不要只写行业景气；"
            "core_logic 必须优先解释公司主营/子公司/产品与主线或产业逻辑的关系；"
            "killer_reason 必须优先指出公司层面的证伪点或缺口，而不是泛泛的行业描述。\n"
            "输出时先回答个股，再回答行业："
            "beneficiary_reason 第一段先讲这只股票的业务绑定和直接受益路径；"
            "core_logic 第一段先引用 stock_focus_snapshot.focus_view 或 company_specific_evidence 中的公司证据；"
            "只有第二层才允许补充行业β背景。"
            "如果你的 reasoning 主要建立在行业板块而不是公司证据上，说明你的分析方向错了。\n"
            "如果行业映射证据缺失或覆盖不足，必须在 killer_reason 或 checklist reason 中明确指出“证据不足”，"
            "不能把缺失数据伪装成负面结论。\n"
            "两个判定器的 checklist 必须逐项覆盖 analysis_framework 中给出的 criterion_id，"
            "不得增删、改名或按措辞重新匹配。\n"
            "每个 checklist 项都必须包含 criterion_id、item、passed、reason、source_refs；"
            "source_refs 必须指向证据包中的具体结构化字段或条目。\n"
            "JSON 结构必须包含："
            "analysis_status, beneficiary_level, beneficiary_reason, cycle_phase, cycle_phase_reason, prosperity_score, "
            "prosperity_judgement, core_logic, killer_reason, observation_window, catalysts, risks, observation_points, "
            "mainline_detector, industry_beta_detector。"
        )
        user_prompt = (
            "请严格按照以下顺序理解证据并输出行业周期分析 JSON：\n"
            "1. 先用 stock_focus_snapshot 判断这只股票自身是否真实受益、财务是否承压、股东和交易状态是否支持主线定位。\n"
            "2. 再用 stock_profile、announcements、news、research、financial_statements_snapshot、shareholder_snapshot、trading_signal_snapshot、stock_flow_snapshot 补充个股论证。\n"
            "3. 最后再用 mainline_context 和 industry_beta_evidence 判断行业背景与主线约束。\n"
            "4. 如果个股证据不足，就明确写证据不足，不要拿行业景气替代个股分析。\n"
            f"证据包：{json.dumps(ordered_evidence_pack, ensure_ascii=False)}"
        )
        return system_prompt, user_prompt

    def _build_llm_industry_cycle_report(
        self,
        *,
        symbol: str,
        evidence_bundle: dict[str, Any],
        evidence_pack: dict[str, Any],
        system_prompt: str,
        user_prompt: str,
        on_text: Optional[Callable[[str, dict[str, Any]], None]] = None,
    ) -> dict[str, Any]:
        from src.ai_caller import call_ai_structured
        from src.analyzer import get_analyzer

        analyzer = get_analyzer()
        if not getattr(analyzer, "is_available", lambda: False)():
            raise RuntimeError("模型服务当前不可用")

        accumulated_text = ""

        last_emitted_length = 0

        def _stream_callback(_delta_text: str, full_text: str) -> None:
            nonlocal last_emitted_length
            nonlocal accumulated_text
            accumulated_text = full_text
            if on_text and (len(full_text) - last_emitted_length >= 180 or len(full_text) < 180):
                last_emitted_length = len(full_text)
                on_text(
                    full_text,
                    _build_streaming_industry_cycle_draft(
                        full_text,
                        symbol=symbol,
                        evidence_pack=evidence_pack,
                    ),
                )

        response_text, model_used, _usage = call_ai_structured(
            analyzer,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            call_type="industry_cycle_report",
            temperature=0.2,
            max_tokens=8192,
            response_validator=lambda _text: None,
            stream=True,
            stream_text_callback=_stream_callback,
        )
        raw_text = accumulated_text or response_text
        parsed = _parse_llm_json_payload(raw_text, response_text)
        if not parsed:
            logger.warning("industry cycle model returned non-JSON payload; using draft fallback")
            draft_payload = _build_streaming_industry_cycle_draft(
                raw_text,
                symbol=symbol,
                evidence_pack=evidence_pack,
            )
            draft_cycle = _as_dict(draft_payload.get("industry_cycle"))
            supporting_judgement = _as_dict(evidence_pack.get("supporting_judgement"))
            fallback_payload = {
                "symbol": symbol,
                "industry_cycle": {
                    "stock_name": evidence_pack.get("stock_name") or symbol,
                    "industry_name": evidence_pack.get("industry_name") or "",
                    "analysis_status": draft_cycle.get("analysis_status") or "观察",
                    "beneficiary_level": draft_cycle.get("beneficiary_level"),
                    "beneficiary_reason": draft_cycle.get("beneficiary_reason")
                    or "模型返回了非标准 JSON，已保留原始输出供人工核查。",
                    "cycle_phase": draft_cycle.get("cycle_phase"),
                    "cycle_phase_reason": draft_cycle.get("cycle_phase_reason")
                    or "模型输出格式异常，周期阶段需结合原始输出复核。",
                    "prosperity_score": draft_cycle.get("prosperity_score") or 0,
                    "prosperity_judgement": draft_cycle.get("prosperity_judgement")
                    or "模型已返回文本，但结构化解析失败，请结合原始输出查看。",
                    "core_logic": draft_cycle.get("core_logic")
                    or "模型原始输出已保留，当前降级为仅展示证据包和原始流式结果。",
                    "killer_reason": "模型输出格式异常，未能稳定解析为 JSON。",
                    "observation_window": draft_cycle.get("observation_window") or "未来 6-12 个月",
                    "catalysts": _as_list(draft_cycle.get("catalysts")),
                    "risks": _as_list(draft_cycle.get("risks")),
                    "observation_points": _as_list(draft_cycle.get("observation_points")),
                    "mainline_detector": _as_dict(draft_cycle.get("mainline_detector"))
                    or {
                        "passed": False,
                        "conclusion": "",
                        "failed_reason": "模型输出格式异常，未能稳定解析。",
                        "checklist": [],
                    },
                    "industry_beta_detector": _as_dict(draft_cycle.get("industry_beta_detector"))
                    or {
                        "passed": False,
                        "conclusion": "",
                        "failed_reason": "模型输出格式异常，未能稳定解析。",
                        "checklist": [],
                    },
                    "evidence": _as_dict(draft_cycle.get("evidence"))
                    or {
                        "stock_focus_snapshot": _as_dict(
                            _as_dict(evidence_pack.get("company_specific_evidence")).get("stock_focus_snapshot")
                        ),
                        "market_mainline": {
                            "report_pending": True,
                            "market_stage": {},
                            "report_current_mainlines": _list_of_dicts(
                                _as_dict(evidence_pack.get("mainline_context")).get("current_mainlines")
                            ),
                            "report_future_mainlines": _list_of_dicts(
                                _as_dict(evidence_pack.get("mainline_context")).get("future_mainlines")
                            ),
                            "matched_current_mainlines": [],
                            "matched_future_mainlines": [],
                            "current_theme_detail": {},
                            "future_theme_detail": {},
                        },
                        "sector_snapshot": _as_dict(
                            _as_dict(evidence_pack.get("industry_beta_evidence")).get("sector_snapshot")
                        ),
                        "fund_flow": _as_dict(
                            _as_dict(evidence_pack.get("industry_beta_evidence")).get("fund_flow_snapshot")
                        ),
                        "peer_group": _as_dict(
                            _as_dict(evidence_pack.get("industry_beta_evidence")).get("peer_snapshot")
                        ),
                        "financial_snapshot": _as_dict(
                            _as_dict(evidence_pack.get("company_specific_evidence")).get("financial_snapshot")
                        ),
                        "valuation_snapshot": _as_dict(evidence_bundle.get("valuation_snapshot")),
                        "sentiment_snapshot": _as_dict(evidence_bundle.get("sentiment_snapshot")),
                        "risk_snapshot": _as_dict(evidence_bundle.get("risk_snapshot")),
                        "data_quality": _as_dict(supporting_judgement.get("data_quality")),
                        "driver_signals": _as_dict(supporting_judgement.get("driver_clues")),
                    },
                },
                "report_pending": False,
                "llm_used": True,
                "model_used": model_used,
                "raw_stream_output": raw_text,
                "raw_response": raw_text,
                "as_of_date": _current_report_as_of_date(),
                "debug_input": {
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                    "evidence_pack": evidence_pack,
                },
                "_fetched_at": datetime.now().isoformat(),
                "_cached": False,
                "fallback_used": True,
            }
            _persist_cache(symbol, fallback_payload)
            _persist_report_cache(symbol, fallback_payload)
            return fallback_payload
        mainline_detector = _validated_detector(
            parsed.get("mainline_detector"),
            _MAINLINE_CRITERION_IDS,
            label="主线判定器",
        )
        industry_beta_detector = _validated_detector(
            parsed.get("industry_beta_detector"),
            _INDUSTRY_BETA_CRITERION_IDS,
            label="行业β判定器",
        )
        market_report = _as_dict(evidence_bundle.get("market_report"))
        market_evidence = _as_dict(evidence_bundle.get("market_evidence"))
        parsed_market_mainline = _as_dict(_as_dict(parsed.get("evidence")).get("market_mainline"))
        current_theme_detail = _first_dict(market_evidence.get("current_themes"))
        future_theme_detail = _first_dict(market_evidence.get("next_themes"))
        parsed_incomplete = not (
            _normalize_text(parsed.get("analysis_status"))
            and _normalize_text(parsed.get("beneficiary_level"))
            and _normalize_text(parsed.get("cycle_phase"))
        )
        format_error_reason = "模型输出格式异常，未能稳定解析为完整 JSON。"

        final_payload = {
            "symbol": symbol,
            "as_of_date": _current_report_as_of_date(),
            "industry_cycle": {
                "stock_name": evidence_pack.get("stock_name") or symbol,
                "industry_name": evidence_pack.get("industry_name") or "",
                "analysis_status": _normalize_text(parsed.get("analysis_status")) or "观察",
                "beneficiary_level": _normalize_text(parsed.get("beneficiary_level")) or "待验证",
                "beneficiary_reason": _normalize_text(parsed.get("beneficiary_reason"))
                or "模型输出格式异常，主营受益路径需结合原始输出复核。",
                "cycle_phase": _normalize_text(parsed.get("cycle_phase")) or "观察期",
                "cycle_phase_reason": _normalize_text(parsed.get("cycle_phase_reason"))
                or "模型输出格式异常，周期阶段需结合原始输出复核。",
                "prosperity_score": parsed.get("prosperity_score") or 0,
                "prosperity_judgement": _normalize_text(parsed.get("prosperity_judgement"))
                or "模型已返回文本，但结构化解析不完整，请结合原始输出查看。",
                "core_logic": _normalize_text(parsed.get("core_logic"))
                or "模型输出格式异常，当前降级为仅展示证据包和原始输出。",
                "killer_reason": parsed.get("killer_reason") or (format_error_reason if parsed_incomplete else None),
                "observation_window": parsed.get("observation_window") or "未来 6-12 个月",
                "catalysts": [str(item) for item in _as_list(parsed.get("catalysts")) if str(item).strip()],
                "risks": [str(item) for item in _as_list(parsed.get("risks")) if str(item).strip()],
                "observation_points": [
                    str(item) for item in _as_list(parsed.get("observation_points")) if str(item).strip()
                ],
                "mainline_detector": {
                    "passed": bool(mainline_detector.get("passed")),
                    "conclusion": str(mainline_detector.get("conclusion") or ""),
                    "failed_reason": mainline_detector.get("failed_reason"),
                    "checklist": _list_of_dicts(mainline_detector.get("checklist")),
                },
                "industry_beta_detector": {
                    "passed": bool(industry_beta_detector.get("passed")),
                    "conclusion": str(industry_beta_detector.get("conclusion") or ""),
                    "failed_reason": industry_beta_detector.get("failed_reason"),
                    "checklist": _list_of_dicts(industry_beta_detector.get("checklist")),
                },
                "evidence": {
                    "stock_focus_snapshot": _as_dict(
                        _as_dict(evidence_pack.get("company_specific_evidence")).get("stock_focus_snapshot")
                    ),
                    "market_mainline": {
                        "report_pending": bool(market_report.get("report_pending")),
                        "market_stage": _as_dict(market_evidence.get("market_stage")),
                        "report_current_mainlines": _list_of_dicts(market_report.get("current_mainlines")),
                        "report_future_mainlines": _list_of_dicts(market_report.get("future_mainlines")),
                        "matched_current_mainlines": _list_of_dicts(
                            parsed_market_mainline.get("matched_current_mainlines")
                        ),
                        "matched_future_mainlines": _list_of_dicts(
                            parsed_market_mainline.get("matched_future_mainlines")
                        ),
                        "current_theme_detail": current_theme_detail,
                        "future_theme_detail": future_theme_detail,
                    },
                    "sector_snapshot": _as_dict(evidence_bundle.get("sector_snapshot")),
                    "fund_flow": _as_dict(evidence_bundle.get("fund_flow_snapshot")),
                    "peer_group": _as_dict(evidence_bundle.get("peer_snapshot")),
                    "valuation_snapshot": _as_dict(evidence_bundle.get("valuation_snapshot")),
                    "sentiment_snapshot": _as_dict(evidence_bundle.get("sentiment_snapshot")),
                    "risk_snapshot": _as_dict(evidence_bundle.get("risk_snapshot")),
                    "data_quality": _as_dict(_as_dict(evidence_pack.get("supporting_judgement")).get("data_quality")),
                    "driver_signals": _as_dict(_as_dict(evidence_pack.get("supporting_judgement")).get("driver_clues")),
                },
            },
            "report_pending": False,
            "llm_used": True,
            "model_used": model_used,
            "raw_stream_output": raw_text,
            "raw_response": raw_text,
            "debug_input": {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "evidence_pack": evidence_pack,
            },
            "_fetched_at": datetime.now().isoformat(),
            "_cached": False,
            "fallback_used": bool((evidence_bundle.get("market_report") or {}).get("report_pending"))
            or parsed_incomplete,
        }
        if on_text and raw_text and len(raw_text) != last_emitted_length:
            on_text(
                raw_text,
                {
                    "symbol": final_payload.get("symbol"),
                    "industry_cycle": final_payload.get("industry_cycle"),
                    "report_pending": False,
                    "llm_used": True,
                    "model_used": model_used,
                    "raw_stream_output": raw_text,
                    "raw_response": raw_text,
                    "debug_input": {
                        "system_prompt": system_prompt,
                        "user_prompt": user_prompt,
                        "evidence_pack": evidence_pack,
                    },
                    "_cached": False,
                    "fallback_used": bool((evidence_bundle.get("market_report") or {}).get("report_pending")),
                },
            )
        _persist_cache(symbol, final_payload)
        _persist_report_cache(symbol, final_payload)
        return final_payload
