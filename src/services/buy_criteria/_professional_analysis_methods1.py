"""Professional buy-analysis function group 1."""

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

__all__ = ['_structured_thesis_labels', 'resolve_investment_thesis', '_bounded_text', '_unsupported_numeric_claims', '_redact_unsupported_numeric_claims', '_repair_incomplete_dimension_headline', '_stock_info', '_evidence_collector', '_target_dimension_context', '_run_evidence_collector', '_collect_source_links', '_empty_public_research']

def _structured_thesis_labels(
    thesis_context: dict[str, Any] | None,
) -> list[str]:
    context = thesis_context if isinstance(thesis_context, dict) else {}
    labels: list[str] = []
    for domain in context.get("domains") or []:
        if not isinstance(domain, dict):
            continue
        label = str(domain.get("label") or "").strip()
        if label and label not in labels:
            labels.append(label)
    return labels

def resolve_investment_thesis(
    thesis: str,
    thesis_context: dict[str, Any] | None,
) -> str:
    """Return a concise thesis label without reparsing conversational prose."""
    explicit = str(thesis or "").strip()
    if explicit:
        return explicit
    labels = _structured_thesis_labels(thesis_context)
    if labels:
        return "、".join(labels)
    context = thesis_context if isinstance(thesis_context, dict) else {}
    return str(context.get("summary") or "").strip()

