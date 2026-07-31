"""Professional buy-analysis function group 2."""

from __future__ import annotations

from src.services.buy_criteria.professional_analysis import (
    annotations,
    json,
    logging,
    re,
    ThreadPoolExecutor,
    as_completed,
    datetime,
    Any,
    Callable,
    Iterable,
    Literal,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
    CriterionEvidence,
    _parse_verdict_json,
    CompetitionLandscapeEvaluator,
    CatalystEventsEvaluator,
    FatalRisksEvaluator,
    GrowthDriversEvaluator,
    IndustrialCompetitivenessEvaluator,
    MainlinePositionEvaluator,
    ProsperityCycleEvaluator,
    MainlineDirectionRelation,
    MainlineGateClassification,
    MainlineLifecycle,
    MainlineStrategyProfile,
    MainlineTriggerProgress,
    mainline_strategy_label,
    normalize_mainline_strategy,
    collect_public_research,
    derive_research_scope,
    research_summary_for_lenses,
    logger,
    PROFESSIONAL_BUY_CONTRACT_VERSION,
    PROFESSIONAL_BUY_ANALYSIS_MODE,
    DIMENSION_DEFINITIONS,
    DIMENSION_IDS,
    DIMENSION_TITLES,
    PUBLIC_RESEARCH_LENSES_BY_DIMENSION,
    DimensionId,
    DimensionStatus,
    ModelDimensionStatus,
    RecommendationCode,
    DimensionAssessment,
    ModelDimensionAssessment,
    ForcedSchemaResponseError,
    ProfessionalAssessment,
    OverallAssessment,
    ANALYST_SYSTEM_PROMPT,
    _NUMERIC_CLAIM_RE,
    _NUMERIC_RANGE_RE,
    _ISO_DATE_RE,
    _BARE_DECIMAL_RE,
    _DANGLING_HEADLINE_RE,
    __all__,
 )

__all__ = ['_merge_public_research', '_source_failure_code', '_source_failure_summary', '_collect_source_failures', '_refresh_professional_evidence_metadata', '_collect_professional_sections', 'collect_professional_evidence', 'collect_market_mainline_evidence']

