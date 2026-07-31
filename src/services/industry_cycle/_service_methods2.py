"""IndustryCycleService method group 2."""

from __future__ import annotations

from src.services.industry_cycle.service import (
    json,
    logging,
    datetime,
    Any,
    Callable,
    Optional,
    _cache_get,
    _current_report_as_of_date,
    _report_cache_get,
    uuid4_hex,
    _assess_evidence_gate,
    _build_data_quality,
    _build_evidence_insufficient_report,
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
    _parse_llm_json_payload,
    _pick_theme_detail,
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
    _build_streaming_industry_cycle_draft,
    _is_usable_report_payload,
    logger,
    _MAINLINE_CRITERION_IDS,
    _INDUSTRY_BETA_CRITERION_IDS,
    _validated_detector,
    _persist_cache,
    _persist_report_cache,
 )

class _IndustryCycleServiceMethods2:
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
