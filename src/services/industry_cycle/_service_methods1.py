"""IndustryCycleService method group 1."""

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

class _IndustryCycleServiceMethods1:
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
