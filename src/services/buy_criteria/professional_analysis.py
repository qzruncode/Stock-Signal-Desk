"""Evidence-complete professional buy analysis for A-share securities.

The conversational ``能否买入`` workflow uses the user-required eight Boolean
dimensions. Data collection is program-owned, while the model judges each
dimension in order. The first non-pass result stops subsequent model analysis.
"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Callable, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from src.services.buy_criteria.base import CriterionEvidence, _parse_verdict_json
from src.services.buy_criteria.evaluators.competition_landscape import (
    CompetitionLandscapeEvaluator,
)
from src.services.buy_criteria.evaluators.catalyst_events import (
    CatalystEventsEvaluator,
)
from src.services.buy_criteria.evaluators.fatal_risks import FatalRisksEvaluator
from src.services.buy_criteria.evaluators.growth_drivers import (
    GrowthDriversEvaluator,
)
from src.services.buy_criteria.evaluators.industrial_competitiveness import (
    IndustrialCompetitivenessEvaluator,
)
from src.services.buy_criteria.evaluators.mainline_position import (
    MainlinePositionEvaluator,
)
from src.services.buy_criteria.evaluators.prosperity_cycle import (
    ProsperityCycleEvaluator,
)
from src.services.buy_criteria.mainline_policy import (
    MainlineDirectionRelation,
    MainlineGateClassification,
    MainlineLifecycle,
    MainlineStrategyProfile,
    MainlineTriggerProgress,
    mainline_strategy_label,
    normalize_mainline_strategy,
)
from src.services.buy_criteria.research_enrichment import (
    collect_public_research,
    derive_research_scope,
    research_summary_for_lenses,
)

logger = logging.getLogger(__name__)


PROFESSIONAL_BUY_CONTRACT_VERSION = "professional_eight_dimension_gate_v10"
PROFESSIONAL_BUY_ANALYSIS_MODE = "professional_eight_dimension_boolean_gate"

DIMENSION_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("market_mainline", "本轮产业方向通过所选主线策略"),
    ("industrial_competitiveness", "公司在产业链中有竞争力"),
    ("industry_cycle", "行业处于上升周期，不是存量博弈"),
    ("competition_quality", "公司业务没有严重价格战或内卷"),
    ("growth_drivers", "有政策、技术、需求或供给变化驱动"),
    ("forward_catalysts", "未来6—12个月仍有明确催化"),
    ("valuation_odds", "估值合理，上涨弹性大于下跌风险"),
    ("major_risks", "无重大风险隐患"),
)
DIMENSION_IDS = tuple(item[0] for item in DIMENSION_DEFINITIONS)
DIMENSION_TITLES = dict(DIMENSION_DEFINITIONS)
PUBLIC_RESEARCH_LENSES_BY_DIMENSION: dict[str, set[str]] = {
    "market_mainline": {"market_consensus", "structural_trend"},
    "industrial_competitiveness": {
        "company_position",
        "competition_structure",
    },
    "industry_cycle": {"structural_trend", "cycle_supply_demand"},
    "competition_quality": {
        "competition_structure",
        "company_position",
    },
    "growth_drivers": {"structural_trend", "cycle_supply_demand"},
}

DimensionId = Literal[
    "market_mainline",
    "industrial_competitiveness",
    "industry_cycle",
    "competition_quality",
    "growth_drivers",
    "forward_catalysts",
    "valuation_odds",
    "major_risks",
]
DimensionStatus = Literal["pass", "fail", "insufficient", "not_evaluated"]
ModelDimensionStatus = Literal["pass", "fail"]
RecommendationCode = Literal[
    "conditional_buy",
    "watchlist",
    "wait",
    "avoid",
    "analysis_unavailable",
    "evidence_insufficient",
]


class DimensionAssessment(BaseModel):
    """Program-owned gate state.

    ``insufficient`` is reserved for a critical source or execution outage.
    The analyst model cannot emit it: when usable sources do not affirmatively
    demonstrate a positive buy criterion, the company simply fails that
    criterion for this screening run.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    dimension_id: DimensionId
    status: DimensionStatus
    evaluated_subjects: list[str] = Field(max_length=12)
    headline: str = Field(min_length=2, max_length=120)
    analysis: str = Field(min_length=8, max_length=2400)
    key_evidence: list[str] = Field(default_factory=list, max_length=5)
    counter_evidence: list[str] = Field(default_factory=list, max_length=4)
    monitoring_points: list[str] = Field(default_factory=list, max_length=4)
    mainline_classification: MainlineGateClassification | None = None


class ModelDimensionAssessment(BaseModel):
    """Exact model-visible schema for one semantic gate judgment."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    dimension_id: DimensionId
    status: ModelDimensionStatus
    evaluated_subjects: list[str] = Field(max_length=12)
    headline: str = Field(min_length=2, max_length=120)
    analysis: str = Field(min_length=8, max_length=2400)
    key_evidence: list[str] = Field(default_factory=list, max_length=5)
    counter_evidence: list[str] = Field(default_factory=list, max_length=4)
    monitoring_points: list[str] = Field(default_factory=list, max_length=4)
    mainline_classification: MainlineGateClassification | None = None


class ForcedSchemaResponseError(ValueError):
    """Retain the exact invalid provider payload for one targeted repair."""

    def __init__(
        self,
        message: str,
        payload: dict[str, Any],
        *,
        issues: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.payload = payload
        self.issues = issues or [{
            "pointer": "/choices/0/message/tool_calls",
            "code": "forced_schema_missing",
            "expected": (
                "exactly one forced tool call whose arguments match the "
                "supplied schema"
            ),
            "allowed": [],
        }]


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


class ProfessionalAssessment(BaseModel):
    """Structured output from the dedicated senior-analyst prompt."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    investment_profile: str = Field(min_length=8, max_length=300)
    overall_summary: str = Field(min_length=20, max_length=800)
    core_thesis: str = Field(min_length=8, max_length=500)
    biggest_issue: str = Field(min_length=4, max_length=500)
    recommendation_code: RecommendationCode
    recommendation_reason: str = Field(min_length=8, max_length=600)
    dimensions: list[DimensionAssessment] = Field(min_length=8, max_length=8)
    bull_case_chain: str = Field(min_length=8, max_length=800)
    risk_chain: str = Field(min_length=8, max_length=800)
    monitoring_points: list[str] = Field(min_length=3, max_length=6)
    evidence_gaps: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _validate_dimension_order(self) -> "ProfessionalAssessment":
        actual = tuple(item.dimension_id for item in self.dimensions)
        if actual != DIMENSION_IDS:
            raise ValueError(
                "dimensions must contain the exact eight dimensions in contract order"
            )
        return self


class OverallAssessment(BaseModel):
    """Cross-dimension conclusion generated after all eight axes exist."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    investment_profile: str = Field(min_length=8, max_length=300)
    overall_summary: str = Field(min_length=20, max_length=800)
    core_thesis: str = Field(min_length=8, max_length=500)
    biggest_issue: str = Field(min_length=4, max_length=500)
    recommendation_code: RecommendationCode
    recommendation_reason: str = Field(min_length=8, max_length=600)
    bull_case_chain: str = Field(min_length=8, max_length=800)
    risk_chain: str = Field(min_length=8, max_length=800)
    monitoring_points: list[str] = Field(min_length=3, max_length=6)
    evidence_gaps: list[str] = Field(default_factory=list, max_length=8)


ANALYST_SYSTEM_PROMPT = """\
你是管理A股组合的资深买方分析师。你的任务不是机械筛选，也不是宣传公司，而是回答“现在能否买入”。
你必须把公司、产业、市场、估值和风险放进同一套逻辑中，像正式投委会材料一样同时列出支持证据与反证。

程序按以下八个维度依次调用你。你每次只判断当前维度；当前维度不通过时，程序不会再调用后续维度：
1. 本轮产业方向通过所选主线策略；
2. 公司在产业链中有竞争力；
3. 行业处于上升周期，不是存量博弈；
4. 公司业务没有严重价格战或内卷；
5. 有政策、技术、需求或供给变化驱动；
6. 未来6—12个月仍有明确催化；
7. 估值合理，上涨弹性大于下跌风险；
8. 无重大风险隐患。

状态口径：
- pass：证据链充分，主要反证不足以推翻；
- fail：未达到本次买入筛选的正向证明，或存在足以否决该项的风险。理由必须区分
  “有效反证明确定不符合”和“可用来源未形成准入证明”，不得把后者写成公司事实性不存在；
- 关键来源或分析服务失败由程序生成“分析未完成”，模型不得返回第三种语义状态。

分析纪律：
- 市场主线专指未来1—6个月A股主导产业叙事，必须由机构策略、政策落地、
  产业供需、技术路线、资本开支或连续景气证据建立；1—3年结构性趋势只能作为背景。
  第一维必须先区分产业归属和主线生命周期，再按确认型或前瞻布局型策略判断；
  候选主线在确认型策略下不通过，在前瞻布局型策略下也必须满足结构化分支、
  1—6个月窗口、多类独立证据和已启动的产业触发条件，不能仅凭名称通过。
  资金流、涨跌幅、成交排名、均线、技术指标和个股走势完全不属于第一维证据；
  公司真实受益由第二维判断，公司事件与未来催化由第六维判断；
- 竞争力必须落到产品、份额、技术、客户、盈利能力、成本或正式经营证据，概念关系不算；
- 周期必须检查订单、装机、出货、价格、库存、产能利用率、资本开支或连续财务趋势；
- 内卷必须检查同一细分行业的价格、供给、毛利率、客户议价和产能，不能靠关键词或股价下跌判断；
- 驱动要区分政策、技术、需求、供给，并说明传导到公司收入或利润的路径；
- 催化必须有未来时间窗，同时列正向催化和定增、解禁、减持等反向事件；
- 估值不能只报一个PE。至少结合历史/同行/增长，并做保守下行锚与合理上行锚的赔率分析；
- 风险要检查现金流、应收、存货、客户集中、负债、商誉、质押、减持、解禁、再融资、监管和诉讼；
- 历史事件必须按“事项+年份”建立公告时间线。detected 只证明曾出现过，不代表当前仍在进行；后续正式公告覆盖早期程序阶段。交易所审核通过、证监会注册、发行完成是不同状态，不得互相替代；
- 不能笼统写“证据不足”。已检查有效来源但未形成正向证明时，应写明公开披露边界、
  已使用的替代证据及“不符合本次筛选条件”，不能声称公司事实性没有该能力；
- 数字、日期、产品、客户、订单、产能和行业判断只能来自本轮证据。允许根据证据做明确算术，但必须说明口径；
- 证据有冲突时主动写冲突。不得用模型记忆补全本轮没有的事实，不得编造来源链接。

