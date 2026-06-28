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
    _CATALYST_KEYWORDS,
    _FADING_STAGE_KEYWORDS,
    _PRICE_WAR_KEYWORDS,
    _THREE_YEAR_SPACE_KEYWORDS,
    _as_dict,
    _as_list,
    _contains_any,
    _extract_competition_clues,
    _extract_driver_clues,
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
    _build_driver_signals,
    _build_streaming_industry_cycle_draft,
    _infer_beneficiary_level,
    _infer_cycle_phase,
    _is_usable_report_payload,
    _merge_detector_with_fallback,
    _pick_failed_reason,
    _report_has_conflicting_conclusion,
)

logger = logging.getLogger(__name__)


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
            if cached:
                cached["_cached"] = True
                return cached

        payload = self._build_payload(code, force=force)
        _persist_cache(code, payload)
        return payload

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

            # Reuse the same-day finalized snapshot as a degraded fallback before
            # telling the page to start a new model task. This keeps page/model
            # consumers aligned on one report object and avoids duplicate token burn.
            day_snapshot = _cache_get(code)
            if _is_usable_report_payload(day_snapshot):
                day_snapshot["_cached"] = True
                day_snapshot["report_pending"] = False
                _persist_report_cache(code, day_snapshot)
                return day_snapshot
            if day_snapshot:
                logger.info("industry cycle day snapshot is stale or incomplete for %s", code)

            rebuilt_payload = self._build_payload(code, force=False)
            if _is_usable_report_payload(rebuilt_payload):
                rebuilt_payload["_cached"] = False
                rebuilt_payload["report_pending"] = False
                _persist_cache(code, rebuilt_payload)
                _persist_report_cache(code, rebuilt_payload)
                return rebuilt_payload
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
                        "beneficiary_reason": _normalize_text(_as_dict(_as_dict(evidence_pack.get("company_specific_evidence")).get("stock_focus_snapshot")).get("focus_view")),
                        "cycle_phase": None,
                        "cycle_phase_reason": "",
                        "prosperity_score": 0,
                        "prosperity_judgement": "",
                        "core_logic": _normalize_text(_as_dict(_as_dict(evidence_pack.get("company_specific_evidence")).get("stock_focus_snapshot")).get("focus_view")),
                        "killer_reason": None,
                        "observation_window": "未来 6-12 个月",
                        "catalysts": [],
                        "risks": [],
                        "observation_points": [],
                        "mainline_detector": {"passed": False, "conclusion": "", "failed_reason": None, "checklist": []},
                        "industry_beta_detector": {"passed": False, "conclusion": "", "failed_reason": None, "checklist": []},
                        "evidence": {
                            "stock_focus_snapshot": _as_dict(_as_dict(evidence_pack.get("company_specific_evidence")).get("stock_focus_snapshot")),
                            "market_mainline": {
                                "report_pending": bool(_as_dict(evidence_pack.get("mainline_context")).get("report_pending")),
                                "market_stage": _as_dict(_as_dict(evidence_pack.get("mainline_context")).get("market_stage")),
                                "report_current_mainlines": _list_of_dicts(_as_dict(evidence_pack.get("mainline_context")).get("current_mainlines")),
                                "report_future_mainlines": _list_of_dicts(_as_dict(evidence_pack.get("mainline_context")).get("future_mainlines")),
                                "matched_current_mainlines": [],
                                "matched_future_mainlines": [],
                                "current_theme_detail": _first_dict(_as_dict(evidence_pack.get("mainline_context")).get("current_theme_evidence")),
                                "future_theme_detail": _first_dict(_as_dict(evidence_pack.get("mainline_context")).get("future_theme_evidence")),
                            },
                            "sector_snapshot": _as_dict(_as_dict(evidence_pack.get("industry_beta_evidence")).get("sector_snapshot")),
                            "fund_flow": _as_dict(_as_dict(evidence_pack.get("industry_beta_evidence")).get("fund_flow_snapshot")),
                            "peer_group": _as_dict(_as_dict(evidence_pack.get("industry_beta_evidence")).get("peer_snapshot")),
                            "financial_snapshot": _as_dict(_as_dict(evidence_pack.get("company_specific_evidence")).get("financial_snapshot")),
                            "valuation_snapshot": {},
                            "sentiment_snapshot": _as_dict(_as_dict(evidence_pack.get("supporting_judgement")).get("coverage_snapshot")),
                            "risk_snapshot": _as_dict(_as_dict(evidence_pack.get("supporting_judgement")).get("risk_snapshot")),
                            "data_quality": _as_dict(_as_dict(evidence_pack.get("supporting_judgement")).get("data_quality")),
                            "driver_signals": _as_dict(_as_dict(evidence_pack.get("supporting_judgement")).get("driver_clues")),
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
                "stream_text": "【证据闸门未通过】\n" + "\n".join(f"- {item}" for item in _as_list(evidence_gate.get("blocking_reasons"))),
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
                "stock_info": _prune_none({
                    "name": stock_name,
                    "industry": industry_name,
                    "market": stock_info.get("market"),
                    "listing_date": stock_info.get("listing_date"),
                    "main_business": main_business,
                    "product_type": stock_info.get("product_type"),
                    "product_name": stock_info.get("product_name"),
                    "profile": stock_info.get("profile"),
                }),
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
        evidence_pack["financial_snapshot"] = _prune_none({
            "latest_report_date": latest_financial.get("report_date"),
            "revenue": latest_financial.get("revenue"),
            "revenue_yoy": latest_financial.get("revenue_yoy"),
            "net_profit": latest_financial.get("net_profit"),
            "net_profit_yoy": latest_financial.get("net_profit_yoy"),
            "roe": latest_financial.get("roe"),
            "gross_margin": latest_financial.get("gross_margin"),
            "debt_ratio": latest_financial.get("debt_ratio"),
            "eps": latest_financial.get("eps"),
        })
        _emit(26, "已汇总核心财务摘要", dict(evidence_pack))

        financial_statements = _as_dict(get_financial_statements(symbol=symbol, periods=8, force=force))
        latest_balance = _first_dict(financial_statements.get("balance_sheet"))
        latest_income = _first_dict(financial_statements.get("income_statement"))
        latest_cashflow = _first_dict(financial_statements.get("cashflow"))
        evidence_pack["financial_statements_snapshot"] = _prune_none({
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
        })
        _emit(28, "已汇总三大财报明细摘要", dict(evidence_pack))

        shareholder_data = _as_dict(get_shareholder_structure(symbol=symbol, force=force))
        evidence_pack["shareholder_snapshot"] = _prune_none({
            "actual_controller": shareholder_data.get("actual_controller"),
            "holder_count": shareholder_data.get("holder_count"),
            "holder_count_change_pct": shareholder_data.get("holder_count_change_pct"),
            "institution_holding_pct": shareholder_data.get("institution_holding_pct"),
            "top10_holders": _list_of_dicts(shareholder_data.get("top10_holders"))[:5],
            "major_holder_changes": _list_of_dicts(shareholder_data.get("major_holder_changes"))[:6],
            "source_chain": _as_list(shareholder_data.get("source_chain")),
            "errors": _as_list(shareholder_data.get("errors")),
        })
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

        company_texts = [
            main_business,
            *[
                f"{_normalize_text(item.get('title'))} {_normalize_text(item.get('summary'))}"
                for item in _list_of_dicts(news_data.get("items"))[:8]
            ],
            *[
                f"{_normalize_text(item.get('title'))} {_normalize_text(item.get('summary') or item.get('content'))}"
                for item in _list_of_dicts(announcements_data.get("items"))[:8]
            ],
            *[
                f"{_normalize_text(item.get('title'))} {_normalize_text(item.get('industry'))} {_normalize_text(item.get('rating'))}"
                for item in _list_of_dicts(research_data.get("items"))[:6]
            ],
            *[
                f"{_normalize_text(item.get('title'))} {_normalize_text(item.get('risk_summary'))}"
                for item in _list_of_dicts(risk_data.get("items"))[:6]
            ],
            _normalize_text(_as_dict(evidence_pack.get("shareholder_snapshot")).get("actual_controller")),
            *[
                f"{_normalize_text(item.get('holder'))} {_normalize_text(item.get('direction'))} {item.get('pct') or ''}"
                for item in _list_of_dicts(_as_dict(evidence_pack.get("shareholder_snapshot")).get("major_holder_changes"))[:6]
            ],
            _normalize_text(stock_focus_snapshot.get("focus_view")),
            " ".join(_as_list(stock_focus_snapshot.get("finance_points"))[:4]),
            " ".join(_as_list(stock_focus_snapshot.get("holder_points"))[:4]),
            " ".join(_as_list(stock_focus_snapshot.get("trading_points"))[:4]),
            " ".join(
                str(part) for part in [
                    _as_dict(trading_snapshot.get("latest_quote")).get("change_pct"),
                    _as_dict(trading_snapshot.get("latest_quote")).get("volume_ratio"),
                    _as_dict(trading_snapshot.get("latest_quote")).get("turnover_rate"),
                    _as_dict(trading_snapshot.get("momentum")).get("pct_chg_20d"),
                    _as_dict(trading_snapshot.get("momentum")).get("pct_chg_60d"),
                    _as_dict(stock_flow_snapshot).get("stage_change_pct"),
                    _as_dict(stock_flow_snapshot).get("continuous_turnover_rate"),
                    _as_dict(stock_flow_snapshot).get("net_inflow"),
                ] if part not in (None, "")
            ),
            " ".join(
                str(part) for part in [
                    latest_income.get("revenue_yoy"),
                    latest_income.get("parent_net_profit_yoy"),
                    latest_income.get("research_expense"),
                    latest_cashflow.get("capex"),
                    latest_balance.get("contract_liabilities"),
                ] if part not in (None, "")
            ),
            _normalize_text(trading_signal_snapshot.get("trend_stage")),
            _normalize_text(trading_signal_snapshot.get("trend_reason")),
            " ".join(_as_list(trading_signal_snapshot.get("risk_flags"))[:4]),
            " ".join(_as_list(trading_signal_snapshot.get("positive_flags"))[:4]),
        ]
        driver_clues = _extract_driver_clues(*company_texts)
        competition_clues = _extract_competition_clues(*company_texts)

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
        sentiment_snapshot = _prune_none({
            "news_count": len(news_data.get("items") or []),
            "research_count": len(research_data.get("items") or []),
            "positive_research_count": sum(
                1 for item in (research_data.get("items") or [])
                if _contains_any(item.get("rating"), ("买入", "增持", "推荐", "优于大市", "强烈推荐"))
            ),
            "sentiment_score": _safe_float(sentiment_data.get("sentiment_score")) or 0.0,
            "social_score": _safe_float(social_data.get("overall_score")) or 0.0,
            "discussion_count": _safe_int(social_data.get("total_discussion")) or 0,
        })
        risk_snapshot = _prune_none({
            "high_risk_count": _safe_int((risk_data.get("analysis") or {}).get("severity_distribution", {}).get("high")) or 0,
            "medium_risk_count": _safe_int((risk_data.get("analysis") or {}).get("severity_distribution", {}).get("medium")) or 0,
            "top_risk_labels": ((risk_data.get("analysis") or {}).get("top_risk_labels") or [])[:5],
        })
        evidence_pack["sentiment_snapshot"] = sentiment_snapshot
        evidence_pack["risk_snapshot"] = risk_snapshot
        _emit(31, "已完成舆情、社交情绪和风险摘要", dict(evidence_pack))

        sector_snapshot = _prune_none({
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
            "source": (board_item or {}).get("_source") or ("ths_industry_summary" if ths_board_summary else "sector_endpoint"),
        })
        fund_flow_source = (
            (flow_item or {}).get("_source")
            or ("macro_sector_flow" if flow_item else ("ths_industry_summary" if ths_board_summary else "macro_sector_flow"))
        )
        fund_flow_snapshot = _prune_none({
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
            "total_amount": _safe_float((flow_item or {}).get("total_amount")) or _safe_float((board_item or {}).get("total_amount")),
            "up_count": _safe_int((flow_item or {}).get("up_count")),
            "down_count": _safe_int((flow_item or {}).get("down_count")),
            "leading_stock": (flow_item or {}).get("leading_stock") or (board_item or {}).get("lead_stock"),
            "source": fund_flow_source,
        })
        evidence_pack["sector_snapshot"] = sector_snapshot
        evidence_pack["fund_flow"] = fund_flow_snapshot
        evidence_pack["peer_group"] = peer_snapshot
        _emit(33, "已完成行业板块、资金流和同行样本整理", dict(evidence_pack))

        valuation_snapshot = _prune_none({
            "pe_ttm": _safe_float((valuation_data or {}).get("pe_ttm")),
            "pb": _safe_float((valuation_data or {}).get("pb")),
            "industry_name": industry_average.get("industry"),
            "industry_pe": _safe_float(industry_average.get("pe")),
            "industry_pb": _safe_float(industry_average.get("pb")),
            "industry_sample_size": _safe_int(industry_average.get("sample_size")),
            "pe_premium_vs_industry": _safe_float(((valuation_signal.get("metrics") or {}).get("pe_premium_vs_industry"))),
            "pb_premium_vs_industry": _safe_float(((valuation_signal.get("metrics") or {}).get("pb_premium_vs_industry"))),
            "price_overdraft_status": _normalize_text(valuation_signal.get("status")) or None,
            "price_overdraft_score": _safe_float(valuation_signal.get("score")),
            "reasoning": (valuation_signal.get("reasoning") or [])[:5],
        })
        if _safe_int(peer_snapshot.get("sample_size")) in (0, None) and _safe_int(industry_average.get("sample_size")) not in (0, None):
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
            board_source_ok=bool(sector_data.get("items")) or flow_item is not None or bool(ths_industry_summary.get("source_ok")),
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
            "sector_board_available": bool(sector_data.get("items")) or bool(ths_industry_summary.get("source_ok")) or sector_snapshot.get("rank") is not None,
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
        evidence_pack = _prune_none({
            "symbol": symbol,
            "stock_name": stock_name,
            "industry_name": industry_name,
            "generated_at": generated_at,
            "analysis_framework": {
                "analysis_status_options": ["主线", "分支主线", "观察", "退潮", "非主线"],
                "mainline_detector_items": [
                    "当前属于市场主线 / 分支主线",
                    "不是冷门低估股",
                    "不是单纯蹭概念",
                    "主营业务能实际受益",
                    "有政策 / 技术 / 需求 / 供给变化驱动",
                    "未来 6-12 个月仍有催化",
                ],
                "industry_beta_detector_items": [
                    "行业处于上升周期",
                    "未来 3 年空间明确",
                    "不是严重价格战 / 内卷行业",
                    "有政策 / 技术 / 需求 / 供给变化驱动",
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
                "announcements": _summarize_text_items(_list_of_dicts(announcements_data.get("items")), summary_key="content", limit=6),
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
                        "risk_summary": _normalize_text(item.get("risk_summary")),
                        "severity": _normalize_text(item.get("severity")),
                    }
                    for item in _list_of_dicts(risk_data.get("items"))[:6]
                    if _normalize_text(item.get("title")) or _normalize_text(item.get("risk_summary"))
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
        })
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
            "主线属性判定器 checklist 必须覆盖以下6项："
            "当前属于市场主线 / 分支主线；不是冷门低估股；不是单纯蹭概念；主营业务能实际受益；"
            "有政策 / 技术 / 需求 / 供给变化驱动；未来 6-12 个月仍有催化。\n"
            "行业β判定器 checklist 必须覆盖以下4项："
            "行业处于上升周期；未来 3 年空间明确；不是严重价格战 / 内卷行业；"
            "有政策 / 技术 / 需求 / 供给变化驱动。\n"
            "每个 checklist 项都必须包含 item、passed、reason、source。\n"
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
            if on_text and (
                len(full_text) - last_emitted_length >= 180
                or len(full_text) < 180
            ):
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
                    "beneficiary_reason": draft_cycle.get("beneficiary_reason") or "模型返回了非标准 JSON，已保留原始输出供人工核查。",
                    "cycle_phase": draft_cycle.get("cycle_phase"),
                    "cycle_phase_reason": draft_cycle.get("cycle_phase_reason") or "模型输出格式异常，周期阶段需结合原始输出复核。",
                    "prosperity_score": draft_cycle.get("prosperity_score") or 0,
                    "prosperity_judgement": draft_cycle.get("prosperity_judgement") or "模型已返回文本，但结构化解析失败，请结合原始输出查看。",
                    "core_logic": draft_cycle.get("core_logic") or "模型原始输出已保留，当前降级为仅展示证据包和原始流式结果。",
                    "killer_reason": "模型输出格式异常，未能稳定解析为 JSON。",
                    "observation_window": draft_cycle.get("observation_window") or "未来 6-12 个月",
                    "catalysts": _as_list(draft_cycle.get("catalysts")),
                    "risks": _as_list(draft_cycle.get("risks")),
                    "observation_points": _as_list(draft_cycle.get("observation_points")),
                    "mainline_detector": _as_dict(draft_cycle.get("mainline_detector")) or {
                        "passed": False,
                        "conclusion": "",
                        "failed_reason": "模型输出格式异常，未能稳定解析。",
                        "checklist": [],
                    },
                    "industry_beta_detector": _as_dict(draft_cycle.get("industry_beta_detector")) or {
                        "passed": False,
                        "conclusion": "",
                        "failed_reason": "模型输出格式异常，未能稳定解析。",
                        "checklist": [],
                    },
                    "evidence": _as_dict(draft_cycle.get("evidence")) or {
                        "stock_focus_snapshot": _as_dict(_as_dict(evidence_pack.get("company_specific_evidence")).get("stock_focus_snapshot")),
                        "market_mainline": {
                            "report_pending": True,
                            "market_stage": {},
                            "report_current_mainlines": _list_of_dicts(_as_dict(evidence_pack.get("mainline_context")).get("current_mainlines")),
                            "report_future_mainlines": _list_of_dicts(_as_dict(evidence_pack.get("mainline_context")).get("future_mainlines")),
                            "matched_current_mainlines": [],
                            "matched_future_mainlines": [],
                            "current_theme_detail": {},
                            "future_theme_detail": {},
                        },
                        "sector_snapshot": _as_dict(_as_dict(evidence_pack.get("industry_beta_evidence")).get("sector_snapshot")),
                        "fund_flow": _as_dict(_as_dict(evidence_pack.get("industry_beta_evidence")).get("fund_flow_snapshot")),
                        "peer_group": _as_dict(_as_dict(evidence_pack.get("industry_beta_evidence")).get("peer_snapshot")),
                        "financial_snapshot": _as_dict(_as_dict(evidence_pack.get("company_specific_evidence")).get("financial_snapshot")),
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
        mainline_detector = _as_dict(parsed.get("mainline_detector"))
        industry_beta_detector = _as_dict(parsed.get("industry_beta_detector"))
        fallback_payload: dict[str, Any] = {}
        fallback_cycle: dict[str, Any] = {}
        if not _list_of_dicts(mainline_detector.get("checklist")) or not _list_of_dicts(industry_beta_detector.get("checklist")):
            try:
                fallback_payload = self._build_payload(symbol, force=False)
                _persist_cache(symbol, fallback_payload)
            except Exception:
                logger.exception("industry cycle detector fallback build failed for %s", symbol)
                fallback_payload = {}
            fallback_cycle = _as_dict(fallback_payload.get("industry_cycle"))
            mainline_detector = _merge_detector_with_fallback(
                mainline_detector,
                _as_dict(fallback_cycle.get("mainline_detector")),
            )
            industry_beta_detector = _merge_detector_with_fallback(
                industry_beta_detector,
                _as_dict(fallback_cycle.get("industry_beta_detector")),
            )
        market_report = _as_dict(evidence_bundle.get("market_report"))
        market_evidence = _as_dict(evidence_bundle.get("market_evidence"))
        parsed_market_mainline = _as_dict(_as_dict(parsed.get("evidence")).get("market_mainline"))
        current_theme_detail = _first_dict(market_evidence.get("current_themes"))
        future_theme_detail = _first_dict(market_evidence.get("next_themes"))
        stock_info = _as_dict(evidence_bundle.get("stock_info"))
        stock_focus_summary = self._build_stock_focus_summary(
            stock_name=_normalize_text(evidence_pack.get("stock_name") or symbol),
            main_business=_normalize_text(stock_info.get("main_business")),
            beneficiary_level=_normalize_text(parsed.get("beneficiary_level")),
            beneficiary_reason=_normalize_text(parsed.get("beneficiary_reason")),
            news_items=_list_of_dicts(evidence_bundle.get("news_data", {}).get("items") if isinstance(evidence_bundle.get("news_data"), dict) else []),
            research_items=_list_of_dicts(evidence_bundle.get("research_data", {}).get("items") if isinstance(evidence_bundle.get("research_data"), dict) else []),
        )
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
                "beneficiary_reason": _normalize_text(parsed.get("beneficiary_reason")) or "模型输出格式异常，主营受益路径需结合原始输出复核。",
                "cycle_phase": _normalize_text(parsed.get("cycle_phase")) or "观察期",
                "cycle_phase_reason": _normalize_text(parsed.get("cycle_phase_reason")) or "模型输出格式异常，周期阶段需结合原始输出复核。",
                "prosperity_score": parsed.get("prosperity_score") or 0,
                "prosperity_judgement": _normalize_text(parsed.get("prosperity_judgement")) or "模型已返回文本，但结构化解析不完整，请结合原始输出查看。",
                "core_logic": stock_focus_summary or _normalize_text(parsed.get("core_logic")) or "模型输出格式异常，当前降级为仅展示证据包和原始输出。",
                "killer_reason": parsed.get("killer_reason") or (format_error_reason if parsed_incomplete else None),
                "observation_window": parsed.get("observation_window") or "未来 6-12 个月",
                "catalysts": [str(item) for item in _as_list(parsed.get("catalysts")) if str(item).strip()],
                "risks": [str(item) for item in _as_list(parsed.get("risks")) if str(item).strip()],
                "observation_points": [str(item) for item in _as_list(parsed.get("observation_points")) if str(item).strip()],
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
                    "stock_focus_snapshot": _as_dict(_as_dict(evidence_pack.get("company_specific_evidence")).get("stock_focus_snapshot")),
                    "market_mainline": {
                        "report_pending": bool(market_report.get("report_pending")),
                        "market_stage": _as_dict(market_evidence.get("market_stage")),
                        "report_current_mainlines": _list_of_dicts(market_report.get("current_mainlines")),
                        "report_future_mainlines": _list_of_dicts(market_report.get("future_mainlines")),
                        "matched_current_mainlines": _list_of_dicts(parsed_market_mainline.get("matched_current_mainlines")),
                        "matched_future_mainlines": _list_of_dicts(parsed_market_mainline.get("matched_future_mainlines")),
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
            "fallback_used": bool((evidence_bundle.get("market_report") or {}).get("report_pending")) or parsed_incomplete,
        }
        if _report_has_conflicting_conclusion(final_payload):
            if not fallback_payload:
                try:
                    fallback_payload = self._build_payload(symbol, force=False)
                    _persist_cache(symbol, fallback_payload)
                except Exception:
                    logger.exception("industry cycle consistency fallback build failed for %s", symbol)
                    fallback_payload = {}
            fallback_cycle = _as_dict(fallback_payload.get("industry_cycle"))
            if fallback_cycle:
                logger.info("industry cycle final report conflicted with detector/evidence for %s, fallback applied", symbol)
                final_payload = {
                    **fallback_payload,
                    "symbol": symbol,
                    "as_of_date": _current_report_as_of_date(),
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
                    "fallback_used": True,
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

    def _build_payload(self, symbol: str, *, force: bool) -> dict[str, Any]:
        from api.v1.endpoints.financials import (
            get_valuation_ratios,
            get_research_report,
            get_risk_events,
            get_sentiment,
            get_social_sentiment,
            search_news,
        )
        from api.v1.endpoints.macro import _fetch_sector_flow_industry
        from api.v1.endpoints.sectors import get_sector_list
        from api.v1.endpoints.stock_info import get_stock_info
        from src.services.market_theme_service import MarketThemeService

        stock_info = _as_dict(get_stock_info(symbol=symbol, force=force))
        if not stock_info.get("_ths_business_ok") and not _normalize_text(stock_info.get("product_type")):
            refreshed_stock_info = _as_dict(get_stock_info(symbol=symbol, force=True))
            if refreshed_stock_info:
                stock_info = refreshed_stock_info
        industry_name = _normalize_text(stock_info.get("industry"))
        stock_name = _normalize_text(stock_info.get("short_name") or stock_info.get("name") or symbol)
        main_business = _normalize_text(stock_info.get("main_business"))

        news_data = _as_dict(search_news(symbol=symbol, days=90, source="all", force=force))
        risk_data = _as_dict(get_risk_events(symbol=symbol, days=180, force=force))
        research_data = _as_dict(get_research_report(symbol=symbol, days=1095, force=force))
        sentiment_data = _as_dict(get_sentiment(symbol=symbol, days=90, force=force))
        social_data = _as_dict(get_social_sentiment(symbol=symbol, days=90, force=force))

        market_theme_service = MarketThemeService()
        market_report = _as_dict(market_theme_service.get_model_report(force=False))
        market_evidence = _as_dict(market_theme_service.get_evidence(force=False))
        sector_data = _as_dict(get_sector_list(type="industry", force=force))
        flow_records = _list_of_dicts(_fetch_sector_flow_industry())
        valuation_data = _as_dict(get_valuation_ratios(symbol=symbol, with_history=True, force=force))
        board_item, board_rank, board_total = _find_industry_board(industry_name, sector_data.get("items") or [])
        flow_item, flow_rank, flow_total = _find_sector_flow(industry_name, flow_records)
        peer_snapshot = _fetch_peer_snapshot(industry_name)
        if board_item is None and flow_item is not None:
            board_item = _fallback_sector_item_from_flow(flow_item)
            board_rank = flow_rank
            board_total = flow_total

        news_items = news_data.get("items") or []
        risk_items = risk_data.get("items") or []
        research_items = research_data.get("items") or []
        current_matches: list[dict[str, Any]] = []
        future_matches: list[dict[str, Any]] = []

        driver_texts = [
            main_business,
            *[f"{item.get('title', '')} {item.get('summary', '')}" for item in news_items[:20]],
            *[f"{item.get('title', '')} {item.get('industry', '')} {item.get('rating', '')}" for item in research_items[:20]],
            *[f"{item.get('title', '')} {item.get('risk_summary', '')}" for item in risk_items[:20]],
        ]
        driver_signals = _build_driver_signals(driver_texts)
        driver_pass = bool(driver_signals)
        price_war_detected = _contains_any(" ".join(driver_texts), _PRICE_WAR_KEYWORDS)

        sentiment_score = _safe_float(sentiment_data.get("sentiment_score")) or 0.0
        social_score = _safe_float(social_data.get("overall_score")) or 0.0
        total_discussion = _safe_int(social_data.get("total_discussion")) or 0
        positive_research = sum(
            1 for item in research_items
            if _contains_any(item.get("rating"), ("买入", "增持", "推荐", "优于大市", "强烈推荐"))
        )
        catalyst_hits = [
            word for word in _CATALYST_KEYWORDS
            if _contains_any(" ".join(driver_texts), [word])
        ]
        high_risk_count = _safe_int((risk_data.get("analysis") or {}).get("severity_distribution", {}).get("high")) or 0
        medium_risk_count = _safe_int((risk_data.get("analysis") or {}).get("severity_distribution", {}).get("medium")) or 0
        sector_change = _safe_float((board_item or {}).get("change_pct"))
        flow_amount = _safe_float((flow_item or {}).get("main_net_inflow"))
        matched_current = current_matches[0] if current_matches else None
        matched_future = future_matches[0] if future_matches else None
        current_theme_detail = _pick_theme_detail(market_evidence.get("current_themes") or [], matched_current)
        future_theme_detail = _pick_theme_detail(market_evidence.get("next_themes") or [], matched_future)
        current_stage = _normalize_text((matched_current or {}).get("stage"))
        beneficiary_level, beneficiary_reason = _infer_beneficiary_level(
            matched_current=matched_current,
            matched_future=matched_future,
        )
        valuation_signal = (valuation_data or {}).get("price_overdraft_signal") or {}
        valuation_status = _normalize_text(valuation_signal.get("status"))
        valuation_score = _safe_float(valuation_signal.get("score"))
        industry_average = (valuation_data or {}).get("industry_average") or {}
        pe_ttm = _safe_float((valuation_data or {}).get("pe_ttm"))
        pb = _safe_float((valuation_data or {}).get("pb"))
        pe_premium_vs_industry = _safe_float(((valuation_signal.get("metrics") or {}).get("pe_premium_vs_industry")))
        pb_premium_vs_industry = _safe_float(((valuation_signal.get("metrics") or {}).get("pb_premium_vs_industry")))
        if _safe_int(peer_snapshot.get("sample_size")) in (0, None) and _safe_int(industry_average.get("sample_size")) not in (0, None):
            peer_snapshot = {
                **peer_snapshot,
                "sample_size": _safe_int(industry_average.get("sample_size")),
                "source": peer_snapshot.get("source") or "valuation_industry_average_fallback",
            }

        mainline_checklist = [
            {
                "item": "当前属于市场主线 / 分支主线",
                "passed": bool(matched_current),
                "reason": (
                    f"命中当前主线“{matched_current['name']}”，阶段 {matched_current.get('stage')}"
                    if matched_current else "本地不再用关键词匹配市场主线，需由模型基于市场主线报告与公司主营资料判断"
                ),
                "source": "市场主线报告 / 证据层",
            },
            {
                "item": "不是冷门低估股",
                "passed": positive_research >= 2 or len(news_items) >= 6 or total_discussion >= 80,
                "reason": f"研报 {len(research_items)} 篇，新闻 {len(news_items)} 条，讨论 {total_discussion} 条，说明个股本身并非完全缺乏跟踪。",
                "source": "研报 / 新闻 / 社交讨论",
            },
            {
                "item": "不是单纯蹭概念",
                "passed": False,
                "reason": (
                    f"受益级别为“{beneficiary_level}”，{beneficiary_reason}"
                    if beneficiary_level not in {"概念映射", "待验证"}
                    else "本地不再用关键词判断是否蹭概念，需要由模型结合主营、公告、研报和市场主线报告确认"
                ),
                "source": "主营业务 / 主线分支 / 新闻 / 研报",
            },
            {
                "item": "主营业务能实际受益",
                "passed": beneficiary_level in {"核心受益", "直接受益"},
                "reason": (
                    f"{beneficiary_reason} 主营业务：{main_business[:72]}"
                    if main_business else "主营业务信息缺失，无法确认真实受益路径"
                ),
                "source": "公司资料 / 主线分支映射",
            },
            {
                "item": "有政策 / 技术 / 需求 / 供给变化驱动",
                "passed": driver_pass,
                "reason": (
                    "驱动信号：" + " / ".join(f"{k}:{','.join(v)}" for k, v in driver_signals.items())
                    if driver_signals else "近端资讯中未聚合出足够强的产业驱动"
                ),
                "source": "新闻 / 研报 / 风险事件",
            },
            {
                "item": "未来 6-12 个月仍有催化",
                "passed": len(catalyst_hits) >= 2 or positive_research >= 2 or bool(matched_future),
                "reason": (
                    f"催化词命中 {', '.join(catalyst_hits[:4])}"
                    if catalyst_hits else (
                        f"候选主线为“{matched_future['name']}”"
                        if matched_future else "中期催化线索不足"
                    )
                ),
                "source": "新闻 / 研报 / 主线候选",
            },
        ]
        mainline_passed = all(item["passed"] for item in mainline_checklist)

        beta_checklist = [
            {
                "item": "行业处于上升周期",
                "passed": (
                    (sector_change is not None and sector_change > 0)
                    or (board_rank is not None and board_rank <= 15)
                ) and not _contains_any(current_stage, _FADING_STAGE_KEYWORDS),
                "reason": (
                    f"板块涨跌幅 {sector_change if sector_change is not None else 'N/A'}%，排名 {board_rank or 'N/A'}/{board_total or 'N/A'}"
                ),
                "source": "行业板块表现 / 主线阶段",
            },
            {
                "item": "未来 3 年空间明确",
                "passed": positive_research >= 2 or _contains_any(" ".join(driver_texts), _THREE_YEAR_SPACE_KEYWORDS),
                "reason": (
                    f"研报覆盖 {len(research_items)} 篇，正向评级 {positive_research} 篇"
                    if research_items else "长期空间论证不足"
                ),
                "source": "研报 / 行业叙事 / 候选主线",
            },
            {
                "item": "不是严重价格战 / 内卷行业",
                "passed": not price_war_detected,
                "reason": "未检测到明显价格战信号" if not price_war_detected else "资讯中出现价格战 / 内卷信号",
                "source": "新闻 / 风险事件",
            },
            {
                "item": "有政策 / 技术 / 需求 / 供给变化驱动",
                "passed": driver_pass,
                "reason": (
                    "驱动信号：" + " / ".join(f"{k}:{','.join(v)}" for k, v in driver_signals.items())
                    if driver_signals else "驱动因素不足"
                ),
                "source": "新闻 / 研报 / 风险事件",
            },
        ]
        beta_passed = all(item["passed"] for item in beta_checklist)

        prosperity_score = 50
        prosperity_score += 12 if mainline_passed else 0
        prosperity_score += 12 if beta_passed else 0
        prosperity_score += 8 if matched_current else 0
        prosperity_score += 6 if flow_amount and flow_amount > 0 else 0
        prosperity_score += 6 if sector_change and sector_change > 0 else 0
        prosperity_score += 4 if positive_research >= 2 else 0
        prosperity_score += 4 if len(catalyst_hits) >= 2 else 0
        prosperity_score += 4 if beneficiary_level == "核心受益" else 0
        prosperity_score += 2 if beneficiary_level == "直接受益" else 0
        prosperity_score -= 10 if price_war_detected else 0
        prosperity_score -= 10 if high_risk_count > 0 else 0
        prosperity_score -= 6 if medium_risk_count >= 3 else 0
        prosperity_score -= 6 if valuation_status in {"high", "medium"} else 0
        prosperity_score = max(0, min(100, prosperity_score))

        if mainline_passed and beta_passed:
            if matched_current and int(matched_current.get("rank") or 99) == 1 and not _contains_any(current_stage, _FADING_STAGE_KEYWORDS):
                analysis_status = "主线"
            else:
                analysis_status = "分支主线"
        elif matched_current and (_contains_any(current_stage, _FADING_STAGE_KEYWORDS) or price_war_detected or high_risk_count > 0):
            analysis_status = "退潮"
        elif matched_future or driver_pass or positive_research > 0:
            analysis_status = "观察"
        else:
            analysis_status = "非主线"

        killer_reason = (
            _pick_failed_reason(mainline_checklist)
            if not mainline_passed
            else _pick_failed_reason(beta_checklist)
        )
        cycle_phase, cycle_phase_reason = _infer_cycle_phase(
            analysis_status=analysis_status,
            current_theme_detail=current_theme_detail,
            matched_current=matched_current,
            sector_change=sector_change,
            flow_amount=flow_amount,
            news_count=len(news_items),
        )

        prosperity_judgement = self._build_prosperity_judgement(
            analysis_status=analysis_status,
            industry_name=industry_name,
            matched_current=matched_current,
            matched_future=matched_future,
            cycle_phase=cycle_phase,
            sector_change=sector_change,
            board_rank=board_rank,
            board_total=board_total,
            flow_amount=flow_amount,
            high_risk_count=high_risk_count,
            price_war_detected=price_war_detected,
        )
        core_logic = self._build_core_logic(
            driver_signals=driver_signals,
            matched_current=matched_current,
            matched_future=matched_future,
            price_war_detected=price_war_detected,
            main_business=main_business,
            beneficiary_level=beneficiary_level,
        )
        stock_focus_summary = self._build_stock_focus_summary(
            stock_name=stock_name,
            main_business=main_business,
            beneficiary_level=beneficiary_level,
            beneficiary_reason=beneficiary_reason,
            news_items=news_items,
            research_items=research_items,
        )
        data_quality = _build_data_quality(
            board_rank=board_rank,
            flow_rank=flow_rank,
            research_count=len(research_items),
            discussion_count=total_discussion,
            peer_sample_size=_safe_int((peer_snapshot or {}).get("sample_size")),
            board_source_ok=bool(sector_data.get("items")) or flow_item is not None,
            peer_source_ok=bool(peer_snapshot.get("source_ok", True)),
        )

        catalysts = self._build_catalysts(catalyst_hits, matched_future)
        risks = self._build_risks(
            price_war_detected,
            high_risk_count,
            medium_risk_count,
            current_stage,
            killer_reason,
            valuation_status=valuation_status,
            valuation_score=valuation_score,
        )
        observation_points = self._build_observation_points(
            matched_current=matched_current,
            sector_change=sector_change,
            flow_amount=flow_amount,
            catalyst_hits=catalyst_hits,
            price_war_detected=price_war_detected,
            valuation_status=valuation_status,
        )

        return {
            "symbol": symbol,
            "industry_cycle": {
                "stock_name": stock_name,
                "industry_name": industry_name,
                "analysis_status": analysis_status,
                "beneficiary_level": beneficiary_level,
                "beneficiary_reason": beneficiary_reason,
                "cycle_phase": cycle_phase,
                "cycle_phase_reason": cycle_phase_reason,
                "prosperity_score": prosperity_score,
                "prosperity_judgement": prosperity_judgement,
                "core_logic": stock_focus_summary if stock_focus_summary else core_logic,
                "killer_reason": killer_reason if killer_reason else None,
                "observation_window": "未来 6-12 个月",
                "catalysts": catalysts,
                "risks": risks,
                "observation_points": observation_points,
                "mainline_detector": {
                    "passed": mainline_passed,
                    "conclusion": (
                        "主线属性成立，个股具备真实受益与持续催化。"
                        if mainline_passed else "主线属性暂未完全成立，当前仍有关键约束未过。"
                    ),
                    "failed_reason": None if mainline_passed else _pick_failed_reason(mainline_checklist),
                    "checklist": mainline_checklist,
                },
                "industry_beta_detector": {
                    "passed": beta_passed,
                    "conclusion": (
                        "行业 β 明确，具备中期景气上行基础。"
                        if beta_passed else "行业 β 还不够硬，暂时不能把它当成强 β 方向。"
                    ),
                    "failed_reason": None if beta_passed else _pick_failed_reason(beta_checklist),
                    "checklist": beta_checklist,
                },
                "evidence": {
                    "market_mainline": {
                        "report_pending": bool(market_report.get("report_pending")),
                        "market_stage": market_evidence.get("market_stage") or {},
                        "report_current_mainlines": _list_of_dicts(market_report.get("current_mainlines")),
                        "report_future_mainlines": _list_of_dicts(market_report.get("future_mainlines")),
                        "matched_current_mainlines": current_matches[:3],
                        "matched_future_mainlines": future_matches[:3],
                        "current_theme_detail": current_theme_detail or {},
                        "future_theme_detail": future_theme_detail or {},
                    },
                    "sector_snapshot": {
                        "rank": board_rank,
                        "total": board_total,
                        "change_pct": sector_change,
                        "leading_stock": (board_item or {}).get("lead_stock"),
                        "leading_stock_change_pct": _safe_float((board_item or {}).get("lead_stock_change_pct")),
                        "up_count": _safe_int((board_item or {}).get("up_count")),
                        "down_count": _safe_int((board_item or {}).get("down_count")),
                        "fallback_from_flow": bool((board_item or {}).get("_fallback_from_flow")),
                    },
                    "fund_flow": {
                        "rank": flow_rank,
                        "total": flow_total,
                        "main_net_inflow": flow_amount,
                        "super_large_net_inflow": _safe_float((flow_item or {}).get("super_large_net_inflow")),
                        "large_net_inflow": _safe_float((flow_item or {}).get("large_net_inflow")),
                        "pct_chg": _safe_float((flow_item or {}).get("pct_chg")),
                        "leading_stock": (flow_item or {}).get("leading_stock"),
                    },
                    "peer_group": peer_snapshot,
                    "valuation_snapshot": {
                        "pe_ttm": pe_ttm,
                        "pb": pb,
                        "industry_name": industry_average.get("industry"),
                        "industry_pe": _safe_float(industry_average.get("pe")),
                        "industry_pb": _safe_float(industry_average.get("pb")),
                        "industry_sample_size": _safe_int(industry_average.get("sample_size")),
                        "pe_premium_vs_industry": pe_premium_vs_industry,
                        "pb_premium_vs_industry": pb_premium_vs_industry,
                        "price_overdraft_status": valuation_status or None,
                        "price_overdraft_score": valuation_score,
                    },
                    "sentiment_snapshot": {
                        "news_count": len(news_items),
                        "research_count": len(research_items),
                        "positive_research_count": positive_research,
                        "sentiment_score": sentiment_score,
                        "social_score": social_score,
                        "discussion_count": total_discussion,
                    },
                    "risk_snapshot": {
                        "high_risk_count": high_risk_count,
                        "medium_risk_count": medium_risk_count,
                        "top_risk_labels": ((risk_data.get("analysis") or {}).get("top_risk_labels") or [])[:5],
                    },
                    "data_quality": data_quality,
                    "driver_signals": driver_signals,
                },
            },
            "_fetched_at": datetime.now().isoformat(),
            "_cached": False,
            "fallback_used": bool(market_report.get("report_pending")),
        }

    def _build_prosperity_judgement(
        self,
        *,
        analysis_status: str,
        industry_name: str,
        matched_current: Optional[dict[str, Any]],
        matched_future: Optional[dict[str, Any]],
        cycle_phase: str,
        sector_change: Optional[float],
        board_rank: Optional[int],
        board_total: int,
        flow_amount: Optional[float],
        high_risk_count: int,
        price_war_detected: bool,
    ) -> str:
        if analysis_status in {"主线", "分支主线"}:
            return (
                f"{industry_name} 当前处在{cycle_phase}，已能映射到市场主线“{matched_current.get('name') if matched_current else '当前主线'}”，"
                f"板块强度 {sector_change if sector_change is not None else 'N/A'}%，排名 {board_rank or 'N/A'}/{board_total or 'N/A'}。"
            )
        if analysis_status == "观察":
            future_name = matched_future.get("name") if matched_future else "候选方向"
            return f"{industry_name} 当前更接近{cycle_phase}，已经出现景气线索，但更偏“{future_name}”式的候选观察期。"
        if analysis_status == "退潮":
            return f"{industry_name} 的景气逻辑边际走弱，当前更像{cycle_phase}下的高位分歧或退潮，需防止高位兑现。"
        risk_suffix = "，且存在明显价格战压制" if price_war_detected else ""
        if high_risk_count > 0:
            risk_suffix += "，高等级风险事件也在压制预期"
        return f"{industry_name} 暂时没有形成足够强的景气共振，当前仍停留在{cycle_phase}{risk_suffix}。"

    def _build_core_logic(
        self,
        *,
        driver_signals: dict[str, list[str]],
        matched_current: Optional[dict[str, Any]],
        matched_future: Optional[dict[str, Any]],
        price_war_detected: bool,
        main_business: str,
        beneficiary_level: str,
    ) -> str:
        signal_parts = []
        for key in ("policy", "technology", "demand", "supply"):
            hits = driver_signals.get(key) or []
            if hits:
                signal_parts.append(f"{key}:{'/'.join(hits[:3])}")
        driver_text = "；".join(signal_parts) if signal_parts else "当前驱动证据偏弱"
        theme_text = (
            f"当前映射主线“{matched_current.get('name')}”"
            if matched_current else (
                f"更接近候选主线“{matched_future.get('name')}”"
                if matched_future else "暂未映射到明确主线"
            )
        )
        suffix = "；但价格战/内卷会削弱行业 β" if price_war_detected else ""
        business_text = f"主营受益路径：{main_business[:56]}" if main_business else "主营受益路径暂不清晰"
        return f"{theme_text}，驱动来自 {driver_text}；受益级别为{beneficiary_level}；{business_text}{suffix}。"

    def _build_stock_focus_summary(
        self,
        *,
        stock_name: str,
        main_business: str,
        beneficiary_level: str,
        beneficiary_reason: str,
        news_items: list[dict[str, Any]],
        research_items: list[dict[str, Any]],
    ) -> str:
        direct_company_signals: list[str] = []
        for item in news_items[:8]:
            title = _normalize_text(item.get("title"))
            if title:
                direct_company_signals.append(title)
        for item in research_items[:4]:
            title = _normalize_text(item.get("title"))
            if title:
                direct_company_signals.append(title)

        summary_parts = [f"{stock_name} 需要优先从个股受益路径来理解，当前受益级别为“{beneficiary_level}”。"]
        if beneficiary_reason:
            summary_parts.append(beneficiary_reason)
        if main_business:
            summary_parts.append(f"主营业务是：{main_business[:96]}。")
        if direct_company_signals:
            summary_parts.append(f"与公司直接相关的近端证据包括：{'；'.join(direct_company_signals[:3])}。")
        return " ".join(summary_parts)

    def _build_catalysts(self, catalyst_hits: list[str], matched_future: Optional[dict[str, Any]]) -> list[str]:
        items = [f"{hit}持续验证" for hit in catalyst_hits[:4]]
        if matched_future and matched_future.get("name"):
            items.append(f"候选主线“{matched_future['name']}”若继续发酵，有望抬升行业预期")
        return items[:5] or ["暂无足够强的中期催化，需继续跟踪订单、政策和资本开支。"]

    def _build_risks(
        self,
        price_war_detected: bool,
        high_risk_count: int,
        medium_risk_count: int,
        current_stage: str,
        killer_reason: str,
        valuation_status: str,
        valuation_score: Optional[float],
    ) -> list[str]:
        items: list[str] = []
        if price_war_detected:
            items.append("价格战 / 内卷可能压制盈利与行业 β。")
        if high_risk_count > 0:
            items.append("高等级风险事件仍在压制情绪与估值。")
        if medium_risk_count >= 3:
            items.append("中等级风险事件偏多，说明景气验证还不够顺畅。")
        if valuation_status in {"high", "medium"}:
            items.append(f"股价透支信号为 {valuation_status}，透支分 {valuation_score if valuation_score is not None else 'N/A'}，需警惕预期先行后的回撤。")
        if _contains_any(current_stage, _FADING_STAGE_KEYWORDS):
            items.append(f"当前主线阶段为“{current_stage}”，要警惕一致预期后的兑现。")
        if killer_reason:
            items.append(f"当前最核心约束：{killer_reason}")
        return items[:5] or ["暂无明显结构性风险，但仍需跟踪景气兑现节奏。"]

    def _build_observation_points(
        self,
        *,
        matched_current: Optional[dict[str, Any]],
        sector_change: Optional[float],
        flow_amount: Optional[float],
        catalyst_hits: list[str],
        price_war_detected: bool,
        valuation_status: str,
    ) -> list[str]:
        points = []
        points.append("观察行业板块能否继续维持在涨幅前列，而不是单日脉冲。")
        if flow_amount is not None:
            points.append("观察主力资金净流入能否持续，而不是一次性冲高回落。")
        if matched_current:
            points.append(f"观察主线“{matched_current.get('name')}”是否仍在扩散，而不是只剩个股抱团。")
        if catalyst_hits:
            points.append(f"观察 {' / '.join(catalyst_hits[:3])} 是否从预期走向兑现。")
        if price_war_detected:
            points.append("重点跟踪降价、毛利率和订单质量，避免景气被价格战证伪。")
        if valuation_status in {"high", "medium"}:
            points.append("观察估值透支能否被订单、利润或政策继续消化，避免只剩估值顶着。")
        if sector_change is not None and sector_change < 0:
            points.append("当前板块涨跌幅偏弱，先看是否只是短期回撤还是趋势转弱。")
        return points[:5]