def _bounded_text(value: Any, limit: int = 8_000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    head = max(1, int(limit * 0.72))
    tail = max(1, limit - head)
    return text[:head] + "\n...[证据包按长度截断]...\n" + text[-tail:]

def _unsupported_numeric_claims(
    assessment: BaseModel,
    evidence_source: Any,
) -> list[str]:
    """Find quantitative claims that cannot be traced to collected evidence."""
    evidence_text = (
        evidence_source
        if isinstance(evidence_source, str)
        else json.dumps(evidence_source, ensure_ascii=False, default=str)
    )
    evidence_values: dict[str, list[float]] = {}
    structured_values: list[float] = []

    def visit(node: Any) -> None:
        if isinstance(node, bool) or node is None:
            return
        if isinstance(node, (int, float)):
            structured_values.append(float(node))
            return
        if isinstance(node, dict):
            for value in node.values():
                visit(value)
            return
        if isinstance(node, (list, tuple)):
            for value in node:
                visit(value)

    if not isinstance(evidence_source, str):
        visit(evidence_source)
    for match in _NUMERIC_CLAIM_RE.finditer(evidence_text):
        unit = match.group("unit").lower()
        evidence_values.setdefault(unit, []).append(float(match.group("value")))
    for match in _NUMERIC_RANGE_RE.finditer(evidence_text):
        unit = match.group("unit").lower()
        evidence_values.setdefault(unit, []).extend(
            [
                float(match.group("start")),
                float(match.group("end")),
            ]
        )
    for match in _ISO_DATE_RE.finditer(evidence_text):
        evidence_values.setdefault("年", []).append(float(match.group("year")))
        evidence_values.setdefault("月", []).append(float(match.group("month")))
        evidence_values.setdefault("日", []).append(float(match.group("day")))
    evidence_bare_decimals = [float(match.group("value")) for match in _BARE_DECIMAL_RE.finditer(evidence_text)]

    output_text = json.dumps(assessment.model_dump(), ensure_ascii=False)
    unsupported: list[str] = []
    for match in _NUMERIC_CLAIM_RE.finditer(output_text):
        value = float(match.group("value"))
        unit = match.group("unit").lower()
        candidates = evidence_values.get(unit, [])
        if unit == "%":
            matched = any(abs(value - candidate) <= 0.6 for candidate in candidates)
        elif unit in {"亿元", "万元", "元", "倍", "mw", "gw"}:
            matched = any(abs(value - candidate) <= max(0.1, abs(candidate) * 0.015) for candidate in candidates)
        else:
            matched = any(value == candidate for candidate in candidates)
        if not matched and structured_values:
            if unit == "%":
                matched = any(abs(value - candidate) <= 0.6 for candidate in structured_values)
            elif unit in {"亿元", "万元", "元", "倍", "mw", "gw"}:
                scale = {
                    "亿元": 100_000_000,
                    "万元": 10_000,
                    "元": 1,
                }.get(unit)
                matched = any(
                    (abs(value - candidate) <= max(0.1, abs(candidate) * 0.015))
                    or (scale is not None and abs(value * scale - candidate) <= max(1, abs(candidate) * 0.015))
                    for candidate in structured_values
                )
            else:
                matched = any(value == candidate for candidate in structured_values)
        if not matched:
            unsupported.append(match.group(0))
    for match in _BARE_DECIMAL_RE.finditer(output_text):
        value = float(match.group("value"))
        matched = any(
            abs(value - candidate) <= max(0.0001, abs(candidate) * 0.015)
            for candidate in [*evidence_bare_decimals, *structured_values]
        )
        if not matched:
            unsupported.append(match.group(0))
    return list(dict.fromkeys(unsupported))

def _redact_unsupported_numeric_claims(
    assessment: BaseModel,
    unsupported: list[str],
) -> BaseModel:
    """Remove whole unsupported statements without leaving broken prose."""
    redacted_item_text = "该项包含未核验数值，原陈述已删除。"

    def clean(value: Any) -> Any:
        if isinstance(value, str):
            segments = re.split(r"(?<=[。！？；\n])", value)
            return "".join(
                segment for segment in segments if not any(claim in segment for claim in unsupported)
            ).strip()
        if isinstance(value, list):
            cleaned_items = [clean(item) for item in value]
            return [
                (redacted_item_text if isinstance(item, str) and not item.strip() else item) for item in cleaned_items
            ]
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        return value

    payload = clean(assessment.model_dump())
    fallback_text = "相关时间或数值陈述未通过本轮证据追溯，已从展示中删除。"
    for field_name in (
        "headline",
        "analysis",
        "investment_profile",
        "overall_summary",
        "core_thesis",
        "biggest_issue",
        "recommendation_reason",
        "bull_case_chain",
        "risk_chain",
    ):
        if field_name in payload and not str(payload.get(field_name) or "").strip():
            payload[field_name] = fallback_text
    notice = "模型生成的部分数字未通过本轮证据追溯，已从展示中删除。"
    if "counter_evidence" in payload:
        counter = list(payload.get("counter_evidence") or [])
        if notice not in counter:
            counter.append(notice)
        payload["counter_evidence"] = counter[:4]
    elif "evidence_gaps" in payload:
        gaps = list(payload.get("evidence_gaps") or [])
        if notice not in gaps:
            gaps.append(notice)
        payload["evidence_gaps"] = gaps[:8]
    return type(assessment).model_validate(payload)

def _repair_incomplete_dimension_headline(
    assessment: DimensionAssessment,
) -> DimensionAssessment:
    """Replace a visibly truncated headline with the first complete finding."""
    headline = str(assessment.headline or "").strip()
    if len(headline) >= 8 and not _DANGLING_HEADLINE_RE.search(headline):
        return assessment
    candidates = [
        value.strip(" 【】[]") for value in re.split(r"[。\n；]", assessment.analysis) if value.strip(" 【】[]")
    ]
    replacement = next(
        (value for value in candidates if len(value) >= 8 and not _DANGLING_HEADLINE_RE.search(value)),
        "",
    )
    if not replacement:
        return assessment
    return assessment.model_copy(update={"headline": replacement[:120].rstrip("，,：:")})

def _stock_info(symbol: str) -> dict[str, Any]:
    try:
        from api.v1.endpoints.stock_info import get_stock_info

        result = get_stock_info(symbol)
        return dict(result) if isinstance(result, dict) else {"symbol": symbol}
    except Exception as exc:
        logger.warning("professional buy stock info failed for %s: %s", symbol, exc)
        return {"symbol": symbol, "name": symbol, "industry": ""}

def _evidence_collector(section: str) -> Any:
    factories: dict[str, Callable[[], Any]] = {
        "market_mainline": MainlinePositionEvaluator,
        "industrial_competitiveness": IndustrialCompetitivenessEvaluator,
        "industry_cycle": ProsperityCycleEvaluator,
        "competition_quality": CompetitionLandscapeEvaluator,
        "growth_drivers": GrowthDriversEvaluator,
        "forward_catalysts": CatalystEventsEvaluator,
        "major_risks": FatalRisksEvaluator,
    }
    factory = factories.get(section)
    if factory is None:
        raise KeyError(f"unknown professional evidence section: {section}")
    return factory()

def _target_dimension_context(
    section: str,
    raw_data: Any,
) -> dict[str, Any]:
    """Keep decision-bearing thesis facts outside truncatable prose."""
    raw = raw_data if isinstance(raw_data, dict) else {}
    membership = raw.get("thesis_membership")
    membership = membership if isinstance(membership, dict) else {}
    context: dict[str, Any] = {}
    if membership:
        context["thesis_membership"] = {
            key: membership.get(key)
            for key in (
                "requested_domains",
                "company_matched",
                "matched_domains",
                "lookup_themes",
                "boards",
                "coverage_complete",
                "decision_boundary",
                "warnings",
            )
            if membership.get(key) is not None
        }
    if section == "market_mainline":
        report = raw.get("market_mainline_report")
        report = report if isinstance(report, dict) else {}
        context["market_report"] = {
            key: report.get(key)
            for key in (
                "report_pending",
                "as_of_date",
                "overview",
                "market_stage",
                "current_mainlines",
                "future_mainlines",
            )
            if report.get(key) is not None
        }
        context["snapshot_source"] = raw.get("market_mainline_snapshot_source")
        context["snapshot_id"] = raw.get("market_mainline_snapshot_id")
        board_catalog = raw.get("board_catalog")
        board_catalog = board_catalog if isinstance(board_catalog, dict) else {}
        context["direction_board_mapping"] = {
            sector_type: [
                {key: item.get(key) for key in ("name", "code", "data_source") if item.get(key) is not None}
                for item in ((board_catalog.get(sector_type) or {}).get("matched_items") or [])
                if isinstance(item, dict)
            ][:12]
            for sector_type in ("industry", "concept")
        }
    elif section == "industrial_competitiveness":
        formal = raw.get("formal_business_evidence")
        formal = formal if isinstance(formal, dict) else {}
        segments = raw.get("business_segments")
        segments = segments if isinstance(segments, dict) else {}
        context["formal_business_evidence"] = {
            "items": [
                {
                    **{
                        key: item.get(key)
                        for key in (
                            "source",
                            "date",
                            "title",
                            "url",
                            "report_date",
                        )
                        if item.get(key) is not None
                    },
                    "excerpt": _bounded_text(item.get("excerpt"), 700),
                }
                for item in formal.get("items") or []
                if isinstance(item, dict)
            ][:8],
            "documents": [item for item in formal.get("documents") or [] if isinstance(item, dict)][:3],
        }
        context["business_segments"] = [
            item
            for item in segments.get("items") or []
            if isinstance(item, dict) and str(item.get("category") or "").lower() in {"product", "industry"}
        ][:16]
    return context

def _run_evidence_collector(
    section: str,
    evaluator: Any,
    symbol: str,
    stock_info: dict[str, Any],
    pre_fetched_data: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    try:
        evidence: CriterionEvidence = evaluator.collect_data(
            symbol,
            stock_info,
            pre_fetched_data,
        )
        evidence_gap = evaluator.evidence_failure_reason(evidence)
        return section, {
            "success": evidence_gap is None,
            "evidence_gap": evidence_gap,
            "summary": _bounded_text(evidence.data_summary),
            "raw_data": evidence.raw_data,
            "structured_context": _target_dimension_context(
                section,
                evidence.raw_data,
            ),
        }
    except Exception as exc:
        logger.warning(
            "professional buy evidence %s/%s failed: %s",
            symbol,
            section,
            exc,
        )
        return section, {
            "success": False,
            "evidence_gap": f"{type(exc).__name__}: {str(exc)[:240]}",
            "summary": "该证据维度获取失败。",
            "raw_data": {},
        }

def _collect_source_links(value: Any, *, limit: int = 24) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    url_keys = {"url", "source_url", "pdf_url", "report_url", "article_url"}

    def visit(node: Any, context: dict[str, Any] | None = None) -> None:
        if len(found) >= limit:
            return
        if isinstance(node, dict):
            merged_context = {
                **(context or {}),
                **{
                    key: node.get(key)
                    for key in (
                        "title",
                        "name",
                        "source",
                        "org",
                        "publish_date",
                        "publish_time",
                        "date",
                        "report_date",
                    )
                    if node.get(key)
                },
            }
            for key, raw_url in node.items():
                if key not in url_keys:
                    continue
                url = str(raw_url or "").strip()
                if not re.match(r"^https?://", url, re.I) or url in seen:
                    continue
                seen.add(url)
                found.append(
                    {
                        "title": str(
                            merged_context.get("title")
                            or merged_context.get("name")
                            or merged_context.get("source")
                            or "原始资料"
                        )[:160],
                        "source": str(merged_context.get("source") or merged_context.get("org") or "公开来源")[:80],
                        "date": str(
                            merged_context.get("publish_date")
                            or merged_context.get("publish_time")
                            or merged_context.get("report_date")
                            or merged_context.get("date")
                            or ""
                        )[:32],
                        "url": url,
                    }
                )
            for child in node.values():
                visit(child, merged_context)
        elif isinstance(node, (list, tuple)):
            for child in node:
                visit(child, context)

    visit(value)
    return found

def _empty_public_research(scope: dict[str, Any]) -> dict[str, Any]:
    return {
        "scope": scope,
        "attempts": [],
        "items": [],
        "lens_status": {},
        "retrieved_source_count": 0,
        "retrieval_complete": True,
        "errors": [],
    }