请只返回符合给定结构的 JSON，不输出 Markdown。禁止自行打总分或用其他维度抵消当前维度。
"""


def _bounded_text(value: Any, limit: int = 8_000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    head = max(1, int(limit * 0.72))
    tail = max(1, limit - head)
    return text[:head] + "\n...[证据包按长度截断]...\n" + text[-tail:]


_NUMERIC_CLAIM_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>%|亿元|万元|元|倍|MW|GW|个月|季度|年|月|日|家|只)",
    re.IGNORECASE,
)
_NUMERIC_RANGE_RE = re.compile(
    r"(?P<start>\d+(?:\.\d+)?)\s*[-—~～至]\s*"
    r"(?P<end>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>%|亿元|万元|元|倍|MW|GW|个月|季度|年|月|日|家|只)",
    re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(
    r"(?P<year>20\d{2})-(?P<month>\d{1,2})-(?P<day>\d{1,2})"
)
_BARE_DECIMAL_RE = re.compile(
    r"(?<![\d./:-])(?P<value>\d+\.\d+)(?![\d])"
    r"(?!\s*(?:%|亿元|万元|元|倍|MW|GW|个月|季度|年|月|日|家|只))",
    re.IGNORECASE,
)


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
        evidence_values.setdefault(unit, []).extend([
            float(match.group("start")),
            float(match.group("end")),
        ])
    for match in _ISO_DATE_RE.finditer(evidence_text):
        evidence_values.setdefault("年", []).append(float(match.group("year")))
        evidence_values.setdefault("月", []).append(float(match.group("month")))
        evidence_values.setdefault("日", []).append(float(match.group("day")))
    evidence_bare_decimals = [
        float(match.group("value"))
        for match in _BARE_DECIMAL_RE.finditer(evidence_text)
    ]

    output_text = json.dumps(assessment.model_dump(), ensure_ascii=False)
    unsupported: list[str] = []
    for match in _NUMERIC_CLAIM_RE.finditer(output_text):
        value = float(match.group("value"))
        unit = match.group("unit").lower()
        candidates = evidence_values.get(unit, [])
        if unit == "%":
            matched = any(abs(value - candidate) <= 0.6 for candidate in candidates)
        elif unit in {"亿元", "万元", "元", "倍", "mw", "gw"}:
            matched = any(
                abs(value - candidate) <= max(0.1, abs(candidate) * 0.015)
                for candidate in candidates
            )
        else:
            matched = any(value == candidate for candidate in candidates)
        if not matched and structured_values:
            if unit == "%":
                matched = any(
                    abs(value - candidate) <= 0.6
                    for candidate in structured_values
                )
            elif unit in {"亿元", "万元", "元", "倍", "mw", "gw"}:
                scale = {
                    "亿元": 100_000_000,
                    "万元": 10_000,
                    "元": 1,
                }.get(unit)
                matched = any(
                    (
                        abs(value - candidate)
                        <= max(0.1, abs(candidate) * 0.015)
                    )
                    or (
                        scale is not None
                        and abs(value * scale - candidate)
                        <= max(1, abs(candidate) * 0.015)
                    )
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
                segment
                for segment in segments
                if not any(claim in segment for claim in unsupported)
            ).strip()
        if isinstance(value, list):
            cleaned_items = [clean(item) for item in value]
            return [
                (
                    redacted_item_text
                    if isinstance(item, str) and not item.strip()
                    else item
                )
                for item in cleaned_items
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


_DANGLING_HEADLINE_RE = re.compile(
    r"(?:为|是|包括|来自|由于|因为|但|且|及|与|和|或|：|:|，|,)$"
)


def _repair_incomplete_dimension_headline(
    assessment: DimensionAssessment,
) -> DimensionAssessment:
    """Replace a visibly truncated headline with the first complete finding."""
    headline = str(assessment.headline or "").strip()
    if len(headline) >= 8 and not _DANGLING_HEADLINE_RE.search(headline):
        return assessment
    candidates = [
        value.strip(" 【】[]")
        for value in re.split(r"[。\n；]", assessment.analysis)
        if value.strip(" 【】[]")
    ]
    replacement = next(
        (
            value
            for value in candidates
            if len(value) >= 8
            and not _DANGLING_HEADLINE_RE.search(value)
        ),
        "",
    )
    if not replacement:
        return assessment
    return assessment.model_copy(
        update={"headline": replacement[:120].rstrip("，,：:")}
    )


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
        context["snapshot_source"] = raw.get(
            "market_mainline_snapshot_source"
        )
        context["snapshot_id"] = raw.get("market_mainline_snapshot_id")
        board_catalog = raw.get("board_catalog")
        board_catalog = (
            board_catalog if isinstance(board_catalog, dict) else {}
        )
        context["direction_board_mapping"] = {
            sector_type: [{
                key: item.get(key)
                for key in ("name", "code", "data_source")
                if item.get(key) is not None
            }
                for item in (
                    (board_catalog.get(sector_type) or {}).get(
                        "matched_items"
                    )
                    or []
                )
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
            "documents": [
                item for item in formal.get("documents") or []
                if isinstance(item, dict)
            ][:3],
        }
        context["business_segments"] = [
            item for item in segments.get("items") or []
            if isinstance(item, dict)
            and str(item.get("category") or "").lower() in {"product", "industry"}
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
                        "title", "name", "source", "org", "publish_date",
                        "publish_time", "date", "report_date",
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
                found.append({
                    "title": str(
                        merged_context.get("title")
                        or merged_context.get("name")
                        or merged_context.get("source")
                        or "原始资料"
                    )[:160],
                    "source": str(
                        merged_context.get("source")
                        or merged_context.get("org")
                        or "公开来源"
                    )[:80],
                    "date": str(
                        merged_context.get("publish_date")
                        or merged_context.get("publish_time")
                        or merged_context.get("report_date")
                        or merged_context.get("date")
                        or ""
                    )[:32],
                    "url": url,
                })
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
        "retrieved_source_count": len({
            str(item.get("url") or "")
            for item in items
            if item.get("url")
        }),
        "retrieval_complete": all(
            status != "retrieval_failed"
            for status in lens_status.values()
        ),
        "errors": list(dict.fromkeys([
            *[str(value) for value in current.get("errors") or []],
            *[str(value) for value in incoming.get("errors") or []],
        ])),
    }


def _source_failure_code(value: Any) -> str:
    text = str(value or "").lower()
    if any(marker in text for marker in ("timeout", "timed out", "超时", "504")):
        return "timeout"
    if any(marker in text for marker in (
        "connection",
        "connecterror",
        "remoteprotocolerror",
        "connection reset",
        "connection aborted",
        "broken pipe",
        "ssl",
        "连接",
    )):
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
            elif (
                isinstance(error, dict)
                and error.get("success") is False
            ):
                append(
                    str(section),
                    str(key),
                    "；".join(
                        str(value)
                        for value in error.get("errors") or []
                    ) or "unavailable",
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
        f"{attempt.get('lens')}/{attempt.get('subject') or '全市场'}: "
        "已完成检索，但公开来源未返回匹配材料"
        for attempt in enrichment.get("attempts") or []
        if isinstance(attempt, dict)
        and attempt.get("status") == "no_matching_public_material"
    ]
    evidence["evidence_gaps"] = [
        *evidence["capability_gaps"],
        *[
            item["summary"]
            for item in evidence["source_failures"]
        ],
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
        section for section in dict.fromkeys(requested_sections)
        if section in DIMENSION_IDS and section not in sections
    ]
    if not requested:
        return
    symbol = str(evidence.get("symbol") or "")
    stock_info = evidence.get("stock_info")
    stock_info = stock_info if isinstance(stock_info, dict) else {}
    collector_sections = [
        section for section in requested
        if section != "valuation_odds"
    ]
    required_lenses = set().union(*(
        PUBLIC_RESEARCH_LENSES_BY_DIMENSION.get(section, set())
        for section in requested
    ))
    existing_lenses = set(
        (evidence.get("public_research") or {}).get("lens_status") or {}
    )
    lenses = required_lenses - existing_lenses
    enrichment: dict[str, Any] = _empty_public_research(
        evidence.get("research_scope") or {}
    )
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
                        **_empty_public_research(
                            evidence.get("research_scope") or {}
                        ),
                        "lens_status": {
                            lens: "retrieval_failed" for lens in lenses
                        },
                        "retrieval_complete": False,
                        "errors": [
                            f"{type(exc).__name__}: {str(exc)[:240]}"
                        ],
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
                    lens: (
                        combined_research.get("lens_status") or {}
                    ).get(lens)
                    for lens in section_lenses
                },
                "items": [
                    item
                    for item in combined_research.get("items") or []
                    if isinstance(item, dict)
                    and item.get("lens") in section_lenses
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
            isinstance(value, dict) and value.get("success") is not False
            for value in valuation_packet.values()
        )
        sections["valuation_odds"] = {
            "success": valuation_sources_ok,
            "evidence_gap": (
                None
                if valuation_sources_ok
                else "本轮估值、预期、同行与行情来源均未取得有效数据"
            ),
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
    mainline_strategy: MainlineStrategyProfile | str = (
        MainlineStrategyProfile.CONFIRMED_MAINLINE
    ),
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
        (
            item
            for item in base_packet.get("items") or []
            if isinstance(item, dict)
        ),
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
            "deterministic_entry_context": payload.get(
                "deterministic_entry_context"
            ),
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
            "lens_status": (
                evidence.get("public_research") or {}
            ).get("lens_status"),
            "retrieved_source_count": (
                evidence.get("public_research") or {}
            ).get("retrieved_source_count"),
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
            } | {
                "snippet": _bounded_text(item.get("snippet"), 420),
            }
            for item in (
                (evidence.get("public_research") or {}).get("items") or []
            )
            if isinstance(item, dict)
        ][:40],
        "company_packet": _compact_base_company_packet(
            evidence.get("base_company_packet")
        ),
        "dimension_evidence": sections,
        "evidence_gaps": evidence.get("evidence_gaps") or [],
        "capability_gaps": evidence.get("capability_gaps") or [],
        "source_failures": evidence.get("source_failures") or [],
        "public_disclosure_limits": (
            evidence.get("public_disclosure_limits") or []
        ),
        "required_dimension_order": [
            {"dimension_id": key, "title": title}
            for key, title in DIMENSION_DEFINITIONS
        ],
    }


def _compact_base_company_packet(value: Any) -> dict[str, Any]:
    """Keep decision-bearing fields and remove repeated source metadata."""
    item = value if isinstance(value, dict) else {}

    def pick(source: Any, keys: tuple[str, ...]) -> dict[str, Any]:
        source = source if isinstance(source, dict) else {}
        return {
            key: source.get(key)
            for key in keys
            if source.get(key) is not None
        }

    snapshot = item.get("snapshot") if isinstance(item.get("snapshot"), dict) else {}
    technical = (
        snapshot.get("technical")
        if isinstance(snapshot.get("technical"), dict)
        else {}
    )
    financials = (
        item.get("financials")
        if isinstance(item.get("financials"), dict)
        else {}
    )
    periods = [
        pick(period, (
            "report_date", "report_period", "revenue", "revenue_yoy",
            "parent_net_profit", "parent_net_profit_yoy",
            "deducted_net_profit", "deducted_net_profit_yoy",
            "gross_margin", "net_margin", "roe", "operating_cash_flow",
            "free_cash_flow", "cash_conversion_ratio", "debt_ratio",
            "accounts_receivable", "inventory", "contract_liabilities",
            "flow_basis",
        ))
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
        "profile": pick(item.get("profile"), (
            "short_name", "company_name", "industry", "industry_eastmoney",
            "listing_date", "main_business", "company_profile",
        )),
        "quote": pick(snapshot.get("quote"), (
            "price", "change_pct", "volume", "amount", "turnover_rate",
            "pe_dynamic", "pb", "data_time", "is_stale",
        )),
        "technical": {
            "indicators": pick(technical.get("indicators"), (
                "close", "ma20", "ma60", "rsi14", "atr14_pct",
                "return_5d_pct", "return_20d_pct", "return_60d_pct",
                "high_20d", "low_20d", "high_60d", "low_60d",
            )),
            **pick(technical, ("data_time", "is_stale", "source")),
        },
        "financials": {
            **pick(financials, ("amount_unit", "ratio_unit", "data_time")),
            "items": periods,
        },
        "business_segments": {
            **pick(segments, ("latest_report_date", "source_url")),
            "items": [
                pick(segment, (
                    "report_date", "category", "segment_name", "revenue",
                    "revenue_share_pct", "gross_profit_share_pct",
                    "gross_margin_pct",
                ))
                for segment in (segments.get("items") or [])[:6]
                if isinstance(segment, dict)
            ],
        },
        "valuation": pick(item.get("valuation"), (
            "trade_date", "current_price", "pe_ttm", "pe_static",
            "pb_mrq", "ps_ttm", "pcf_ttm", "peg_trailing", "peg_forward",
            "forward_pe", "dividend_yield", "history_statistics",
            "positive_pe_percentile", "industry_average", "data_time",
        )),
        "consensus": {
            **pick(consensus, (
                "coverage_available", "coverage_count_latest",
                "coverage_status", "latest_institution_report_date",
                "forecast_warning", "data_time",
            )),
            "estimates": consensus.get("estimates") or [],
            "actuals": consensus.get("actuals") or [],
        },
        "peer_comparison": item.get("peer_comparison") or {},
        "risk_events": {
            **pick(risks, (
                "has_risk_events", "analysis", "data_time",
                "freshness_unknown",
            )),
            "items": risks.get("items") or [],
        },
        "announcements": {
            **pick(announcements, (
                "has_announcements", "analysis", "coverage_start",
                "coverage_end", "data_time",
            )),
            "items": announcements.get("items") or [],
        },
        "capital_flow": item.get("capital_flow") or {},
        "evidence_coverage": item.get("evidence_coverage") or {},
    }


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
    periods = [
        item
        for item in financials.get("items") or []
        if isinstance(item, dict)
    ]
    period_rows = [
        {
            "report_date": item.get("report_date"),
            "report_period": item.get("report_period"),
            "reported_period_revenue_yoy_pct": item.get("revenue_yoy"),
            "reported_period_parent_net_profit_yoy_pct": item.get(
                "parent_net_profit_yoy"
            ),
            "reported_period_deducted_net_profit_yoy_pct": item.get(
                "deducted_net_profit_yoy"
            ),
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
        if len({
            str(item.get("report_period") or item.get("report_date") or "")
            for item in items
        }) == 4
    )
    latest_full_year = complete_years[-1] if complete_years else None
    latest_full_year_items = (
        periods_by_year.get(latest_full_year, [])
        if latest_full_year is not None
        else []
    )
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
            output: money(sum(
                float(item.get(source) or 0)
                for item in latest_full_year_items
            ))
            for output, source in flow_fields.items()
        }
        previous_year_items = periods_by_year.get(
            int(latest_full_year) - 1,
            [],
        )
        if len(previous_year_items) == 4:
            for output, source in flow_fields.items():
                current_value = sum(
                    float(item.get(source) or 0)
                    for item in latest_full_year_items
                )
                previous_value = sum(
                    float(item.get(source) or 0)
                    for item in previous_year_items
                )
                annual_summary[output.replace("_亿元", "_yoy_pct")] = (
                    round(
                        (current_value - previous_value)
                        / abs(previous_value)
                        * 100,
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
        annual_summary.update({
            "year": latest_full_year,
            "year_end_accounts_receivable_亿元": money(
                latest_year_end.get("accounts_receivable")
            ),
            "year_end_inventory_亿元": money(latest_year_end.get("inventory")),
            "year_end_debt_ratio_pct": latest_year_end.get("debt_ratio"),
        })

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
    valuation = (
        packet.get("valuation")
        if isinstance(packet.get("valuation"), dict)
        else {}
    )
    peers = (
        packet.get("peer_comparison")
        if isinstance(packet.get("peer_comparison"), dict)
        else {}
    )
    peer_dimensions = (
        peers.get("dimensions")
        if isinstance(peers.get("dimensions"), dict)
        else {}
    )

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

    peer_growth = compact_peer("growth", (
        "symbol", "name", "eps_growth_3y_cagr_pct",
        "eps_growth_report_year_pct", "eps_growth_ttm_pct",
        "revenue_growth_3y_cagr_pct", "revenue_growth_report_year_pct",
        "revenue_growth_ttm_pct", "net_profit_growth_3y_cagr_pct",
        "net_profit_growth_report_year_pct", "net_profit_growth_ttm_pct",
        "rank",
    ))
    peer_profitability = compact_peer("profitability", (
        "symbol", "name", "roe_3y_average_pct",
        "net_margin_3y_average_pct", "asset_turnover_3y_average",
        "equity_multiplier_3y_average", "rank",
    ))
    peer_valuation = compact_peer("valuation", (
        "symbol", "name", "peg_forward", "pe_ttm", "forward_pe",
        "ps_ttm", "forward_ps", "pb_mrq", "pcf_ttm",
        "ev_ebitda_report_year", "rank",
    ))

    base: dict[str, Any] = {"profile": compact_profile}
    if dimension_id == "market_mainline":
        pass
    elif dimension_id == "industrial_competitiveness":
        base.update({
            "financials": recent_financial_context,
            "business_segments": packet.get("business_segments") or {},
            "peer_growth": peer_growth,
            "peer_profitability": peer_profitability,
        })
    elif dimension_id == "industry_cycle":
        base.update({
            "financials": financial_context,
            "business_segments": packet.get("business_segments") or {},
        })
    elif dimension_id == "competition_quality":
        base.update({
            "financials": recent_financial_context,
            "business_segments": packet.get("business_segments") or {},
            "peer_profitability": peer_profitability,
        })
    elif dimension_id == "growth_drivers":
        base.update({
            "financials": financial_context,
            "business_segments": packet.get("business_segments") or {},
        })
    elif dimension_id == "forward_catalysts":
        base.update({
            "announcements": packet.get("announcements") or {},
            "risk_events": packet.get("risk_events") or {},
        })
    elif dimension_id == "valuation_odds":
        base.update({
            "quote": quote,
            "valuation": valuation,
            "peer_valuation": peer_valuation,
            "peer_growth": peer_growth,
            "financials": recent_financial_context,
        })
    elif dimension_id == "major_risks":
        base.update({
            "financials": recent_financial_context,
            "risk_events": packet.get("risk_events") or {},
            "announcements": packet.get("announcements") or {},
        })
    return base


def _mainline_report_from_evidence(
    evidence: dict[str, Any],
) -> dict[str, Any]:
    section = (
        evidence.get("dimension_evidence")
        if isinstance(evidence.get("dimension_evidence"), dict)
        else {}
    ).get("market_mainline")
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
    return next((
        item
        for item in rows
        if isinstance(item, dict)
        and str(item.get("name") or "").strip() == target
    ), None)


def _early_candidate_eligibility(
    row: dict[str, Any],
    classification: MainlineGateClassification,
) -> tuple[bool, list[str]]:
    """Apply only program-verifiable constraints for an emerging direction."""

    missing: list[str] = []
    branches = {
        str(value).strip()
        for value in row.get("branches") or []
        if str(value).strip()
    }
    relation = MainlineDirectionRelation(
        classification.direction_relation
    )
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
        if isinstance(value, dict)
        and str(value.get("axis") or "").strip()
        and bool(value.get("evidence_refs"))
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
    trigger_assessments = [
        item
        for item in row.get("trigger_assessments") or []
        if isinstance(item, dict)
    ]
    if not any(
        str(item.get("status") or "") in {"met", "partial"}
        for item in trigger_assessments
    ):
        missing.append("至少一个已满足或部分满足的触发条件")
    if MainlineTriggerProgress(
        classification.trigger_progress
    ) not in {
        MainlineTriggerProgress.MET,
        MainlineTriggerProgress.PARTIAL,
    }:
        missing.append("可核验的触发进度")
    classified_lifecycle = MainlineLifecycle(
        classification.lifecycle
    )
    if classified_lifecycle not in {
        MainlineLifecycle.EMERGING,
        MainlineLifecycle.VALIDATING,
    }:
        missing.append("候选主线生命周期")
    if str(row.get("lifecycle") or "") != classified_lifecycle.value:
        missing.append("与候选报告一致的生命周期")
    return not missing, missing


def _enforce_mainline_gate_policy(
    result: DimensionAssessment,
    evidence: dict[str, Any],
) -> DimensionAssessment:
    """Enforce strategy boundaries without replacing the semantic judgment."""

    profile = normalize_mainline_strategy(
        evidence.get("mainline_strategy")
    )
    classification = result.mainline_classification
    if classification is None:
        raise ForcedSchemaResponseError(
            "market_mainline requires mainline_classification",
            result.model_dump(mode="json"),
            issues=[{
                "pointer": "/mainline_classification",
                "code": "mainline_classification_required",
                "expected": (
                    "one typed classification for the requested mainline "
                    "strategy"
                ),
                "allowed": [],
            }],
        )
    if normalize_mainline_strategy(
        classification.strategy_profile
    ) != profile:
        raise ForcedSchemaResponseError(
            "mainline_classification.strategy_profile does not match request",
            result.model_dump(mode="json"),
            issues=[{
                "pointer": (
                    "/mainline_classification/strategy_profile"
                ),
                "code": "mainline_strategy_mismatch",
                "expected": profile.value,
                "allowed": [profile.value],
            }],
        )

    report = _mainline_report_from_evidence(evidence)
    current_rows = report.get("current_mainlines") or []
    candidate_rows = (
        report.get("candidate_mainlines")
        or report.get("future_mainlines")
        or []
    )
    relation = MainlineDirectionRelation(
        classification.direction_relation
    )
    matched_current = _mainline_row_by_name(
        current_rows,
        classification.matched_mainline,
    )
    matched_candidate = _mainline_row_by_name(
        candidate_rows,
        classification.matched_mainline,
    )

    binding_issues: list[dict[str, Any]] = []
    if relation in {
        MainlineDirectionRelation.ACTIVE_BRANCH,
        MainlineDirectionRelation.EMERGING_BRANCH,
    }:
        expected_rows = (
            current_rows
            if relation == MainlineDirectionRelation.ACTIVE_BRANCH
            else candidate_rows
        )
        matched_row = (
            matched_current
            if relation == MainlineDirectionRelation.ACTIVE_BRANCH
            else matched_candidate
        )
        expected_names = [
            str(item.get("name") or "").strip()
            for item in expected_rows
            if isinstance(item, dict)
            and str(item.get("name") or "").strip()
        ]
        if matched_row is None:
            binding_issues.append({
                "pointer": "/mainline_classification/matched_mainline",
                "code": "mainline_resource_binding_invalid",
                "expected": (
                    "one exact mainline name copied from the corresponding "
                    "structured report section"
                ),
                "allowed": expected_names,
            })
        else:
            expected_branches = [
                str(value).strip()
                for value in matched_row.get("branches") or []
                if str(value).strip()
            ]
            if (
                str(classification.matched_branch or "").strip()
                not in expected_branches
            ):
                binding_issues.append({
                    "pointer": "/mainline_classification/matched_branch",
                    "code": "mainline_branch_binding_invalid",
                    "expected": (
                        "one exact branch name copied from the matched "
                        "structured mainline"
                    ),
                    "allowed": expected_branches,
                })
            expected_lifecycle = str(
                matched_row.get("lifecycle") or ""
            ).strip()
            if (
                str(classification.lifecycle or "").strip()
                != expected_lifecycle
            ):
                binding_issues.append({
                    "pointer": "/mainline_classification/lifecycle",
                    "code": "mainline_lifecycle_binding_invalid",
                    "expected": expected_lifecycle,
                    "allowed": [expected_lifecycle],
                })
    elif relation == MainlineDirectionRelation.CORE:
        matched_row = matched_current or matched_candidate
        if matched_row is None:
            binding_issues.append({
                "pointer": "/mainline_classification/matched_mainline",
                "code": "mainline_resource_binding_invalid",
                "expected": (
                    "one exact mainline name copied from the structured report"
                ),
                "allowed": [
                    str(item.get("name") or "").strip()
                    for item in [*current_rows, *candidate_rows]
                    if isinstance(item, dict)
                    and str(item.get("name") or "").strip()
                ],
            })
        else:
            expected_lifecycle = str(
                matched_row.get("lifecycle") or ""
            ).strip()
            if (
                str(classification.lifecycle or "").strip()
                != expected_lifecycle
            ):
                binding_issues.append({
                    "pointer": "/mainline_classification/lifecycle",
                    "code": "mainline_lifecycle_binding_invalid",
                    "expected": expected_lifecycle,
                    "allowed": [expected_lifecycle],
                })
    if binding_issues:
        raise ForcedSchemaResponseError(
            "mainline classification failed structured resource binding",
            result.model_dump(mode="json"),
            issues=binding_issues,
        )

    if result.status == "pass":
        current_branches = {
            str(value).strip()
            for value in (matched_current or {}).get("branches") or []
            if str(value).strip()
        }
        current_lifecycle = str(
            (matched_current or {}).get("lifecycle") or ""
        )
        classified_lifecycle = str(
            classification.lifecycle or ""
        )
        current_relation_bound = (
            relation == MainlineDirectionRelation.CORE
            or (
                relation == MainlineDirectionRelation.ACTIVE_BRANCH
                and str(
                    classification.matched_branch or ""
                ).strip() in current_branches
            )
        )
        current_eligible = (
            matched_current is not None
            and current_relation_bound
            and MainlineLifecycle(classification.lifecycle)
            in {
                MainlineLifecycle.CONFIRMED,
                MainlineLifecycle.EXPANDING,
            }
            and current_lifecycle == classified_lifecycle
        )
        missing: list[str] = []
        early_eligible = False
        if (
            profile == MainlineStrategyProfile.EARLY_POSITIONING
            and matched_candidate is not None
        ):
            early_eligible, missing = _early_candidate_eligibility(
                matched_candidate,
                classification,
            )
        if not current_eligible and not early_eligible:
            candidate_name = str(
                classification.matched_mainline or "该方向"
            )
            conditions = "、".join(missing) or "所选策略的结构化准入条件"
            return result.model_copy(update={
                "status": "fail",
                "headline": (
                    f"{candidate_name}未达到"
                    f"{mainline_strategy_label(profile)}准入条件"
                )[:120],
                "analysis": (
                    f"产业归属或候选关系可以成立，但程序复核发现尚未满足：{conditions}。"
                    "本次只否定所选买入策略下的准入资格，不否定该产业方向本身。"
                ),
                "counter_evidence": list(dict.fromkeys([
                    *result.counter_evidence,
                    *missing,
                ]))[:4],
            })

    if (
        result.status == "fail"
        and relation == MainlineDirectionRelation.EMERGING_BRANCH
        and matched_candidate is not None
    ):
        candidate_name = str(
            classification.matched_mainline or "候选主线"
        )
        branch_name = str(
            classification.matched_branch or "本轮产业方向"
        )
        if profile == MainlineStrategyProfile.CONFIRMED_MAINLINE:
            return result.model_copy(update={
                "headline": (
                    f"{branch_name}属于{candidate_name}候选分支，"
                    "但未通过确认型主线门槛"
                )[:120],
                "analysis": (
                    f"{branch_name}与{candidate_name}的产业归属成立。"
                    "当前失败只表示该候选方向尚未升级为未来1—6个月已确认主导叙事，"
                    "不表示该产业不存在或与上位主题无关。"
                ),
            })
    return result


def _call_professional_model(
    evidence: dict[str, Any],
    *,
    dimension_loader: Callable[[str], None] | None = None,
    precomputed_dimensions: Iterable[DimensionAssessment | dict[str, Any]] = (),
    evaluation_limit: int | None = None,
    on_reasoning: Callable[[str], None] | None = None,
) -> tuple[ProfessionalAssessment | None, str]:
    from src.llm.anthropic_gateway import completion_gateway
    from src.storage import persist_llm_usage

    def field(value: Any, name: str) -> Any:
        return value.get(name) if isinstance(value, dict) else getattr(value, name, None)

    def response_failure_payload(response: Any) -> dict[str, Any]:
        choices = field(response, "choices") or []
        if not choices:
            return {
                "choices": [],
                "model": field(response, "model"),
                "usage": normalized_usage(response),
            }
        choice = choices[0]
        message = field(choice, "message")
        return {
            "finish_reason": field(choice, "finish_reason"),
            "content": field(message, "content"),
            "reasoning_content": field(message, "reasoning_content"),
            "tool_calls": field(message, "tool_calls") or [],
            "model": field(response, "model"),
            "usage": normalized_usage(response),
        }

    def payload_from_response(response: Any, tool_name: str) -> dict[str, Any]:
        choices = field(response, "choices") or []
        if not choices:
            payload = response_failure_payload(response)
            raise ForcedSchemaResponseError(
                "professional analysis response has no choices",
                payload,
                issues=[{
                    "pointer": "/choices",
                    "code": "choices_missing",
                    "expected": (
                        "one response choice containing the forced tool call"
                    ),
                    "allowed": [tool_name],
                }],
            )
        choice = choices[0]
        message = field(choice, "message")
        tool_calls = field(message, "tool_calls") or []
        for call in tool_calls:
            function = field(call, "function")
            if field(function, "name") != tool_name:
                continue
            arguments = field(function, "arguments")
            try:
                payload = (
                    json.loads(arguments)
                    if isinstance(arguments, str)
                    else arguments
                )
            except json.JSONDecodeError as exc:
                raise ForcedSchemaResponseError(
                    "professional analysis tool arguments are not valid JSON",
                    {
                        "tool_name": tool_name,
                        "arguments": arguments,
                    },
                    issues=[{
                        "pointer": "/arguments",
                        "code": "json_invalid",
                        "expected": "one valid JSON object matching the schema",
                        "allowed": [],
                    }],
                ) from exc
            if not isinstance(payload, dict):
                raise ForcedSchemaResponseError(
                    "professional analysis tool arguments are not an object",
                    {
                        "tool_name": tool_name,
                        "arguments": payload,
                    },
                    issues=[{
                        "pointer": "/arguments",
                        "code": "object_type_required",
                        "expected": "JSON object",
                        "allowed": [],
                    }],
                )
            return payload
        # Compatibility fallback for gateways that return forced JSON as text.
        content = field(message, "content")
        if isinstance(content, str):
            parsed = _parse_verdict_json(content)
            if isinstance(parsed, dict):
                return parsed
        reasoning = field(message, "reasoning_content")
        failure_payload = response_failure_payload(response)
        raise ForcedSchemaResponseError(
            (
                "professional analysis response did not call the forced schema"
                f" (finish={field(choice, 'finish_reason')},"
                f" content_chars={len(content) if isinstance(content, str) else 0},"
                f" reasoning_chars={len(reasoning) if isinstance(reasoning, str) else 0})"
            ),
            failure_payload,
            issues=[{
                "pointer": "/choices/0/message/tool_calls",
                "code": "forced_schema_missing",
                "expected": (
                    f"exactly one {tool_name} tool call whose arguments "
                    "match the supplied schema"
                ),
                "allowed": [tool_name],
            }],
        )

    def validation_issues(exc: ValidationError) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        for error in exc.errors(include_url=False):
            location = error.get("loc") or ()
            pointer = "/" + "/".join(
                str(value).replace("~", "~0").replace("/", "~1")
                for value in location
            )
            issues.append({
                "pointer": pointer or "/",
                "code": str(error.get("type") or "schema_invalid"),
                "expected": str(error.get("msg") or "value matching schema"),
                "allowed": (
                    error.get("ctx")
                    if isinstance(error.get("ctx"), dict)
                    else []
                ),
            })
        return issues

    def normalized_usage(response: Any) -> dict[str, int]:
        usage = field(response, "usage")
        return {
            key: int(field(usage, key) or 0)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        }

    def collect_streamed_response(response_stream: Any) -> dict[str, Any]:
        tool_arguments: dict[int, list[str]] = {}
        tool_names: dict[int, str] = {}
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        model_used = "anthropic-gateway"
        usage: dict[str, int] = {}
        finish_reason: Any = None
        for chunk in response_stream:
            model_used = str(field(chunk, "model") or model_used)
            chunk_usage = normalized_usage(chunk)
            if any(chunk_usage.values()):
                usage = chunk_usage
            choices = field(chunk, "choices") or []
            if not choices:
                continue
            choice = choices[0]
            finish_reason = field(choice, "finish_reason") or finish_reason
            delta = field(choice, "delta")
            content = field(delta, "content")
            if isinstance(content, str) and content:
                content_parts.append(content)
            reasoning_values: list[str] = []
            for name in ("reasoning_content", "reasoning"):
                value = field(delta, name)
                if isinstance(value, str) and value:
                    reasoning_values.append(value)
            model_extra = field(delta, "model_extra")
            if isinstance(model_extra, dict):
                for name in ("reasoning_content", "reasoning"):
                    value = model_extra.get(name)
                    if (
                        isinstance(value, str)
                        and value
                        and value not in reasoning_values
                    ):
                        reasoning_values.append(value)
            reasoning_delta = "".join(reasoning_values)
            if reasoning_delta:
                reasoning_parts.append(reasoning_delta)
                if on_reasoning:
                    on_reasoning(reasoning_delta)
            for tool_call in field(delta, "tool_calls") or []:
                raw_index = field(tool_call, "index")
                try:
                    index = int(raw_index or 0)
                except (TypeError, ValueError):
                    index = 0
                function = field(tool_call, "function")
                name = field(function, "name")
                if isinstance(name, str) and name:
                    current_name = tool_names.get(index, "")
                    tool_names[index] = (
                        name
                        if not current_name or current_name == name
                        else current_name + name
                    )
                arguments = field(function, "arguments")
                if isinstance(arguments, str) and arguments:
                    tool_arguments.setdefault(index, []).append(arguments)
        return {
            "model": model_used,
            "choices": [{
                "finish_reason": finish_reason,
                "message": {
                    "content": "".join(content_parts),
                    "reasoning_content": "".join(reasoning_parts),
                    "tool_calls": [
                        {
                            "function": {
                                "name": tool_names.get(index),
                                "arguments": "".join(parts),
                            },
                        }
                        for index, parts in sorted(tool_arguments.items())
                    ],
                },
            }],
            "usage": usage,
        }

    def forced_call(
        *,
        tool_name: str,
        description: str,
        system_prompt: str,
        user_prompt: str,
        response_model: type[BaseModel],
        max_tokens: int,
        call_type: str,
    ) -> BaseModel:
        schema = _inline_json_schema(response_model.model_json_schema())
        tool = {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": description,
                "parameters": schema,
            },
        }
        request = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "tools": [tool],
            "tool_choice": {
                "type": "function",
                "function": {"name": tool_name},
            },
            "temperature": 0.1,
            "max_tokens": max_tokens,
        }
        if on_reasoning:
            response = collect_streamed_response(
                completion_gateway(
                    **request,
                    stream=True,
                    stream_options={"include_usage": True},
                )
            )
        else:
            response = completion_gateway(
                **request,
            )
        persist_llm_usage(
            normalized_usage(response),
            str(field(response, "model") or "anthropic-gateway"),
            call_type,
            stock_code=str(evidence.get("symbol") or "").strip() or None,
        )
        payload = payload_from_response(response, tool_name)
        try:
            parsed = response_model.model_validate(payload)
        except ValidationError as exc:
            raise ForcedSchemaResponseError(
                "professional analysis payload failed exact schema validation",
                payload,
                issues=validation_issues(exc),
            ) from exc
        return parsed

    evidence_view = _prompt_evidence_view(evidence)
    strategy_profile = normalize_mainline_strategy(
        evidence_view.get("mainline_strategy")
    )
    stock_info = evidence_view.get("stock_info")
    stock_info = stock_info if isinstance(stock_info, dict) else {}
    compact_stock_info = {
        key: stock_info.get(key)
        for key in (
            "symbol",
            "name",
            "short_name",
            "industry",
            "main_business",
            "business_scope",
        )
        if stock_info.get(key)
    }

    adjacent_evidence = {
        dimension_id: set() for dimension_id in DIMENSION_IDS
    }
    dimension_instructions = {
        "market_mainline": (
            "先识别未来1—6个月A股占主导的产业叙事，再说明本轮结构化产业方向"
            "处于主线核心、活跃分支、候选分支、仅有长期趋势还是无关。必须实际核验"
            " public_research 的 market_consensus、structural_trend 与市场主线报告；"
            "主线报告不可用不等于其他证据不可用。中期主线只能由机构策略、政策落地、"
            "产业供需、技术路线、资本开支或连续景气证据建立。1—3年长期趋势不能冒充"
            "当前主线；资金流、涨跌幅、成交排名、均线、技术指标和个股走势完全不得进入"
            "本维度判断。板块目录只用于名称映射，目录存在不是主线证据。最终 headline"
            " 必须同时点明中期主导叙事和本轮产业方向的层级关系。本轮提供结构化产业方向时，"
            "不得因某家公司尚未证明真实业务暴露而把产业方向判为非主线；公司是否真实受益"
            "留给第二维，公司事件与未来催化留给第六维。"
            f"本轮主线投资口径为 {strategy_profile.value}"
            f"（{mainline_strategy_label(strategy_profile)}）。"
            "必须填写 mainline_classification，matched_mainline 必须逐字复制报告中的"
            "当前或候选主线名称，matched_branch 必须逐字复制其结构化 branches；"
            "若判断核心主线可不填 matched_branch。确认型口径只接受已确认/扩散期的当前"
            "主线核心或活跃分支。前瞻布局型还可以接受候选核心或分支，但必须核验"
            "one_to_six_months 窗口、至少两类 evidence_axes 以及至少一个 met/partial"
            " trigger_assessment。"
        ),
        "industrial_competitiveness": (
            "精确市场份额、客户名称或专利数量不是唯一合格证据。若正式分部收入占比、"
            "盈利能力、同行相对位置、产品商业化、技术参数、垂直一体化或规模成本能够互相"
            "印证，可以形成判断；只有可选指标未公开不得机械降级。概念关系仍然不算竞争力。"
            "现金流、应收和存货属于经营质量/风险，不得在缺少客户流失、份额下降或同业对比"
            "证据时用来否定产业竞争力；也不得在没有同细分可比基准时把某个绝对毛利率评价为"
            "偏低或偏高。若正式主营占比、头部客户、同行位置和产业链一体化已交叉印证，应按"
            "竞争地位本身判断，不被其他维度问题重复扣分。"
        ),
        "industry_cycle": (
            "公司单个季度收入或利润下降不能证明整个细分产业进入下行周期。优先使用产业的"
            "订单、装机/出货、价格、库存、产能利用率、资本开支和政策执行证据，再用公司连续"
            "经营数据验证兑现。法定大行业统计不能替代真实细分产品供需。public_research"
            "已经返回的装机、订单和供需材料必须实际核验，不能再声称本轮没有取得。若行业"
            "装机、订单、价格等直接证据明确处于上行，公司一个季度增速转负或现金流偏弱只能"
            "作为兑现节奏监控，不能单独把行业周期降级。若上一维已经确认公司的真实相关业务，"
            "则该相关业务连续多个可比期的收入、订单、出货、利润或毛利率改善，本身可以作为"
            "公司兑现路径证明景气；产业供需没有明确反向时，不得仅因缺少行业总量统计就机械"
            "判定不符合本次准入条件。公司兑现和产业供需是两条可替代的主证据链，"
            "不要求两者同时齐全。"
        ),
        "competition_quality": (
            "应收账款、存货或经营现金流反映回款与营运资金压力，不能单独证明价格战或客户"
            "议价恶化。价格下降、同质化扩产、份额恶化、连续毛利率受压等直接证据优先；稳定"
            "毛利率、同行盈利能力和差异化产品可作为竞争秩序仍健康的正面证据。若无价格战、"
            "份额恶化或连续毛利率受压的直接证据，不得仅因应收、存货或现金流偏弱判为内卷；"
            "这些指标应转交重大风险维度解释。"
        ),
        "valuation_odds": (
            "只用基本面估值锚、历史分位、同行可比、增长兑现和保守情景判断赔率。技术止损、"
            "均线、ATR、20日高低点和机械目标价不得用于证明基本面下行有限，也不得成为pass依据。"
        ),
        "forward_catalysts": (
            "只接受证据明确披露且位于未来6—12个月的事件窗口。财报只有在本轮证据包含预约"
            "披露日时才能写具体日期或月份；不得按惯例猜测中报、三季报月份，也不得把例行财报"
            "本身包装成催化。审批、投产、达产、客户验证和订单时间均不得自行预测。"
        ),
        "major_risks": (
            "把公告按事项和年份建立时间线。风险工具只提供原始证据，不提供事件类型、严重度"
            "或当前状态；必须根据内容研判。后续回复、审核决定、注册、发行结果、判决或终止"
            "公告覆盖早期阶段。"
            "不同融资/交易方案不得串联：旧方案已发行不代表新方案已完成；交易所审核通过也"
            "不等于证监会注册或发行完成。对现金流、应收、存货等说明风险传导，不得把风险信号"
            "夸大成已经爆雷。质押次数或补充质押标题本身不能证明控股股东资金链紧张，只有"
            "累计质押比例、平仓风险、违约或伴随减持等正式证据才能支持该判断。"
        ),
    }
    dimension_system_prompt = (
        "你是管理A股组合的资深买方分析师。只核验程序指定的一个买入维度，"
        "同时写支持证据、主要反证和后续验证。状态只能是 pass 或 fail；"
        "只有当前维度的核心条件被证据充分支持时才可 pass。未形成正向准入证明或存在"
        "足以否决该项的风险时必须 fail。fail 表示不符合本次买入筛选条件，不必然表示"
        "公司事实性没有相关业务、能力或事件；analysis 必须明确区分有效反证与公开资料"
        "未形成正向证明。关键来源整体失败由程序中止分析，模型不得猜测或返回系统状态。"
        "市场主线专指未来1—6个月A股主导产业叙事；长期趋势只能作为背景，"
        "资金流、涨跌幅、成交排名、均线、技术指标和个股走势完全不得进入第一维。"
        "第一维只判断本轮结构化产业方向的市场属性，不判断个股买点或公司真实受益；"
        "公司业务连接、订单、收入兑现和竞争优势留给第二维；"
        "不得仅凭分部名称把现有终端或零部件写成“传统”或“非AI”；"
        "主线报告不可用时不能把缺失本身写成方向事实性不存在，应根据其余可用证据判断"
        "是否达到本次主线准入证明。市场主线维度必须填写 mainline_classification，"
        "其他七个维度必须将该字段留空。"
        "概念关系不能证明竞争力；周期看供需和连续经营数据；内卷看同一细分行业的价格、"
        "供给、毛利率和客户议价。催化只接受证据中明确披露的事件与时间窗，"
        "不得自行预测审批月份、投产日期、订单或业绩。估值必须用基本面估值锚、历史或同行、"
        "增长兑现和上下行情景讨论赔率；技术止损、均线与ATR只能描述交易位置，"
        "绝不能证明基本面下行风险被锁定。重大风险项的 pass 表示“没有重要隐患”；"
        "现金流显著弱于利润、应收存货或客户集中度较高时必须实质评估，"
        "不能因为尚未爆雷就写成风险可控。没查到造假、处罚或诉讼不等于确认不存在。"
        "company_context 中的结构化财务、估值和风险字段优先级高于新闻或研报概述；"
        "latest_full_year 是程序从数据中识别的最近完整会计年度；年度金额由四个单季求和，"
        "其中显式的年度同比字段才可用于全年同比。quarterly中的reported_period_*_yoy_pct"
        "保留公司披露的截至报告期同比口径：一季报可视为单季同比，中报和三季报通常是年初至"
        "报告期累计同比，年报是全年同比；不得把中报或三季报同比写成单季同比，也不得把四个"
        "报告期同比相加或平均。"
        "事项名称中的方案年度不是公告发生年份，例如“2025年度方案”可能在2026年审核；"
        "所有进展日期必须逐字复制正式公告日期，禁止把方案年度嫁接到公告月份。"
        "仅有补充质押公告而没有累计质押比例时，不得推断高比例或伴随减持；"
        "再融资问询及回复不等于监管处罚，也不能仅凭关键词判断当前生命周期。"
        "主线报告缺失但机构策略、政策与产业证据仍可用时，"
        "应基于现有证据判断 pass/fail；关键中期叙事来源整体失败由程序中止本维度。"
        "只使用本轮证据，禁止补造数字、同行区间、客户、原因解释、行业数据或未来日期；"
        "所有数字必须原样复制证据，不做格式转换、舍入或自设阈值；"
        "public_research 中 source_quality=ugc_lead_only 的材料只能作为线索，必须有正式披露、"
        "专业来源或结构化市场数据交叉验证后才能写入关键证据；不得把自媒体标题当事实。"
        "禁止笼统写“证据不足”：若来源有效但没有披露精确指标，写“已核对的公开披露未提供"
        "某精确指标”，说明替代证据，并判断是否达到本次准入证明。"
        "非关键可选指标未披露不得机械判 fail。evaluated_subjects始终返回数组："
        "存在required_scope_confirmation.subjects"
        "时逐字复制实际判断的结构化方向，否则填写实际采用的投资逻辑或公司业务方向。"
        "analysis应凝练、避免重复证据列表并以完整句结束；"
        "三个证据字段始终使用数组。"
    )

    def dimension_request(
        definition: tuple[str, str],
        *,
        retry: bool = False,
        repair_error: Exception | None = None,
    ) -> DimensionAssessment:
        dimension_id, title = definition
        section_names = {dimension_id, *adjacent_evidence.get(dimension_id, set())}
        sections = {
            key: {
                "success": value.get("success"),
                "evidence_gap": value.get("evidence_gap"),
                "summary": _bounded_text(
                    (
                        "技术止损、均线、ATR和机械目标价已从基本面估值判断中排除；"
                        "请只使用company_context的结构化估值、同行、增长和财务质量。"
                        if key == "valuation_odds"
                        else value.get("summary")
                    ),
                    850 if retry else 1_250,
                ),
                "structured_context": value.get("structured_context") or {},
                "deterministic_entry_context": None,
            }
            for key, value in evidence_view["dimension_evidence"].items()
            if key in section_names and isinstance(value, dict)
        }
        company_context = _dimension_company_context(
            (
                evidence_view.get("company_packet")
                if isinstance(evidence_view.get("company_packet"), dict)
                else {}
            ),
            dimension_id,
        )
        if retry and dimension_id == "major_risks":
            financial = company_context.get("financials")
            financial = financial if isinstance(financial, dict) else {}
            company_context = {
                "profile": company_context.get("profile") or {},
                "financials": {
                    "basis": financial.get("basis"),
                    "latest_full_year": financial.get("latest_full_year"),
                    "latest_quarters": financial.get("latest_quarters"),
                },
                "risk_events": company_context.get("risk_events") or {},
            }
        request = {
            "stock_info": compact_stock_info,
            "requested_at": evidence_view.get("requested_at"),
            "investment_thesis": evidence_view.get("investment_thesis"),
            "thesis_context": evidence_view.get("thesis_context"),
            "mainline_strategy": strategy_profile.value,
            "required_scope_confirmation": {
                "subjects": _structured_thesis_labels(
                    evidence_view.get("thesis_context")
                ),
                "instruction": (
                    "evaluated_subjects 必须逐字复制本轮实际判断的结构化产业方向；"
                    "不得用公司法定行业或公开搜索自行替换。"
                ),
            },
            "research_scope": evidence_view.get("research_scope"),
            "public_research_coverage": evidence_view.get(
                "public_research_coverage"
            ),
            "public_research": [
                item
                for item in evidence_view.get("public_research_evidence") or []
                if isinstance(item, dict)
                and item.get("lens")
                in PUBLIC_RESEARCH_LENSES_BY_DIMENSION.get(
                    dimension_id,
                    set(),
                )
            ][:8],
            "company_context": company_context,
            "requested_dimension": {
                "dimension_id": dimension_id,
                "title": title,
                "professional_instruction": dimension_instructions.get(
                    dimension_id,
                    "使用本轮证据完成本维度的支持、反证与边界分析。",
                ),
            },
            "prior_dimensions": [
                item.model_dump(mode="json")
                for item in dimensions
                if item.status != "not_evaluated"
            ],
            "dimension_evidence": sections,
            "capability_gaps": [
                gap
                for gap in evidence_view.get("capability_gaps") or []
                if any(section in str(gap) for section in section_names)
            ][:6],
            "source_failures": [
                failure
                for failure in evidence_view.get("source_failures") or []
                if isinstance(failure, dict)
                and (
                    failure.get("section") in section_names
                    or failure.get("section") == "base_company_packet"
                    or (
                        failure.get("section") == "public_research"
                        and failure.get("source")
                        in PUBLIC_RESEARCH_LENSES_BY_DIMENSION.get(
                            dimension_id,
                            set(),
                        )
                    )
                )
            ][:8],
            "public_disclosure_limits": [
                gap
                for gap in evidence_view.get("public_disclosure_limits") or []
            ][:6],
        }
        if retry and repair_error is not None:
            invalid_payload = (
                repair_error.payload
                if isinstance(repair_error, ForcedSchemaResponseError)
                else {"error": f"{type(repair_error).__name__}: {repair_error}"}
            )
            request["targeted_repair"] = {
                "invalid_payload": invalid_payload,
                "issues": (
                    repair_error.issues
                    if isinstance(repair_error, ForcedSchemaResponseError)
                    else [{
                        "pointer": "/choices/0/message/tool_calls",
                        "code": "forced_schema_missing",
                        "expected": (
                            "exactly one submit_dimension_assessment tool call "
                            "whose arguments match the supplied schema"
                        ),
                        "allowed": ["submit_dimension_assessment"],
                    }]
                ),
                "instruction": (
                    "只修复上述结构化输出错误；保持同一维度、同一证据和同一 JSON Schema，"
                    "不得重做任务图或改判其他维度。"
                ),
            }
        user_prompt = json.dumps(request, ensure_ascii=False, default=str)
        raw_sections = evidence.get("dimension_evidence")
        raw_sections = raw_sections if isinstance(raw_sections, dict) else {}
        validation_evidence = {
            "requested_at": evidence.get("requested_at"),
            "thesis_context": evidence.get("thesis_context"),
            "base_company_packet": evidence.get("base_company_packet"),
            "public_research": request.get("public_research") or [],
            "structured_context": {
                key: sections[key].get("structured_context") or {}
                for key in sections
            },
            "dimension_evidence": {
                key: raw_sections.get(key)
                for key in section_names
                if key in raw_sections
            },
        }
        active_system_prompt = dimension_system_prompt + (
            "这是同一失败维度的定点恢复调用。上一次完整非法 payload 和字段错误已附在"
            " targeted_repair；只返回最小但完整的合规判断，"
            "优先使用company_context中的结构化字段，删除任何无法逐字或等值追溯的数字。"
            if retry
            else ""
        )
        result = forced_call(
            tool_name="submit_dimension_assessment",
            description="提交程序指定的一个买入分析维度",
            system_prompt=active_system_prompt,
            user_prompt=user_prompt,
            response_model=ModelDimensionAssessment,
            # Provider-side reasoning counts against this budget. Leave enough
            # room for the reasoning plus the required typed tool call.
            max_tokens=16_000 if retry else 20_000,
            call_type="professional_buy_dimensions",
        )
        if not isinstance(result, ModelDimensionAssessment):
            raise ValueError("dimension response has unexpected type")
        result = DimensionAssessment.model_validate(result.model_dump())
        if result.dimension_id != dimension_id:
            raise ValueError(
                f"模型返回维度 {result.dimension_id}，预期 {dimension_id}"
            )
        if result.status == "not_evaluated":
            raise ValueError(
                "模型不得把当前已执行维度标记为 not_evaluated"
            )
        if dimension_id == "market_mainline":
            result = _enforce_mainline_gate_policy(result, evidence)
        elif result.mainline_classification is not None:
            raise ForcedSchemaResponseError(
                "mainline_classification is only valid for market_mainline",
                result.model_dump(mode="json"),
                issues=[{
                    "pointer": "/mainline_classification",
                    "code": "field_not_allowed_for_dimension",
                    "expected": "null",
                    "allowed": [None],
                }],
            )
        required_subjects = _structured_thesis_labels(
            evidence_view.get("thesis_context")
        )
        confirmed_subjects = {
            str(value).strip() for value in result.evaluated_subjects
            if str(value).strip()
        }
        if required_subjects and not set(required_subjects).intersection(
            confirmed_subjects
        ):
            raise ValueError(
                "模型没有确认本轮结构化产业方向，拒绝接受脱离主题的维度判断"
            )
        unsupported_claims = _unsupported_numeric_claims(
            result,
            validation_evidence,
        )
        if unsupported_claims:
            logger.warning(
                "professional buy redacted unsupported numeric claims for %s/%s: %s",
                evidence.get("symbol"),
                dimension_id,
                "、".join(unsupported_claims[:8]),
            )
            result = _redact_unsupported_numeric_claims(
                result,
                unsupported_claims,
            )
        return _repair_incomplete_dimension_headline(result)

    dimensions = [
        (
            item
            if isinstance(item, DimensionAssessment)
            else DimensionAssessment.model_validate(item)
        )
        for item in precomputed_dimensions
    ]
    if len(dimensions) > len(DIMENSION_IDS):
        raise ValueError("precomputed dimensions exceed the eight-gate contract")
    if tuple(item.dimension_id for item in dimensions) != DIMENSION_IDS[
        :len(dimensions)
    ]:
        raise ValueError(
            "precomputed dimensions must be an exact prefix of the eight gates"
        )
    model_errors: list[str] = []
    blocked = bool(dimensions and dimensions[-1].status != "pass")
    evaluated_in_call = 0
    for dimension_id, title in DIMENSION_DEFINITIONS[len(dimensions):]:
        if blocked:
            dimensions.append(DimensionAssessment(
                dimension_id=dimension_id,
                status="not_evaluated",
                evaluated_subjects=_structured_thesis_labels(
                    evidence_view.get("thesis_context")
                ),
                headline="前序布尔闸门已关闭",
                analysis="前一维度未通过，本维度按固定状态机不再执行，不能用于抵消首个阻断项。",
                key_evidence=[],
                counter_evidence=[],
                monitoring_points=[],
            ))
            continue
        if (
            evaluation_limit is not None
            and evaluated_in_call >= evaluation_limit
        ):
            dimensions.append(DimensionAssessment(
                dimension_id=dimension_id,
                status="not_evaluated",
                evaluated_subjects=_structured_thesis_labels(
                    evidence_view.get("thesis_context")
                ),
                headline="当前共享评估范围不包含本维度",
                analysis=(
                    "本次只生成批次共享的市场主线结论；公司级维度将在逐股流程中继续执行。"
                ),
                key_evidence=[],
                counter_evidence=[],
                monitoring_points=[],
            ))
            continue

        if (
            dimension_id not in evidence.get("dimension_evidence", {})
            and dimension_loader is not None
        ):
            try:
                dimension_loader(dimension_id)
            except Exception as exc:
                logger.warning(
                    "professional buy lazy evidence %s failed for %s: %s",
                    dimension_id,
                    evidence.get("symbol"),
                    exc,
                )
                evidence.setdefault("dimension_evidence", {})[dimension_id] = {
                    "success": False,
                    "evidence_gap": (
                        f"{type(exc).__name__}: {str(exc)[:240]}"
                    ),
                    "summary": "该证据维度按需获取失败。",
                    "raw_data": {},
                    "structured_context": {},
                }
            evidence_view = _prompt_evidence_view(evidence)

        current_section = (
            evidence_view.get("dimension_evidence") or {}
        ).get(dimension_id)
        if (
            not isinstance(current_section, dict)
            or current_section.get("success") is False
        ):
            gap = str(
                (current_section or {}).get("evidence_gap")
                or "当前维度关键证据未取得"
            )
            result = DimensionAssessment(
                dimension_id=dimension_id,
                status="insufficient",
                evaluated_subjects=_structured_thesis_labels(
                    evidence_view.get("thesis_context")
                ),
                headline=f"{title}的关键取证未完成",
                analysis=(
                    f"{gap}。程序将本轮标记为分析未完成并关闭后续闸门，"
                    "不会把取证失败写成公司的事实性结论。"
                ),
                key_evidence=[],
                counter_evidence=[],
                monitoring_points=[f"重新取得“{title}”的关键证据"],
            )
            dimensions.append(result)
            blocked = True
            continue

        try:
            result = dimension_request((dimension_id, title))
        except Exception as first_error:
            try:
                result = dimension_request(
                    (dimension_id, title),
                    retry=True,
                    repair_error=first_error,
                )
            except Exception as retry_error:
                error = (
                    f"{title}: {type(retry_error).__name__}: "
                    f"{str(retry_error)[:180]}"
                )
                model_errors.append(error)
                logger.warning(
                    "professional buy %s failed for %s; first=%s",
                    error,
                    evidence.get("symbol"),
                    first_error,
                )
                result = DimensionAssessment(
                    dimension_id=dimension_id,
                    status="insufficient",
                    evaluated_subjects=_structured_thesis_labels(
                        evidence_view.get("thesis_context")
                    ),
                    headline="当前维度的专业复核未完成",
                    analysis=(
                        f"模型没有返回“{title}”的有效结构化判断，"
                        "程序将本轮标记为分析未完成并关闭后续闸门。"
                    ),
                    key_evidence=[],
                    counter_evidence=[],
                    monitoring_points=[f"重新核验“{title}”"],
                )
        dimensions.append(result)
        evaluated_in_call += 1
        if result.status != "pass":
            blocked = True

    if blocked or any(
        item.status == "not_evaluated" for item in dimensions
    ):
        error = "；".join(model_errors)
        return _derive_overall_from_dimensions(
            dimensions,
            compact_stock_info,
            model_errors,
        ), error

    compact_dimensions = [
        {
            "dimension_id": item.dimension_id,
            "status": item.status,
            "headline": item.headline,
            "key_evidence": item.key_evidence[:1],
            "counter_evidence": item.counter_evidence[:1],
            "monitoring_points": item.monitoring_points[:1],
        }
        for item in dimensions
    ]
    overall_request = {
        "stock_info": compact_stock_info,
        "requested_at": evidence_view.get("requested_at"),
        "investment_thesis": evidence_view.get("investment_thesis"),
        "dimensions": compact_dimensions,
        "evidence_gaps": (evidence_view.get("evidence_gaps") or [])[:6],
    }
    overall_system_prompt = (
        "你是A股投委会的资深分析师。八个维度已经逐项完成，你只负责形成一致的结论先行摘要。"
        "不得改变任何维度的状态，不得补造数字。请定义投资画像、核心逻辑、最大问题、"
        "当前建议、看多传导链、风险传导链和至少三个有明确数据口径的监控指标；"
        "证据没有给出目标值或阈值时，不得自行设定。"
        "调用本步骤意味着八个布尔闸门已经全部 pass，recommendation_code 必须为"
        " conditional_buy；不得改成评分、加权或跨维度抵消。"
    )
    try:
        overall = forced_call(
            tool_name="submit_overall_assessment",
            description="提交八维分析的统一投委会结论",
            system_prompt=overall_system_prompt,
            user_prompt=json.dumps(overall_request, ensure_ascii=False, default=str),
            response_model=OverallAssessment,
            max_tokens=16_000,
            call_type="professional_buy_overall",
        )
        if not isinstance(overall, OverallAssessment):
            raise ValueError("overall response has unexpected type")
        unsupported_overall_claims = _unsupported_numeric_claims(
            overall,
            {
                "request": overall_request,
                "validated_dimensions": [
                    item.model_dump() for item in dimensions
                ],
            },
        )
        if unsupported_overall_claims:
            logger.warning(
                "professional buy redacted unsupported overall numeric claims for %s: %s",
                evidence.get("symbol"),
                "、".join(unsupported_overall_claims[:8]),
            )
            overall = _redact_unsupported_numeric_claims(
                overall,
                unsupported_overall_claims,
            )
            if not isinstance(overall, OverallAssessment):
                raise ValueError("redacted overall response has unexpected type")
    except Exception as exc:
        error = f"综合结论: {type(exc).__name__}: {str(exc)[:240]}"
        logger.warning("professional buy overall failed for %s: %s", evidence.get("symbol"), error)
        return _derive_overall_from_dimensions(
            dimensions,
            compact_stock_info,
            [error],
        ), error

    try:
        return ProfessionalAssessment(
            **overall.model_dump(),
            dimensions=dimensions,
        ), ""
    except Exception as exc:
        return None, f"ProfessionalAssessment: {type(exc).__name__}: {str(exc)[:240]}"


def _derive_overall_from_dimensions(
    dimensions: list[DimensionAssessment],
    stock_info: dict[str, Any],
    errors: list[str],
) -> ProfessionalAssessment:
    """Render the Boolean gate state without inventing a scoring fallback."""
    counts = {
        status: sum(1 for item in dimensions if item.status == status)
        for status in ("pass", "fail", "insufficient", "not_evaluated")
    }
    name = str(
        stock_info.get("name")
        or stock_info.get("short_name")
        or stock_info.get("symbol")
        or "该公司"
    )
    executed = [
        item for item in dimensions
        if item.status != "not_evaluated"
    ]
    supportive = [
        item for item in executed
        if item.status == "pass"
    ]
    blocking = next(
        (
            item for item in executed
            if item.status in {"fail", "insufficient"}
        ),
        None,
    )
    incomplete_scope = len(executed) < len(DIMENSION_IDS)
    biggest = blocking or (
        executed[-1]
        if executed
        else DimensionAssessment(
            dimension_id="market_mainline",
            status="not_evaluated",
            evaluated_subjects=[],
            headline="本次没有执行公司级维度",
            analysis="当前调用只负责共享资源准备。",
        )
    )
    if blocking and blocking.status == "insufficient":
        recommendation_code: RecommendationCode = "analysis_unavailable"
        recommendation_reason = (
            f"“{DIMENSION_TITLES[blocking.dimension_id]}”的关键来源或分析服务未完成，"
            "本轮不对公司形成买入结论。"
        )
    elif (
        blocking
        and blocking.dimension_id == "market_mainline"
        and blocking.mainline_classification is not None
        and MainlineDirectionRelation(
            blocking.mainline_classification.direction_relation
        ) == MainlineDirectionRelation.EMERGING_BRANCH
    ):
        recommendation_code = "watchlist"
        recommendation_reason = (
            "产业归属已经确认，但候选主线尚未满足本轮所选策略的第一关准入条件；"
            "后续七维不执行，当前进入主线触发跟踪而不是买入名单。"
        )
    elif blocking:
        recommendation_code = "wait"
        recommendation_reason = (
            f"“{DIMENSION_TITLES[blocking.dimension_id]}”未通过，"
            "后续维度不再执行，当前不可进入买入计划。"
        )
    elif incomplete_scope:
        recommendation_code = "watchlist"
        recommendation_reason = (
            f"本次只完成前{len(executed)}个共享维度，"
            "其余公司级维度将在逐股流程中继续执行。"
        )
    else:
        recommendation_code = "conditional_buy"
        recommendation_reason = "八个布尔闸门均已通过，可以进入有纪律的买入计划。"

    positive_headlines = [item.headline for item in supportive[:4]]
    risk_headlines = [
        item.headline
        for item in ([blocking] if blocking else [])
        if item is not None
    ]
    monitoring_points = list(dict.fromkeys(
        point
        for item in dimensions
        for point in item.monitoring_points
        if point
    ))[:6]
    while len(monitoring_points) < 3:
        monitoring_points.append("下一期财报后重新核验八维证据")

    return ProfessionalAssessment(
        investment_profile=(
            f"{name}的八维布尔闸门：通过{counts['pass']}项、"
            f"不通过{counts['fail']}项、取证未完成{counts['insufficient']}项、"
            f"未执行{counts['not_evaluated']}项"
        ),
        overall_summary=(
            f"{name}本轮执行到第{len(executed)}维。{recommendation_reason}"
        ),
        core_thesis=" → ".join(positive_headlines) or "本轮尚未形成可验证的核心看多逻辑",
        biggest_issue=biggest.headline,
        recommendation_code=recommendation_code,
        recommendation_reason=recommendation_reason,
        dimensions=dimensions,
        bull_case_chain=(
            " → ".join(positive_headlines)
            if positive_headlines
            else "本轮未形成可验证的支持链条"
        ),
        risk_chain=(
            " → ".join(risk_headlines)
            if risk_headlines
            else "当前未识别硬性失败，但仍需持续核验经营兑现"
        ),
        monitoring_points=monitoring_points,
        evidence_gaps=list(dict.fromkeys(errors))[:8],
    )


def _fallback_assessment(error: str) -> ProfessionalAssessment:
    dimensions = [
        DimensionAssessment(
            dimension_id=dimension_id,
            status=(
                "insufficient"
                if index == 0
                else "not_evaluated"
            ),
            evaluated_subjects=[],
            headline=(
                "首个维度的专业复核未完成"
                if index == 0
                else "前序布尔闸门已关闭"
            ),
            analysis=(
                "本轮未能取得首个维度的有效结构化判断，程序按分析未完成关闭后续闸门。"
                if index == 0
                else "前一维度未通过，本维度未执行，也不能用于抵消首个阻断项。"
            ),
            key_evidence=[],
            counter_evidence=[],
            monitoring_points=(
                ["待分析服务恢复后基于同一证据包重新评估"]
                if index == 0
                else []
            ),
        )
        for index, dimension_id in enumerate(DIMENSION_IDS)
    ]
    return ProfessionalAssessment(
        investment_profile="八维布尔闸门在首个维度因取证未完成而停止",
        overall_summary="本轮首个维度未形成可验证判断，后续七维按状态机未执行。",
        core_thesis="待专业分析服务恢复后重新核验",
        biggest_issue=error or "专业分析服务不可用",
        recommendation_code="analysis_unavailable",
        recommendation_reason="首个维度分析未完成，程序已关闭买入闸门且不形成公司结论。",
        dimensions=dimensions,
        bull_case_chain="本轮专业复核未完成，暂不构造看多链条",
        risk_chain="分析服务失败 → 八维结论不可验证 → 暂停买入判断",
        monitoring_points=[
            "专业分析服务恢复情况",
            "行情与财务证据的数据时间",
            "重新评估后的八维状态",
        ],
        evidence_gaps=[error or "专业分析模型不可用"],
    )


def _inline_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Expand local Pydantic refs for gateways without ``$defs`` support."""
    definitions = schema.get("$defs")
    definitions = definitions if isinstance(definitions, dict) else {}

    def expand(value: Any) -> Any:
        if isinstance(value, list):
            return [expand(item) for item in value]
        if not isinstance(value, dict):
            return value
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            name = ref.rsplit("/", 1)[-1]
            target = definitions.get(name)
            if not isinstance(target, dict):
                raise ValueError(f"unresolved local JSON schema reference: {ref}")
            merged = {
                **expand(target),
                **{
                    key: expand(item)
                    for key, item in value.items()
                    if key != "$ref"
                },
            }
            return merged
        return {
            key: expand(item)
            for key, item in value.items()
            if key != "$defs"
        }

    result = expand(schema)
    if not isinstance(result, dict):
        raise ValueError("expanded JSON schema is not an object")
    return result


