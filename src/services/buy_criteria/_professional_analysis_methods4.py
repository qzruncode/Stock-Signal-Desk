"""Professional buy-analysis function group 4."""

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

__all__ = ['_dimension_company_context', '_mainline_report_from_evidence', '_mainline_row_by_name', '_early_candidate_eligibility']

def _dimension_company_context(
    packet: dict[str, Any],
    dimension_id: str,
) -> dict[str, Any]:
    """Route explicit structured facts to the dimension that needs them."""

    def money(value: Any) -> float | None:
        if not isinstance(value, (int, float)):
            return None
        return round(float(value) / 100_000_000, 4)

    profile = packet.get("profile")
    profile = profile if isinstance(profile, dict) else {}
    compact_profile = {
        key: profile.get(key)
        for key in (
            "short_name",
            "company_name",
            "industry",
            "industry_eastmoney",
            "listing_date",
            "main_business",
        )
        if profile.get(key) is not None
    }
    financials = packet.get("financials")
    financials = financials if isinstance(financials, dict) else {}
    periods = [item for item in financials.get("items") or [] if isinstance(item, dict)]
    period_rows = [
        {
            "report_date": item.get("report_date"),
            "report_period": item.get("report_period"),
            "reported_period_revenue_yoy_pct": item.get("revenue_yoy"),
            "reported_period_parent_net_profit_yoy_pct": item.get("parent_net_profit_yoy"),
            "reported_period_deducted_net_profit_yoy_pct": item.get("deducted_net_profit_yoy"),
            "reported_growth_basis": (
                "公司披露的截至该报告期同比；一季报等同单季同比，"
                "中报和三季报通常为年初至报告期累计同比，年报为全年同比"
            ),
            "gross_margin_pct": item.get("gross_margin"),
            "net_margin_pct": item.get("net_margin"),
            "roe_pct": item.get("roe"),
            "revenue_亿元": money(item.get("revenue")),
            "parent_net_profit_亿元": money(item.get("parent_net_profit")),
            "deducted_net_profit_亿元": money(item.get("deducted_net_profit")),
            "operating_cash_flow_亿元": money(item.get("operating_cash_flow")),
            "free_cash_flow_亿元": money(item.get("free_cash_flow")),
            "cash_conversion_ratio": item.get("cash_conversion_ratio"),
            "debt_ratio_pct": item.get("debt_ratio"),
            "accounts_receivable_亿元": money(item.get("accounts_receivable")),
            "inventory_亿元": money(item.get("inventory")),
            "contract_liabilities_亿元": money(item.get("contract_liabilities")),
        }
        for item in periods
    ]

    periods_by_year: dict[int, list[dict[str, Any]]] = {}
    for item in periods:
        report_date = str(item.get("report_date") or "")
        if len(report_date) < 4 or not report_date[:4].isdigit():
            continue
        periods_by_year.setdefault(int(report_date[:4]), []).append(item)

    complete_years = sorted(
        year
        for year, items in periods_by_year.items()
        if len({str(item.get("report_period") or item.get("report_date") or "") for item in items}) == 4
    )
    latest_full_year = complete_years[-1] if complete_years else None
    latest_full_year_items = periods_by_year.get(latest_full_year, []) if latest_full_year is not None else []
    annual_summary: dict[str, Any] = {}
    if len(latest_full_year_items) == 4:
        flow_fields = {
            "revenue_亿元": "revenue",
            "parent_net_profit_亿元": "parent_net_profit",
            "deducted_net_profit_亿元": "deducted_net_profit",
            "operating_cash_flow_亿元": "operating_cash_flow",
            "free_cash_flow_亿元": "free_cash_flow",
        }
        annual_summary = {
            output: money(sum(float(item.get(source) or 0) for item in latest_full_year_items))
            for output, source in flow_fields.items()
        }
        previous_year_items = periods_by_year.get(
            int(latest_full_year) - 1,
            [],
        )
        if len(previous_year_items) == 4:
            for output, source in flow_fields.items():
                current_value = sum(float(item.get(source) or 0) for item in latest_full_year_items)
                previous_value = sum(float(item.get(source) or 0) for item in previous_year_items)
                annual_summary[output.replace("_亿元", "_yoy_pct")] = (
                    round(
                        (current_value - previous_value) / abs(previous_value) * 100,
                        4,
                    )
                    if previous_value
                    else None
                )
        annual_profit = annual_summary.get("parent_net_profit_亿元")
        annual_cash = annual_summary.get("operating_cash_flow_亿元")
        annual_summary["cash_to_parent_profit_ratio"] = (
            round(annual_cash / annual_profit, 4)
            if annual_profit not in (None, 0) and annual_cash is not None
            else None
        )
        latest_year_end = latest_full_year_items[-1]
        annual_summary.update(
            {
                "year": latest_full_year,
                "year_end_accounts_receivable_亿元": money(latest_year_end.get("accounts_receivable")),
                "year_end_inventory_亿元": money(latest_year_end.get("inventory")),
                "year_end_debt_ratio_pct": latest_year_end.get("debt_ratio"),
            }
        )

    financial_context = {
        "basis": (
            "季度流量字段已统一为单季度；latest_full_year为程序识别的最近完整会计年度，"
            "年度流量由四个单季求和，年度同比仅在上一完整年度四季齐全时由程序计算，"
            "资产负债字段取该年年末；reported_period_*_yoy_pct保留公司披露的截至报告期"
            "同比口径，不得当作任意单季度同比"
        ),
        "latest_full_year": annual_summary or None,
        "quarterly": period_rows,
    }
    recent_financial_context = {
        "basis": financial_context["basis"],
        "latest_full_year": financial_context["latest_full_year"],
        "latest_quarters": period_rows[-2:],
    }
    quote = packet.get("quote") if isinstance(packet.get("quote"), dict) else {}
    valuation = packet.get("valuation") if isinstance(packet.get("valuation"), dict) else {}
    peers = packet.get("peer_comparison") if isinstance(packet.get("peer_comparison"), dict) else {}
    peer_dimensions = peers.get("dimensions") if isinstance(peers.get("dimensions"), dict) else {}

    def compact_peer(name: str, fields: tuple[str, ...]) -> dict[str, Any]:
        source = peer_dimensions.get(name)
        source = source if isinstance(source, dict) else {}
        current_year = datetime.now().astimezone().year

        def pick(row: Any) -> dict[str, Any]:
            row = row if isinstance(row, dict) else {}
            result: dict[str, Any] = {}
            for key in fields:
                value = row.get(key)
                if key in {"forward_pe", "forward_ps"} and isinstance(value, list):
                    value = [
                        item
                        for item in value
                        if isinstance(item, dict)
                        and isinstance(item.get("year"), int)
                        and item["year"] >= current_year
                        and item.get("value") is not None
                    ]
                if value is not None:
                    result[key] = value
            return result

        return {
            key: source.get(key)
            for key in ("label", "report_date", "sample_size", "target_rank")
            if source.get(key) is not None
        } | {
            "target": pick(source.get("target")),
            "industry_median": pick(source.get("industry_median")),
        }

    peer_growth = compact_peer(
        "growth",
        (
            "symbol",
            "name",
            "eps_growth_3y_cagr_pct",
            "eps_growth_report_year_pct",
            "eps_growth_ttm_pct",
            "revenue_growth_3y_cagr_pct",
            "revenue_growth_report_year_pct",
            "revenue_growth_ttm_pct",
            "net_profit_growth_3y_cagr_pct",
            "net_profit_growth_report_year_pct",
            "net_profit_growth_ttm_pct",
            "rank",
        ),
    )
    peer_profitability = compact_peer(
        "profitability",
        (
            "symbol",
            "name",
            "roe_3y_average_pct",
            "net_margin_3y_average_pct",
            "asset_turnover_3y_average",
            "equity_multiplier_3y_average",
            "rank",
        ),
    )
    peer_valuation = compact_peer(
        "valuation",
        (
            "symbol",
            "name",
            "peg_forward",
            "pe_ttm",
            "forward_pe",
            "ps_ttm",
            "forward_ps",
            "pb_mrq",
            "pcf_ttm",
            "ev_ebitda_report_year",
            "rank",
        ),
    )

    base: dict[str, Any] = {"profile": compact_profile}
    if dimension_id == "market_mainline":
        pass
    elif dimension_id == "industrial_competitiveness":
        base.update(
            {
                "financials": recent_financial_context,
                "business_segments": packet.get("business_segments") or {},
                "peer_growth": peer_growth,
                "peer_profitability": peer_profitability,
            }
        )
    elif dimension_id == "industry_cycle":
        base.update(
            {
                "financials": financial_context,
                "business_segments": packet.get("business_segments") or {},
            }
        )
    elif dimension_id == "competition_quality":
        base.update(
            {
                "financials": recent_financial_context,
                "business_segments": packet.get("business_segments") or {},
                "peer_profitability": peer_profitability,
            }
        )
    elif dimension_id == "growth_drivers":
        base.update(
            {
                "financials": financial_context,
                "business_segments": packet.get("business_segments") or {},
            }
        )
    elif dimension_id == "forward_catalysts":
        base.update(
            {
                "announcements": packet.get("announcements") or {},
                "risk_events": packet.get("risk_events") or {},
            }
        )
    elif dimension_id == "valuation_odds":
        base.update(
            {
                "quote": quote,
                "valuation": valuation,
                "peer_valuation": peer_valuation,
                "peer_growth": peer_growth,
                "financials": recent_financial_context,
            }
        )
    elif dimension_id == "major_risks":
        base.update(
            {
                "financials": recent_financial_context,
                "risk_events": packet.get("risk_events") or {},
                "announcements": packet.get("announcements") or {},
            }
        )
    return base

