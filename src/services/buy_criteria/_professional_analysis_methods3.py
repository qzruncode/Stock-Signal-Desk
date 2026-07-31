"""Professional buy-analysis function group 3."""

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

__all__ = ['_prompt_evidence_view', '_compact_base_company_packet']

def _prompt_evidence_view(evidence: dict[str, Any]) -> dict[str, Any]:
    """Bound the LLM payload while retaining primary evidence and all axes."""
    section_limits = {
        "market_mainline": 2_800,
        "industrial_competitiveness": 3_200,
        "industry_cycle": 2_000,
        "competition_quality": 2_000,
        "growth_drivers": 2_600,
        "forward_catalysts": 3_200,
        "major_risks": 2_000,
        "valuation_odds": 1_500,
    }
    sections: dict[str, Any] = {}
    for section, payload in (evidence.get("dimension_evidence") or {}).items():
        if not isinstance(payload, dict):
            continue
        sections[section] = {
            "success": payload.get("success"),
            "evidence_gap": payload.get("evidence_gap"),
            "summary": _bounded_text(
                payload.get("summary"),
                section_limits.get(section, 2_500),
            ),
            "structured_context": payload.get("structured_context") or {},
            "deterministic_entry_context": payload.get("deterministic_entry_context"),
        }
    return {
        "contract_version": evidence.get("contract_version"),
        "requested_at": evidence.get("requested_at"),
        "symbol": evidence.get("symbol"),
        "stock_info": evidence.get("stock_info"),
        "investment_thesis": evidence.get("investment_thesis"),
        "thesis_context": evidence.get("thesis_context"),
        "mainline_strategy": evidence.get("mainline_strategy"),
        "research_scope": evidence.get("research_scope"),
        "public_research_coverage": {
            "lens_status": (evidence.get("public_research") or {}).get("lens_status"),
            "retrieved_source_count": (evidence.get("public_research") or {}).get("retrieved_source_count"),
        },
        "public_research_evidence": [
            {
                key: item.get(key)
                for key in (
                    "lens",
                    "subject",
                    "query",
                    "title",
                    "source",
                    "source_quality",
                    "published_date",
                    "url",
                )
                if item.get(key) is not None
            }
            | {
                "snippet": _bounded_text(item.get("snippet"), 420),
            }
            for item in ((evidence.get("public_research") or {}).get("items") or [])
            if isinstance(item, dict)
        ][:40],
        "company_packet": _compact_base_company_packet(evidence.get("base_company_packet")),
        "dimension_evidence": sections,
        "evidence_gaps": evidence.get("evidence_gaps") or [],
        "capability_gaps": evidence.get("capability_gaps") or [],
        "source_failures": evidence.get("source_failures") or [],
        "public_disclosure_limits": (evidence.get("public_disclosure_limits") or []),
        "required_dimension_order": [{"dimension_id": key, "title": title} for key, title in DIMENSION_DEFINITIONS],
    }

def _compact_base_company_packet(value: Any) -> dict[str, Any]:
    """Keep decision-bearing fields and remove repeated source metadata."""
    item = value if isinstance(value, dict) else {}

    def pick(source: Any, keys: tuple[str, ...]) -> dict[str, Any]:
        source = source if isinstance(source, dict) else {}
        return {key: source.get(key) for key in keys if source.get(key) is not None}

    snapshot = item.get("snapshot") if isinstance(item.get("snapshot"), dict) else {}
    technical = snapshot.get("technical") if isinstance(snapshot.get("technical"), dict) else {}
    financials = item.get("financials") if isinstance(item.get("financials"), dict) else {}
    periods = [
        pick(
            period,
            (
                "report_date",
                "report_period",
                "revenue",
                "revenue_yoy",
                "parent_net_profit",
                "parent_net_profit_yoy",
                "deducted_net_profit",
                "deducted_net_profit_yoy",
                "gross_margin",
                "net_margin",
                "roe",
                "operating_cash_flow",
                "free_cash_flow",
                "cash_conversion_ratio",
                "debt_ratio",
                "accounts_receivable",
                "inventory",
                "contract_liabilities",
                "flow_basis",
            ),
        )
        for period in (financials.get("items") or [])[-10:]
        if isinstance(period, dict)
    ]
    segments = item.get("business_segments")
    segments = segments if isinstance(segments, dict) else {}
    consensus = item.get("consensus")
    consensus = consensus if isinstance(consensus, dict) else {}
    risks = item.get("risk_events")
    risks = risks if isinstance(risks, dict) else {}
    announcements = item.get("announcements")
    announcements = announcements if isinstance(announcements, dict) else {}
    return {
        "profile": pick(
            item.get("profile"),
            (
                "short_name",
                "company_name",
                "industry",
                "industry_eastmoney",
                "listing_date",
                "main_business",
                "company_profile",
            ),
        ),
        "quote": pick(
            snapshot.get("quote"),
            (
                "price",
                "change_pct",
                "volume",
                "amount",
                "turnover_rate",
                "pe_dynamic",
                "pb",
                "data_time",
                "is_stale",
            ),
        ),
        "technical": {
            "indicators": pick(
                technical.get("indicators"),
                (
                    "close",
                    "ma20",
                    "ma60",
                    "rsi14",
                    "atr14_pct",
                    "return_5d_pct",
                    "return_20d_pct",
                    "return_60d_pct",
                    "high_20d",
                    "low_20d",
                    "high_60d",
                    "low_60d",
                ),
            ),
            **pick(technical, ("data_time", "is_stale", "source")),
        },
        "financials": {
            **pick(financials, ("amount_unit", "ratio_unit", "data_time")),
            "items": periods,
        },
        "business_segments": {
            **pick(segments, ("latest_report_date", "source_url")),
            "items": [
                pick(
                    segment,
                    (
                        "report_date",
                        "category",
                        "segment_name",
                        "revenue",
                        "revenue_share_pct",
                        "gross_profit_share_pct",
                        "gross_margin_pct",
                    ),
                )
                for segment in (segments.get("items") or [])[:6]
                if isinstance(segment, dict)
            ],
        },
        "valuation": pick(
            item.get("valuation"),
            (
                "trade_date",
                "current_price",
                "pe_ttm",
                "pe_static",
                "pb_mrq",
                "ps_ttm",
                "pcf_ttm",
                "peg_trailing",
                "peg_forward",
                "forward_pe",
                "dividend_yield",
                "history_statistics",
                "positive_pe_percentile",
                "industry_average",
                "data_time",
            ),
        ),
        "consensus": {
            **pick(
                consensus,
                (
                    "coverage_available",
                    "coverage_count_latest",
                    "coverage_status",
                    "latest_institution_report_date",
                    "forecast_warning",
                    "data_time",
                ),
            ),
            "estimates": consensus.get("estimates") or [],
            "actuals": consensus.get("actuals") or [],
        },
        "peer_comparison": item.get("peer_comparison") or {},
        "risk_events": {
            **pick(
                risks,
                (
                    "has_risk_events",
                    "analysis",
                    "data_time",
                    "freshness_unknown",
                ),
            ),
            "items": risks.get("items") or [],
        },
        "announcements": {
            **pick(
                announcements,
                (
                    "has_announcements",
                    "analysis",
                    "coverage_start",
                    "coverage_end",
                    "data_time",
                ),
            ),
            "items": announcements.get("items") or [],
        },
        "capital_flow": item.get("capital_flow") or {},
        "evidence_coverage": item.get("evidence_coverage") or {},
    }