def _recommendation_label(code: str) -> str:
    return {
        "conditional_buy": "满足条件时可进入买入计划",
        "watchlist": "进入中期跟踪池",
        "wait": "等待更好的价格或验证信号",
        "avoid": "当前回避",
        "analysis_unavailable": "本轮分析未完成，等待系统恢复",
        "evidence_insufficient": "关键取证未完成，暂停判断",
    }.get(code, "关键取证未完成，暂停判断")


def evaluate_shared_market_mainline(
    *,
    thesis: str,
    thesis_context: dict[str, Any] | None,
    mainline_strategy: MainlineStrategyProfile | str,
    market_mainline_snapshot: dict[str, Any],
    on_reasoning: Callable[[str], None] | None = None,
) -> tuple[DimensionAssessment, str]:
    """Evaluate the market-level first gate exactly once for a stock batch."""
    evidence = collect_market_mainline_evidence(
        thesis=thesis,
        thesis_context=thesis_context,
        mainline_strategy=mainline_strategy,
        market_mainline_snapshot=market_mainline_snapshot,
    )
    assessment, model_error = _call_professional_model(
        evidence,
        evaluation_limit=1,
        on_reasoning=on_reasoning,
    )
    if assessment is None:
        assessment = _fallback_assessment(model_error)
    return assessment.dimensions[0], model_error