def _mainline_report_from_evidence(
    evidence: dict[str, Any],
) -> dict[str, Any]:
    section = (evidence.get("dimension_evidence") if isinstance(evidence.get("dimension_evidence"), dict) else {}).get(
        "market_mainline"
    )
    section = section if isinstance(section, dict) else {}
    raw = section.get("raw_data")
    raw = raw if isinstance(raw, dict) else {}
    report = raw.get("market_mainline_report")
    if isinstance(report, dict):
        return report
    snapshot = evidence.get("market_mainline_snapshot")
    return snapshot if isinstance(snapshot, dict) else {}

def _mainline_row_by_name(
    rows: Any,
    name: str | None,
) -> dict[str, Any] | None:
    target = str(name or "").strip()
    if not target or not isinstance(rows, list):
        return None
    return next(
        (item for item in rows if isinstance(item, dict) and str(item.get("name") or "").strip() == target), None
    )

def _early_candidate_eligibility(
    row: dict[str, Any],
    classification: MainlineGateClassification,
) -> tuple[bool, list[str]]:
    """Apply only program-verifiable constraints for an emerging direction."""

    missing: list[str] = []
    branches = {str(value).strip() for value in row.get("branches") or [] if str(value).strip()}
    relation = MainlineDirectionRelation(classification.direction_relation)
    if relation == MainlineDirectionRelation.EMERGING_BRANCH:
        branch = str(classification.matched_branch or "").strip()
        if not branch or branch not in branches:
            missing.append("结构化候选分支关系")
    elif relation != MainlineDirectionRelation.CORE:
        missing.append("候选主线核心或分支关系")

    if str(row.get("expected_horizon") or "") != "one_to_six_months":
        missing.append("未来1—6个月窗口")
    evidence_axes = {
        str(value.get("axis") or "").strip()
        for value in row.get("evidence_axes") or []
        if isinstance(value, dict) and str(value.get("axis") or "").strip() and bool(value.get("evidence_refs"))
    }
    axis_evidence_refs = {
        str(ref).strip()
        for value in row.get("evidence_axes") or []
        if isinstance(value, dict)
        for ref in value.get("evidence_refs") or []
        if str(ref).strip()
    }
    if len(evidence_axes) < 2 or len(axis_evidence_refs) < 2:
        missing.append("至少两类独立中期证据")
    trigger_assessments = [item for item in row.get("trigger_assessments") or [] if isinstance(item, dict)]
    if not any(str(item.get("status") or "") in {"met", "partial"} for item in trigger_assessments):
        missing.append("至少一个已满足或部分满足的触发条件")
    if MainlineTriggerProgress(classification.trigger_progress) not in {
        MainlineTriggerProgress.MET,
        MainlineTriggerProgress.PARTIAL,
    }:
        missing.append("可核验的触发进度")
    classified_lifecycle = MainlineLifecycle(classification.lifecycle)
    if classified_lifecycle not in {
        MainlineLifecycle.EMERGING,
        MainlineLifecycle.VALIDATING,
    }:
        missing.append("候选主线生命周期")
    if str(row.get("lifecycle") or "") != classified_lifecycle.value:
        missing.append("与候选报告一致的生命周期")
    return not missing, missing
