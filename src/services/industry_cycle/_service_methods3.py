"""IndustryCycleService method group 3."""

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

class _IndustryCycleServiceMethods3:
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