def analyze_professional_buy(
    symbol: str,
    *,
    thesis: str = "",
    thesis_context: dict[str, Any] | None = None,
    mainline_strategy: MainlineStrategyProfile | str = (
        MainlineStrategyProfile.CONFIRMED_MAINLINE
    ),
    pre_fetched_data: dict[str, Any] | None = None,
    on_reasoning: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Collect evidence, evaluate the eight gates in order and normalize them."""
    effective_thesis = resolve_investment_thesis(thesis, thesis_context)
    strategy_profile = normalize_mainline_strategy(mainline_strategy)
    prefetched = (
        pre_fetched_data
        if isinstance(pre_fetched_data, dict)
        else {}
    )
    raw_shared_gate = prefetched.get("market_mainline_assessment")
    shared_gate = (
        DimensionAssessment.model_validate(raw_shared_gate)
        if raw_shared_gate is not None
        else None
    )
    shared_model_error = str(
        prefetched.get("market_mainline_model_error") or ""
    ).strip()
    if shared_gate is not None and shared_gate.dimension_id != "market_mainline":
        raise ValueError(
            "shared market mainline assessment must be the first dimension"
        )
    if shared_gate is not None and shared_gate.status != "pass":
        evidence = {
            "contract_version": PROFESSIONAL_BUY_CONTRACT_VERSION,
            "requested_at": datetime.now().astimezone().isoformat(),
            "symbol": symbol,
            "stock_info": {"symbol": symbol, "name": symbol},
            "investment_thesis": effective_thesis or None,
            "thesis_context": thesis_context,
            "mainline_strategy": strategy_profile.value,
            "research_scope": {},
            "public_research": _empty_public_research({}),
            "base_company_packet": {},
            "base_packet_meta": {
                "success": True,
                "partial": False,
                "data_time": (
                    (
                        prefetched.get("market_mainline_snapshot")
                        if isinstance(
                            prefetched.get("market_mainline_snapshot"),
                            dict,
                        )
                        else {}
                    ).get("data_time")
                ),
                "errors": [],
                "warnings": [],
            },
            "dimension_evidence": {},
        }
    else:
        evidence = collect_professional_evidence(
            symbol,
            thesis=effective_thesis,
            thesis_context=thesis_context,
            mainline_strategy=strategy_profile,
            pre_fetched_data=pre_fetched_data,
            requested_sections=(
                ()
                if shared_gate is not None
                else ("market_mainline",)
            ),
        )

    def load_dimension(dimension_id: str) -> None:
        _collect_professional_sections(
            evidence,
            (dimension_id,),
            pre_fetched_data=pre_fetched_data,
        )

    assessment, model_error = _call_professional_model(
        evidence,
        dimension_loader=load_dimension,
        precomputed_dimensions=(
            (shared_gate,)
            if shared_gate is not None
            else ()
        ),
        on_reasoning=on_reasoning,
    )
    model_error = "；".join(
        value
        for value in (shared_model_error, model_error)
        if value
    )
    if assessment is None:
        assessment = _fallback_assessment(model_error)

    dimensions = [item.model_dump() for item in assessment.dimensions]
    counts = {
        status: sum(1 for item in dimensions if item["status"] == status)
        for status in ("pass", "fail", "insufficient", "not_evaluated")
    }
    blocking = next(
        (
            item for item in dimensions
            if item["status"] in {"fail", "insufficient"}
        ),
        None,
    )
    all_passed = counts["pass"] == len(DIMENSION_IDS)
    recommendation: RecommendationCode
    if all_passed:
        recommendation = "conditional_buy"
    elif blocking and blocking["status"] == "insufficient":
        recommendation = "analysis_unavailable"
    else:
        recommendation = (
            assessment.recommendation_code
            if assessment.recommendation_code in {
                "watchlist",
                "wait",
                "avoid",
            }
            else "wait"
        )
    if model_error and blocking and blocking["status"] == "insufficient":
        analysis_status = "execution_failed"
        final_decision = "分析失败"
    elif blocking and blocking["status"] == "insufficient":
        analysis_status = "source_unavailable"
        final_decision = "分析未完成"
    elif all_passed:
        analysis_status = "completed"
        final_decision = "可买入"
    else:
        analysis_status = "completed"
        final_decision = "不可买入"
    recommendation_reason = assessment.recommendation_reason
    overall_summary = assessment.overall_summary

    stock_info = evidence.get("stock_info") or {}
    base_meta = evidence.get("base_packet_meta") or {}
    return {
        "contract_version": PROFESSIONAL_BUY_CONTRACT_VERSION,
        "analysis_mode": PROFESSIONAL_BUY_ANALYSIS_MODE,
        "symbol": symbol,
        "name": (
            stock_info.get("name")
            or stock_info.get("short_name")
            or symbol
        ),
        "thesis": effective_thesis or None,
        "thesis_context": thesis_context,
        "mainline_strategy": strategy_profile.value,
        "investment_profile": assessment.investment_profile,
        "overall_summary": overall_summary,
        "core_thesis": assessment.core_thesis,
        "biggest_issue": assessment.biggest_issue,
        "recommendation_code": recommendation,
        "recommendation": _recommendation_label(recommendation),
        "recommendation_reason": recommendation_reason,
        "analysis_status": analysis_status,
        "final_decision": final_decision,
        "counts": counts,
        "dimensions": dimensions,
        "executed_count": len(DIMENSION_IDS) - counts["not_evaluated"],
        "not_evaluated_count": counts["not_evaluated"],
        "stopped_at": (
            blocking.get("dimension_id")
            if blocking
            else None
        ),
        "stopped_at_name": (
            DIMENSION_TITLES.get(str(blocking.get("dimension_id") or ""))
            if blocking
            else None
        ),
        "gate_pass_complete": all_passed,
        "bull_case_chain": assessment.bull_case_chain,
        "risk_chain": assessment.risk_chain,
        "monitoring_points": assessment.monitoring_points,
        "evidence_gaps": list(dict.fromkeys([
            *assessment.evidence_gaps,
            *(evidence.get("evidence_gaps") or []),
        ]))[:12],
        "source_links": evidence.get("source_links") or [],
        "data_time": base_meta.get("data_time") or evidence.get("requested_at"),
        "quote_basis": base_meta.get("quote_basis"),
        "quote_is_intraday": base_meta.get("quote_is_intraday"),
        "coverage_complete": analysis_status == "completed",
        "model_error": model_error or None,
    }


__all__ = [
    "DIMENSION_DEFINITIONS",
    "DIMENSION_IDS",
    "DimensionAssessment",
    "ModelDimensionAssessment",
    "PROFESSIONAL_BUY_ANALYSIS_MODE",
    "PROFESSIONAL_BUY_CONTRACT_VERSION",
    "ProfessionalAssessment",
    "analyze_professional_buy",
    "collect_market_mainline_evidence",
    "collect_professional_evidence",
    "evaluate_shared_market_mainline",
]