def _merge_public_research(
    current: dict[str, Any],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    attempts_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for attempt in [*(current.get("attempts") or []), *(incoming.get("attempts") or [])]:
        if not isinstance(attempt, dict):
            continue
        key = (
            str(attempt.get("lens") or ""),
            str(attempt.get("subject") or ""),
            str(attempt.get("query") or ""),
        )
        attempts_by_key[key] = attempt
    items_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in [*(current.get("items") or []), *(incoming.get("items") or [])]:
        if not isinstance(item, dict):
            continue
        key = (
            str(item.get("lens") or ""),
            str(item.get("subject") or ""),
            str(item.get("url") or item.get("title") or ""),
        )
        items_by_key[key] = item
    lens_status = {
        **(current.get("lens_status") or {}),
        **(incoming.get("lens_status") or {}),
    }
    items = list(items_by_key.values())
    return {
        "scope": incoming.get("scope") or current.get("scope") or {},
        "attempts": list(attempts_by_key.values()),
        "items": items,
        "lens_status": lens_status,
        "retrieved_source_count": len({str(item.get("url") or "") for item in items if item.get("url")}),
        "retrieval_complete": all(status != "retrieval_failed" for status in lens_status.values()),
        "errors": list(
            dict.fromkeys(
                [
                    *[str(value) for value in current.get("errors") or []],
                    *[str(value) for value in incoming.get("errors") or []],
                ]
            )
        ),
    }

def _source_failure_code(value: Any) -> str:
    text = str(value or "").lower()
    if any(marker in text for marker in ("timeout", "timed out", "超时", "504")):
        return "timeout"
    if any(
        marker in text
        for marker in (
            "connection",
            "connecterror",
            "remoteprotocolerror",
            "connection reset",
            "connection aborted",
            "broken pipe",
            "ssl",
            "连接",
        )
    ):
        return "connection"
    return "unavailable"

def _source_failure_summary(
    *,
    section: str,
    source: str,
    error: Any,
) -> dict[str, str]:
    code = _source_failure_code(error)
    label = {
        "timeout": "取证超时",
        "connection": "连接失败",
        "unavailable": "来源不可用",
    }[code]
    return {
        "section": section,
        "source": source,
        "error_code": code,
        "summary": f"{section}/{source}：{label}",
    }

def _collect_source_failures(
    sections: dict[str, Any],
    enrichment: dict[str, Any],
    base_meta: dict[str, Any],
) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def append(section: str, source: str, error: Any) -> None:
        item = _source_failure_summary(
            section=section,
            source=source,
            error=error,
        )
        key = (item["section"], item["source"], item["error_code"])
        if key not in seen:
            seen.add(key)
            failures.append(item)

    for section, payload in sections.items():
        if not isinstance(payload, dict):
            continue
        raw_data = payload.get("raw_data")
        raw_data = raw_data if isinstance(raw_data, dict) else {}
        for key, error in raw_data.items():
            if str(key).endswith("_error") and error:
                append(str(section), str(key)[:-6], error)
            elif isinstance(error, dict) and error.get("success") is False:
                append(
                    str(section),
                    str(key),
                    "；".join(str(value) for value in error.get("errors") or []) or "unavailable",
                )

    lens_status = enrichment.get("lens_status")
    lens_status = lens_status if isinstance(lens_status, dict) else {}
    for lens, status in lens_status.items():
        if status == "retrieval_failed":
            append("public_research", str(lens), "retrieval_failed")

    if base_meta.get("errors"):
        append("base_company_packet", "structured_sources", "unavailable")
    return failures

def _refresh_professional_evidence_metadata(evidence: dict[str, Any]) -> None:
    sections = evidence.get("dimension_evidence")
    sections = sections if isinstance(sections, dict) else {}
    enrichment = evidence.get("public_research")
    enrichment = enrichment if isinstance(enrichment, dict) else {}
    base_meta = evidence.get("base_packet_meta")
    base_meta = base_meta if isinstance(base_meta, dict) else {}
    evidence["capability_gaps"] = [
        f"{section}: {payload.get('evidence_gap')}"
        for section, payload in sections.items()
        if isinstance(payload, dict) and payload.get("evidence_gap")
    ]
    evidence["source_failures"] = _collect_source_failures(
        sections,
        enrichment,
        base_meta,
    )
    evidence["public_disclosure_limits"] = [
        f"{attempt.get('lens')}/{attempt.get('subject') or '全市场'}: " "已完成检索，但公开来源未返回匹配材料"
        for attempt in enrichment.get("attempts") or []
        if isinstance(attempt, dict) and attempt.get("status") == "no_matching_public_material"
    ]
    evidence["evidence_gaps"] = [
        *evidence["capability_gaps"],
        *[item["summary"] for item in evidence["source_failures"]],
        *evidence["public_disclosure_limits"],
    ]
    evidence["source_links"] = _collect_source_links(evidence)

def _collect_professional_sections(
    evidence: dict[str, Any],
    requested_sections: Iterable[str],
    *,
    pre_fetched_data: dict[str, Any] | None = None,
) -> None:
    sections = evidence.setdefault("dimension_evidence", {})
    requested = [
        section for section in dict.fromkeys(requested_sections) if section in DIMENSION_IDS and section not in sections
    ]
    if not requested:
        return
    symbol = str(evidence.get("symbol") or "")
    stock_info = evidence.get("stock_info")
    stock_info = stock_info if isinstance(stock_info, dict) else {}
    collector_sections = [section for section in requested if section != "valuation_odds"]
    required_lenses = set().union(*(PUBLIC_RESEARCH_LENSES_BY_DIMENSION.get(section, set()) for section in requested))
    existing_lenses = set((evidence.get("public_research") or {}).get("lens_status") or {})
    lenses = required_lenses - existing_lenses
    enrichment: dict[str, Any] = _empty_public_research(evidence.get("research_scope") or {})
    worker_count = len(collector_sections) + bool(lenses)
    if worker_count:
        with ThreadPoolExecutor(max_workers=max(1, worker_count)) as pool:
            enrichment_future = (
                pool.submit(
                    collect_public_research,
                    stock_info,
                    requested_lenses=lenses,
                )
                if lenses
                else None
            )
            futures = [
                pool.submit(
                    _run_evidence_collector,
                    section,
                    _evidence_collector(section),
                    symbol,
                    stock_info,
                    pre_fetched_data,
                )
                for section in collector_sections
            ]
            for future in as_completed(futures):
                section, result = future.result()
                sections[section] = result
            if enrichment_future is not None:
                try:
                    enrichment = enrichment_future.result()
                except Exception as exc:
                    logger.warning(
                        "professional buy public research %s failed: %s",
                        symbol,
                        exc,
                    )
                    enrichment = {
                        **_empty_public_research(evidence.get("research_scope") or {}),
                        "lens_status": {lens: "retrieval_failed" for lens in lenses},
                        "retrieval_complete": False,
                        "errors": [f"{type(exc).__name__}: {str(exc)[:240]}"],
                    }

    evidence["public_research"] = _merge_public_research(
        evidence.get("public_research") or {},
        enrichment,
    )
    combined_research = evidence["public_research"]
    for section in collector_sections:
        payload = sections.get(section)
        if not isinstance(payload, dict):
            continue
        section_lenses = PUBLIC_RESEARCH_LENSES_BY_DIMENSION.get(
            section,
            set(),
        )
        if not section_lenses:
            continue
        supplement = research_summary_for_lenses(
            combined_research,
            section_lenses,
        )
        payload["summary"] = _bounded_text(
            f"{payload.get('summary') or ''}\n\n{supplement}",
            12_000,
        )
        raw_data = payload.get("raw_data")
        raw_data = raw_data if isinstance(raw_data, dict) else {}
        payload["raw_data"] = {
            **raw_data,
            "public_research": {
                "lens_status": {
                    lens: (combined_research.get("lens_status") or {}).get(lens) for lens in section_lenses
                },
                "items": [
                    item
                    for item in combined_research.get("items") or []
                    if isinstance(item, dict) and item.get("lens") in section_lenses
                ],
            },
        }

    if "valuation_odds" in requested:
        base_item = evidence.get("base_company_packet")
        base_item = base_item if isinstance(base_item, dict) else {}
        valuation_packet = {
            "quote": (base_item.get("snapshot") or {}).get("quote") or {},
            "valuation": base_item.get("valuation") or {},
            "consensus": base_item.get("consensus") or {},
            "peer_comparison": base_item.get("peer_comparison") or {},
            "financials": base_item.get("financials") or {},
        }
        valuation_sources_ok = any(
            isinstance(value, dict) and value.get("success") is not False for value in valuation_packet.values()
        )
        sections["valuation_odds"] = {
            "success": valuation_sources_ok,
            "evidence_gap": (None if valuation_sources_ok else "本轮估值、预期、同行与行情来源均未取得有效数据"),
            "summary": _bounded_text(
                json.dumps(
                    valuation_packet,
                    ensure_ascii=False,
                    default=str,
                ),
                6_000,
            ),
            "raw_data": valuation_packet,
            "structured_context": {},
            "semantic_status": "model_required",
        }
    _refresh_professional_evidence_metadata(evidence)

def collect_professional_evidence(
    symbol: str,
    *,
    thesis: str = "",
    thesis_context: dict[str, Any] | None = None,
    mainline_strategy: MainlineStrategyProfile | str = (MainlineStrategyProfile.CONFIRMED_MAINLINE),
    pre_fetched_data: dict[str, Any] | None = None,
    requested_sections: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Collect requested gate evidence; later gates can be loaded on demand."""
    from src.tools.get_multi_stock_decision_evidence import (
        get_multi_stock_decision_evidence,
    )

    effective_thesis = resolve_investment_thesis(thesis, thesis_context)
    strategy_profile = normalize_mainline_strategy(mainline_strategy)
    stock_info = _stock_info(symbol)
    stock_info["_investment_thesis"] = effective_thesis
    stock_info["_investment_thesis_context"] = thesis_context
    stock_info["_mainline_strategy"] = strategy_profile.value

    base_packet = get_multi_stock_decision_evidence(symbol, effective_thesis)
    base_item = next(
        (item for item in base_packet.get("items") or [] if isinstance(item, dict)),
        {},
    )
    research_scope = derive_research_scope(stock_info, base_item)
    stock_info["_derived_research_scope"] = research_scope
    evidence = {
        "contract_version": PROFESSIONAL_BUY_CONTRACT_VERSION,
        "requested_at": datetime.now().astimezone().isoformat(),
        "symbol": symbol,
        "stock_info": stock_info,
        "investment_thesis": effective_thesis or None,
        "thesis_context": thesis_context,
        "mainline_strategy": strategy_profile.value,
        "research_scope": research_scope,
        "public_research": _empty_public_research(research_scope),
        "base_company_packet": base_item,
        "base_packet_meta": {
            "success": base_packet.get("success"),
            "partial": base_packet.get("partial"),
            "data_time": base_packet.get("data_time"),
            "quote_basis": base_packet.get("quote_basis"),
            "quote_is_intraday": base_packet.get("quote_is_intraday"),
            "source": base_packet.get("source"),
            "errors": base_packet.get("errors") or [],
            "warnings": base_packet.get("warnings") or [],
        },
        "dimension_evidence": {},
    }
    _collect_professional_sections(
        evidence,
        DIMENSION_IDS if requested_sections is None else requested_sections,
        pre_fetched_data=pre_fetched_data,
    )
    return evidence

def collect_market_mainline_evidence(
    *,
    thesis: str,
    thesis_context: dict[str, Any] | None,
    mainline_strategy: MainlineStrategyProfile | str,
    market_mainline_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Collect gate-one evidence once for the structured batch thesis."""
    effective_thesis = resolve_investment_thesis(thesis, thesis_context)
    strategy_profile = normalize_mainline_strategy(mainline_strategy)
    stock_info = {
        "symbol": "MARKET_MAINLINE",
        "name": "本批次结构化产业方向",
        "industry": "",
        "_scope_only": True,
        "_investment_thesis": effective_thesis,
        "_investment_thesis_context": thesis_context,
        "_mainline_strategy": strategy_profile.value,
    }
    research_scope = derive_research_scope(stock_info, {})
    stock_info["_derived_research_scope"] = research_scope
    evidence = {
        "contract_version": PROFESSIONAL_BUY_CONTRACT_VERSION,
        "requested_at": datetime.now().astimezone().isoformat(),
        "symbol": "MARKET_MAINLINE",
        "stock_info": stock_info,
        "investment_thesis": effective_thesis or None,
        "thesis_context": thesis_context,
        "mainline_strategy": strategy_profile.value,
        "research_scope": research_scope,
        "public_research": _empty_public_research(research_scope),
        "base_company_packet": {},
        "base_packet_meta": {
            "success": True,
            "partial": False,
            "data_time": market_mainline_snapshot.get("data_time"),
            "errors": [],
            "warnings": [],
        },
        "dimension_evidence": {},
    }
    _collect_professional_sections(
        evidence,
        ("market_mainline",),
        pre_fetched_data={
            "market_mainline_snapshot": market_mainline_snapshot,
        },
    )
    return evidence
