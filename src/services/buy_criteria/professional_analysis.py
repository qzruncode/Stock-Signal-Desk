"""Evidence-complete professional buy analysis for A-share securities.

The conversational ``能否买入`` workflow uses one coherent analyst judgment
per company.  Data collection is still program-owned, but the model sees every
dimension together so it can explain trade-offs instead of emitting isolated
Boolean fragments.
"""
from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.services.buy_criteria.base import CriterionEvidence, _parse_verdict_json
from src.services.buy_criteria.evaluators.competition_landscape import (
    CompetitionLandscapeEvaluator,
)
from src.services.buy_criteria.evaluators.catalyst_events import (
    CatalystEventsEvaluator,
)
from src.services.buy_criteria.evaluators.entry_risk_reward import (
    EntryRiskRewardEvaluator,
)
from src.services.buy_criteria.evaluators.fatal_risks import FatalRisksEvaluator
from src.services.buy_criteria.evaluators.growth_drivers import (
    GrowthDriversEvaluator,
)
from src.services.buy_criteria.evaluators.growth_space import GrowthSpaceEvaluator
from src.services.buy_criteria.evaluators.industrial_competitiveness import (
    IndustrialCompetitivenessEvaluator,
)
from src.services.buy_criteria.evaluators.mainline_position import (
    MainlinePositionEvaluator,
)
from src.services.buy_criteria.evaluators.prosperity_cycle import (
    ProsperityCycleEvaluator,
)
from src.services.buy_criteria.research_enrichment import (
    collect_public_research,
    derive_research_scope,
    research_summary_for_lenses,
)

logger = logging.getLogger(__name__)


PROFESSIONAL_BUY_CONTRACT_VERSION = "professional_buy_analysis_v3"

DIMENSION_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("market_mainline", "业务属于当前市场主线"),
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
    "three_year_space": {"structural_trend", "cycle_supply_demand"},
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
DimensionStatus = Literal["pass", "partial", "fail", "insufficient"]
RecommendationCode = Literal[
    "conditional_buy",
    "watchlist",
    "wait",
    "avoid",
    "evidence_insufficient",
]


def _dimension_model_concurrency() -> int:
    try:
        return max(
            1,
            min(4, int(os.getenv("PROFESSIONAL_BUY_LLM_CONCURRENCY", "2"))),
        )
    except (TypeError, ValueError):
        return 2


class DimensionAssessment(BaseModel):
    """One evidence-grounded checklist judgment."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    dimension_id: DimensionId
    status: DimensionStatus
    headline: str = Field(min_length=2, max_length=120)
    analysis: str = Field(min_length=8, max_length=2400)
    key_evidence: list[str] = Field(default_factory=list, max_length=5)
    counter_evidence: list[str] = Field(default_factory=list, max_length=4)
    monitoring_points: list[str] = Field(default_factory=list, max_length=4)


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

你必须完整分析八项，任何一项不通过都不能提前停止后续分析：
1. 业务属于当前市场主线；
2. 公司在产业链中有竞争力；
3. 行业处于上升周期，不是存量博弈；
4. 公司业务没有严重价格战或内卷；
5. 有政策、技术、需求或供给变化驱动；
6. 未来6—12个月仍有明确催化；
7. 估值合理，上涨弹性大于下跌风险；
8. 无重大风险隐患。

状态口径：
- pass：证据链充分，主要反证不足以推翻；
- partial：方向成立但交易热度、兑现程度、赔率或证据存在重要保留；
- fail：证据明确不满足，或存在足以否决该项的风险；
- insufficient：关键来源失败或证据太少，不能把“没查到”写成通过或事实性否定。

分析纪律：
- 市场主线必须分三层：1—3年结构性产业趋势、1—6个月A股主导叙事、1—10日交易确认。三层必须分别陈述并综合，任何单日板块排名都不得单独决定结论；
- 竞争力必须落到产品、份额、技术、客户、盈利能力、成本或正式经营证据，概念关系不算；
- 周期必须检查订单、装机、出货、价格、库存、产能利用率、资本开支或连续财务趋势；
- 内卷必须检查同一细分行业的价格、供给、毛利率、客户议价和产能，不能靠关键词或股价下跌判断；
- 驱动要区分政策、技术、需求、供给，并说明传导到公司收入或利润的路径；
- 催化必须有未来时间窗，同时列正向催化和定增、解禁、减持等反向事件；
- 估值不能只报一个PE。至少结合历史/同行/增长，并做保守下行锚与合理上行锚的赔率分析；
- 风险要检查现金流、应收、存货、客户集中、负债、商誉、质押、减持、解禁、再融资、监管和诉讼；
- 历史事件必须按“事项+年份”建立公告时间线。detected 只证明曾出现过，不代表当前仍在进行；后续正式公告覆盖早期程序阶段。交易所审核通过、证监会注册、发行完成是不同状态，不得互相替代；
- 不能笼统写“证据不足”。检索调用失败应写“本轮取证未完成，不代表事实不存在”；已检查有效来源但未披露精确指标，应写明“公开披露边界”和已使用的替代证据，不能自动降级；
- 数字、日期、产品、客户、订单、产能和行业判断只能来自本轮证据。允许根据证据做明确算术，但必须说明口径；
- 证据有冲突时主动写冲突。不得用模型记忆补全本轮没有的事实，不得编造来源链接。

recommendation_code 口径：
- conditional_buy：八项没有 fail/insufficient，且估值赔率和重大风险均至少为 partial；
- watchlist：产业逻辑较强但仍有一到数项关键验证；
- wait：公司可研究，但价格、景气或催化尚未给出合适介入条件；
- avoid：基本逻辑或重大风险使当前不值得介入；
- evidence_insufficient：关键证据源失败，无法完成可信判断。

请只返回符合给定结构的 JSON，不输出 Markdown，也不要自行计算总分；总分由程序按
pass=1、partial=0.5、fail/insufficient=0 统一计算。
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
    """Remove untraceable figures without discarding the analyst judgment."""

    def clean(value: Any) -> Any:
        if isinstance(value, str):
            for claim in unsupported:
                value = value.replace(claim, "未核验数值")
            return value
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        return value

    payload = clean(assessment.model_dump())
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
        failure = evaluator.evidence_failure_reason(evidence)
        return section, {
            "success": failure is None,
            "evidence_gap": failure,
            "summary": _bounded_text(evidence.data_summary),
            "raw_data": evidence.raw_data,
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


def collect_professional_evidence(
    symbol: str,
    *,
    thesis: str = "",
    thesis_context: dict[str, Any] | None = None,
    pre_fetched_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect every checklist dimension without making a judgment."""
    from src.tools.get_multi_stock_decision_evidence import (
        get_multi_stock_decision_evidence,
    )

    stock_info = _stock_info(symbol)
    stock_info["_investment_thesis"] = str(thesis or "").strip()
    stock_info["_investment_thesis_context"] = thesis_context

    base_packet = get_multi_stock_decision_evidence(symbol, thesis)
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
    collectors: tuple[tuple[str, Any], ...] = (
        ("market_mainline", MainlinePositionEvaluator()),
        ("industrial_competitiveness", IndustrialCompetitivenessEvaluator()),
        ("industry_cycle", ProsperityCycleEvaluator()),
        ("three_year_space", GrowthSpaceEvaluator()),
        ("competition_quality", CompetitionLandscapeEvaluator()),
        ("growth_drivers", GrowthDriversEvaluator()),
        ("forward_catalysts", CatalystEventsEvaluator()),
        ("major_risks", FatalRisksEvaluator()),
    )
    sections: dict[str, dict[str, Any]] = {}
    enrichment: dict[str, Any]
    with ThreadPoolExecutor(max_workers=7) as pool:
        enrichment_future = pool.submit(collect_public_research, stock_info)
        futures = [
            pool.submit(
                _run_evidence_collector,
                section,
                evaluator,
                symbol,
                stock_info,
                pre_fetched_data,
            )
            for section, evaluator in collectors
        ]
        for future in as_completed(futures):
            section, result = future.result()
            sections[section] = result
        try:
            enrichment = enrichment_future.result()
        except Exception as exc:
            logger.warning(
                "professional buy public research %s failed: %s",
                symbol,
                exc,
            )
            enrichment = {
                "scope": research_scope,
                "attempts": [],
                "items": [],
                "lens_status": {
                    lens: "retrieval_failed"
                    for lens in (
                        "market_consensus",
                        "structural_trend",
                        "cycle_supply_demand",
                        "competition_structure",
                        "company_position",
                    )
                },
                "retrieved_source_count": 0,
                "retrieval_complete": False,
                "errors": [f"{type(exc).__name__}: {str(exc)[:240]}"],
            }

    for section, lenses in PUBLIC_RESEARCH_LENSES_BY_DIMENSION.items():
        payload = sections.get(section)
        if not isinstance(payload, dict):
            continue
        supplement = research_summary_for_lenses(enrichment, lenses)
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
                    lens: (enrichment.get("lens_status") or {}).get(lens)
                    for lens in lenses
                },
                "items": [
                    item
                    for item in enrichment.get("items") or []
                    if isinstance(item, dict)
                    and item.get("lens") in lenses
                ],
            },
        }

    # The deterministic entry calculation is evidence for the valuation/odds
    # discussion, not a ninth veto and not the final recommendation.
    try:
        entry = EntryRiskRewardEvaluator().evaluate(
            symbol,
            stock_info,
            pre_fetched_data,
        )
        sections["valuation_odds"] = {
            "success": entry.status != "insufficient",
            "evidence_gap": (
                entry.verdict if entry.status == "insufficient" else None
            ),
            "summary": _bounded_text(entry.evidence.data_summary, 4_000),
            "deterministic_entry_context": {
                "status": entry.status,
                "verdict": entry.verdict,
                "details": entry.details,
            },
            "raw_data": entry.evidence.raw_data,
        }
    except Exception as exc:
        sections["valuation_odds"] = {
            "success": False,
            "evidence_gap": f"{type(exc).__name__}: {str(exc)[:240]}",
            "summary": "当前行情与风险收益证据获取失败。",
            "raw_data": {},
        }

    evidence = {
        "contract_version": PROFESSIONAL_BUY_CONTRACT_VERSION,
        "requested_at": datetime.now().astimezone().isoformat(),
        "symbol": symbol,
        "stock_info": stock_info,
        "investment_thesis": str(thesis or "").strip() or None,
        "thesis_context": thesis_context,
        "research_scope": research_scope,
        "public_research": enrichment,
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
        "dimension_evidence": sections,
    }
    evidence["source_links"] = _collect_source_links(evidence)
    evidence["capability_gaps"] = [
        f"{section}: {payload.get('evidence_gap')}"
        for section, payload in sections.items()
        if payload.get("evidence_gap")
    ] + [str(error) for error in base_packet.get("errors") or []]
    evidence["public_disclosure_limits"] = [
        f"{attempt.get('lens')}/{attempt.get('subject') or '全市场'}: "
        "已完成检索，但公开来源未返回匹配材料"
        for attempt in enrichment.get("attempts") or []
        if isinstance(attempt, dict)
        and attempt.get("status") == "no_matching_public_material"
    ]
    # Compatibility field for existing result contracts.  Its entries now
    # preserve the crucial distinction between retrieval failure and a valid
    # search that found no matching public disclosure.
    evidence["evidence_gaps"] = [
        *evidence["capability_gaps"],
        *evidence["public_disclosure_limits"],
    ]
    return evidence


def _prompt_evidence_view(evidence: dict[str, Any]) -> dict[str, Any]:
    """Bound the LLM payload while retaining primary evidence and all axes."""
    section_limits = {
        "market_mainline": 2_800,
        "industrial_competitiveness": 3_200,
        "industry_cycle": 2_000,
        "three_year_space": 2_000,
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
        "screening_flags": item.get("screening_flags") or {},
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
    technical = (
        packet.get("technical")
        if isinstance(packet.get("technical"), dict)
        else {}
    )
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
        base.update({
            "quote": quote,
            "technical": technical,
            "capital_flow": packet.get("capital_flow") or {},
        })
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
            "screening_flags": packet.get("screening_flags") or {},
        })
    return base


def _call_professional_model(
    evidence: dict[str, Any],
) -> tuple[ProfessionalAssessment | None, str]:
    from src.llm.anthropic_gateway import completion_gateway
    from src.storage import persist_llm_usage

    def field(value: Any, name: str) -> Any:
        return value.get(name) if isinstance(value, dict) else getattr(value, name, None)

    def payload_from_response(response: Any, tool_name: str) -> dict[str, Any]:
        choices = field(response, "choices") or []
        if not choices:
            raise ValueError("professional analysis response has no choices")
        choice = choices[0]
        message = field(choice, "message")
        tool_calls = field(message, "tool_calls") or []
        for call in tool_calls:
            function = field(call, "function")
            if field(function, "name") != tool_name:
                continue
            arguments = field(function, "arguments")
            payload = (
                json.loads(arguments)
                if isinstance(arguments, str)
                else arguments
            )
            if not isinstance(payload, dict):
                raise ValueError("professional analysis tool arguments are not an object")
            return payload
        # Compatibility fallback for gateways that return forced JSON as text.
        content = field(message, "content")
        if isinstance(content, str):
            parsed = _parse_verdict_json(content)
            if isinstance(parsed, dict):
                return parsed
        reasoning = field(message, "reasoning_content")
        raise ValueError(
            "professional analysis response did not call the forced schema"
            f" (finish={field(choice, 'finish_reason')},"
            f" content_chars={len(content) if isinstance(content, str) else 0},"
            f" reasoning_chars={len(reasoning) if isinstance(reasoning, str) else 0})"
        )

    def normalized_usage(response: Any) -> dict[str, int]:
        usage = field(response, "usage")
        return {
            key: int(field(usage, key) or 0)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        }

    def forced_call(
        *,
        tool_name: str,
        description: str,
        system_prompt: str,
        user_prompt: str,
        response_model: type[BaseModel],
        max_tokens: int,
        timeout: int,
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
        response = completion_gateway(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            tools=[tool],
            tool_choice={"type": "function", "function": {"name": tool_name}},
            temperature=0.1,
            max_tokens=max_tokens,
            timeout=timeout,
            # llmops currently serves GLM-5.2 through an Anthropic-compatible
            # gateway.  GLM-5.2 defaults to max reasoning, and the gateway does
            # not reliably honour ``thinking.type=disabled`` on its own.  The
            # provider-compatible ``reasoning_effort=none`` is therefore sent
            # alongside it so forced-schema calls reach the actual tool result
            # instead of exhausting the output budget on reasoning_content.
            extra_body={
                "thinking": {"type": "disabled"},
                "reasoning_effort": "none",
            },
        )
        payload = payload_from_response(response, tool_name)
        parsed = response_model.model_validate(payload)
        persist_llm_usage(
            normalized_usage(response),
            str(field(response, "model") or "anthropic-gateway"),
            call_type,
        )
        return parsed

    evidence_view = _prompt_evidence_view(evidence)
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
        "market_mainline": {"industry_cycle", "growth_drivers"},
        "industrial_competitiveness": {"competition_quality"},
        "industry_cycle": {"three_year_space"},
        "competition_quality": {"industrial_competitiveness"},
        "growth_drivers": {"industry_cycle"},
        "forward_catalysts": {"growth_drivers", "major_risks"},
        "valuation_odds": {"industry_cycle"},
        "major_risks": {"forward_catalysts"},
    }
    dimension_instructions = {
        "market_mainline": (
            "按三个时间层分别判断：1—3年结构性产业趋势、1—6个月A股主导叙事、"
            "1—10日板块与个股交易确认。先识别当前市场占主导的叙事，再说明公司细分业务"
            "处于主线核心、活跃分支、轮动支线还是仅有长期趋势。不得因某一天未进入Top N、"
            "单日回撤或单日资金流出直接否决；也不得用长期产业前景冒充当前交易主线。必须"
            "实际核验 public_research 的 market_consensus 与原始板块数据，明确写出当前全市场"
            "占主导的中期叙事，再判断公司方向与它的关系；主线报告不可用不等于其他证据不可用。"
            "1—6个月主导叙事只能由机构策略、连续市场证据或主线报告判断，绝不能由某一天的"
            "涨幅榜改写。单日领涨板块只能进入1—10日交易确认。最终headline必须同时点明中期"
            "主导叙事和公司方向的层级关系。"
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
            "作为兑现节奏监控，不能单独把行业周期降级。"
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
            "把公告按事项和年份建立时间线。status=detected 只是文本命中，绝不能改写成"
            "当前active。后续回复、审核决定、注册、发行结果、判决或终止公告覆盖早期阶段。"
            "不同融资/交易方案不得串联：旧方案已发行不代表新方案已完成；交易所审核通过也"
            "不等于证监会注册或发行完成。对现金流、应收、存货等说明风险传导，不得把风险信号"
            "夸大成已经爆雷。质押次数或补充质押标题本身不能证明控股股东资金链紧张，只有"
            "累计质押比例、平仓风险、违约或伴随减持等正式证据才能支持该判断。"
        ),
    }
    dimension_system_prompt = (
        "你是管理A股组合的资深买方分析师。只核验程序指定的一个买入维度，"
        "同时写支持证据、主要反证和后续验证。状态只能是 pass、partial、fail、insufficient。"
        "主线按结构性产业趋势、中期市场叙事、短期交易确认三层分析；当前资金热度不强但"
        "产业趋势成立时，主线项通常是 partial；"
        "主线报告不可用时必须降低置信度，不能把缺失本身写成 fail。"
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
        "仅有补充质押公告而没有质押比例时，不得推断高比例、超过50%或伴随减持；"
        "再融资问询及回复不等于监管处罚，也不能仅凭关键词判断当前生命周期。"
        "主线报告缺失但原始板块与产业证据仍可用时，"
        "应基于现有证据判断 partial/fail，只有关键市场证据整体缺失才用 insufficient。"
        "只使用本轮证据，禁止补造数字、同行区间、客户、原因解释、行业数据或未来日期；"
        "所有数字必须原样复制证据，不做格式转换、舍入或自设阈值；"
        "public_research 中 source_quality=ugc_lead_only 的材料只能作为线索，必须有正式披露、"
        "专业来源或结构化市场数据交叉验证后才能写入关键证据；不得把自媒体标题当事实。"
        "禁止笼统写“证据不足”：若来源调用失败，写“本轮某来源取证未完成，不代表事实不存在”；"
        "若来源有效但没有披露精确指标，写“已核对的公开披露未提供某精确指标”，并说明替代证据。"
        "非关键可选指标未披露不得自动判 insufficient；只有会阻断该维度结论的关键来源失败才用"
        " insufficient。analysis应凝练、避免重复证据列表并以完整句结束；"
        "三个证据字段始终使用数组。"
    )

    def dimension_request(
        definition: tuple[str, str],
        *,
        retry: bool = False,
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
        if retry and dimension_id == "market_mainline":
            quote = company_context.get("quote")
            quote = quote if isinstance(quote, dict) else {}
            company_context = {
                "profile": company_context.get("profile") or {},
                "quote": {
                    key: quote.get(key)
                    for key in (
                        "change_pct",
                        "turnover_rate",
                        "data_time",
                        "is_stale",
                    )
                    if quote.get(key) is not None
                },
            }
        elif retry and dimension_id == "major_risks":
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
                "screening_flags": company_context.get("screening_flags") or {},
            }
        request = {
            "stock_info": compact_stock_info,
            "requested_at": evidence_view.get("requested_at"),
            "investment_thesis": evidence_view.get("investment_thesis"),
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
            "dimension_evidence": sections,
            "capability_gaps": [
                gap
                for gap in evidence_view.get("capability_gaps") or []
                if any(section in str(gap) for section in section_names)
            ][:6],
            "public_disclosure_limits": [
                gap
                for gap in evidence_view.get("public_disclosure_limits") or []
            ][:6],
        }
        user_prompt = json.dumps(request, ensure_ascii=False, default=str)
        raw_sections = evidence.get("dimension_evidence")
        raw_sections = raw_sections if isinstance(raw_sections, dict) else {}
        validation_evidence = {
            "requested_at": evidence.get("requested_at"),
            "base_company_packet": evidence.get("base_company_packet"),
            "public_research": request.get("public_research") or [],
            "dimension_evidence": {
                key: raw_sections.get(key)
                for key in section_names
                if key in raw_sections
            },
        }
        active_system_prompt = dimension_system_prompt + (
            "这是失败维度的恢复调用：只返回最小但完整的合规判断，"
            "优先使用company_context中的结构化字段，删除任何无法逐字或等值追溯的数字。"
            if retry
            else ""
        )
        result = forced_call(
            tool_name="submit_dimension_assessment",
            description="提交程序指定的一个买入分析维度",
            system_prompt=active_system_prompt,
            user_prompt=user_prompt,
            response_model=DimensionAssessment,
            max_tokens=8_000,
            timeout=360,
            call_type="professional_buy_dimensions",
        )
        if not isinstance(result, DimensionAssessment):
            raise ValueError("dimension response has unexpected type")
        if result.dimension_id != dimension_id:
            raise ValueError(
                f"模型返回维度 {result.dimension_id}，预期 {dimension_id}"
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

    results_by_id: dict[str, DimensionAssessment] = {}
    errors_by_id: dict[str, str] = {}
    model_concurrency = _dimension_model_concurrency()
    with ThreadPoolExecutor(max_workers=model_concurrency) as pool:
        future_map = {
            pool.submit(dimension_request, definition): definition
            for definition in DIMENSION_DEFINITIONS
        }
        for future in as_completed(future_map):
            dimension_id, title = future_map[future]
            try:
                results_by_id[dimension_id] = future.result()
            except Exception as exc:
                errors_by_id[dimension_id] = (
                    f"{title}: {type(exc).__name__}: {str(exc)[:180]}"
                )
                logger.warning(
                    "professional buy %s failed for %s",
                    errors_by_id[dimension_id],
                    evidence.get("symbol"),
                )

    # Successful axes are immutable.  Only failed axes are retried with a
    # smaller evidence packet, so one provider timeout cannot erase the rest.
    if errors_by_id:
        failed_definitions = [
            definition
            for definition in DIMENSION_DEFINITIONS
            if definition[0] in errors_by_id
        ]
        with ThreadPoolExecutor(
            max_workers=min(model_concurrency, len(failed_definitions))
        ) as pool:
            future_map = {
                pool.submit(dimension_request, definition, retry=True): definition
                for definition in failed_definitions
            }
            for future in as_completed(future_map):
                dimension_id, title = future_map[future]
                try:
                    results_by_id[dimension_id] = future.result()
                    errors_by_id.pop(dimension_id, None)
                except Exception as exc:
                    errors_by_id[dimension_id] = (
                        f"{title}重试: {type(exc).__name__}: {str(exc)[:180]}"
                    )
                    logger.warning(
                        "professional buy %s failed for %s",
                        errors_by_id[dimension_id],
                        evidence.get("symbol"),
                    )

    dimensions = [
        results_by_id.get(dimension_id)
        or DimensionAssessment(
            dimension_id=dimension_id,
            status="insufficient",
            headline="该维度的专业复核未完成",
            analysis=f"模型没有返回“{title}”的有效结构化判断，不能用其他维度代替。",
            key_evidence=[],
            counter_evidence=[],
            monitoring_points=[f"重新核验“{title}”"],
        )
        for dimension_id, title in DIMENSION_DEFINITIONS
    ]
    model_errors = list(errors_by_id.values())

    if model_errors:
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
        "若任一维度是 insufficient，recommendation_code 必须是 evidence_insufficient；"
        "反过来，八个维度均已完成且没有 insufficient 时，禁止返回 evidence_insufficient，"
        "也禁止在摘要中写“证据不足以形成判断”；此时必须在 conditional_buy、watchlist、"
        "wait、avoid 中按已完成的八维结果选择。"
        "若有 fail，不得给 conditional_buy；估值或重大风险不足时也不得给 conditional_buy。"
        "partial 并不自动禁止 conditional_buy，也不得声称存在本提示未定义的机械评级规则；"
        "是否建议介入必须结合八项证据的严重程度形成专业判断。"
    )
    try:
        overall = forced_call(
            tool_name="submit_overall_assessment",
            description="提交八维分析的统一投委会结论",
            system_prompt=overall_system_prompt,
            user_prompt=json.dumps(overall_request, ensure_ascii=False, default=str),
            response_model=OverallAssessment,
            max_tokens=6_000,
            timeout=300,
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
    """Preserve valid axis judgments when only the conclusion call fails.

    This is deliberately a structural fallback, not a second stock-picking
    rule engine: statuses and evidence remain exactly those produced by the
    eight analyst judgments.  The code only counts them and arranges their
    already-written conclusions into a readable summary.
    """
    counts = {
        status: sum(1 for item in dimensions if item.status == status)
        for status in ("pass", "partial", "fail", "insufficient")
    }
    score = counts["pass"] + counts["partial"] * 0.5
    name = str(
        stock_info.get("name")
        or stock_info.get("short_name")
        or stock_info.get("symbol")
        or "该公司"
    )
    supportive = [
        item for item in dimensions if item.status in {"pass", "partial"}
    ]
    blocking = [
        item for item in dimensions if item.status in {"fail", "insufficient"}
    ]
    biggest = (
        blocking[0]
        if blocking
        else next(
            (item for item in reversed(dimensions) if item.status == "partial"),
            dimensions[-1],
        )
    )
    if counts["insufficient"]:
        recommendation_code: RecommendationCode = "evidence_insufficient"
        recommendation_reason = "仍有维度未完成有效复核，当前不能形成可信的买入判断。"
    elif counts["fail"]:
        recommendation_code = "wait"
        recommendation_reason = "至少一项核心条件明确不满足，当前更适合等待条件反转。"
    elif score >= 6.5:
        recommendation_code = "conditional_buy"
        recommendation_reason = "八维没有硬性失败，且多数核心条件已有证据支持，可在价格与仓位纪律下制定计划。"
    elif score >= 5:
        recommendation_code = "watchlist"
        recommendation_reason = "产业逻辑具备研究价值，但仍有多项关键验证没有完成。"
    else:
        recommendation_code = "wait"
        recommendation_reason = "当前支持条件不足，赔率或兑现度尚不适合介入。"

    positive_headlines = [item.headline for item in supportive[:4]]
    risk_headlines = [
        item.headline
        for item in (
            blocking
            or [item for item in dimensions if item.status == "partial"]
        )[:4]
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
            f"{name}的八维画像为：通过{counts['pass']}项、半通过{counts['partial']}项、"
            f"不通过{counts['fail']}项、取证未完成{counts['insufficient']}项"
        ),
        overall_summary=(
            f"{name}本轮折算得分约{score:g}/8。"
            f"{recommendation_reason}"
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
            status="insufficient",
            headline="关键分析未完成",
            analysis=(
                "本轮证据采集结果未能完成统一的资深分析师复核，因此不能把零散数据拼成买入结论。"
            ),
            key_evidence=[],
            counter_evidence=[],
            monitoring_points=["待分析服务恢复后基于同一证据包重新评估"],
        )
        for dimension_id in DIMENSION_IDS
    ]
    return ProfessionalAssessment(
        investment_profile="证据已采集，但专业综合判断尚未成功生成",
        overall_summary="本轮没有形成可验证的八维完整分析，暂不提供买入结论。",
        core_thesis="待专业分析服务恢复后重新核验",
        biggest_issue=error or "专业分析服务不可用",
        recommendation_code="evidence_insufficient",
        recommendation_reason="八个维度均未完成统一判断，不能使用模型记忆或机械规则替代。",
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
        "evidence_insufficient": "关键取证未完成，暂停判断",
    }.get(code, "关键取证未完成，暂停判断")


def analyze_professional_buy(
    symbol: str,
    *,
    thesis: str = "",
    thesis_context: dict[str, Any] | None = None,
    pre_fetched_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect all evidence, call the dedicated prompt once and normalize it."""
    evidence = collect_professional_evidence(
        symbol,
        thesis=thesis,
        thesis_context=thesis_context,
        pre_fetched_data=pre_fetched_data,
    )
    assessment, model_error = _call_professional_model(evidence)
    if assessment is None:
        assessment = _fallback_assessment(model_error)

    dimensions = [item.model_dump() for item in assessment.dimensions]
    counts = {
        status: sum(1 for item in dimensions if item["status"] == status)
        for status in ("pass", "partial", "fail", "insufficient")
    }
    score = round(counts["pass"] + counts["partial"] * 0.5, 1)
    recommendation = assessment.recommendation_code
    recommendation_reason = assessment.recommendation_reason
    overall_summary = assessment.overall_summary
    # The model can choose among nuanced non-buy labels, but it cannot emit a
    # buy plan when the validated checklist still contains a hard failure.
    if recommendation == "conditional_buy" and (
        counts["fail"]
        or counts["insufficient"]
        or score < 6.5
    ):
        recommendation = "wait"
    if counts["insufficient"] == len(DIMENSION_IDS):
        recommendation = "evidence_insufficient"
    elif recommendation == "evidence_insufficient" and not counts["insufficient"]:
        recommendation = "watchlist" if score >= 5 else "wait"
        recommendation_reason = (
            "八个维度均已完成复核，不能使用“取证未完成”标签；"
            "当前建议根据已完成维度的支持强度与主要保留重新归一。"
        )
        overall_summary = (
            f"八个维度均已完成：通过{counts['pass']}项、半通过"
            f"{counts['partial']}项、不通过{counts['fail']}项。"
            f"最大问题是：{assessment.biggest_issue}"
        )

    stock_info = evidence.get("stock_info") or {}
    base_meta = evidence.get("base_packet_meta") or {}
    return {
        "contract_version": PROFESSIONAL_BUY_CONTRACT_VERSION,
        "analysis_mode": "professional_eight_dimension_buy_analysis",
        "symbol": symbol,
        "name": (
            stock_info.get("name")
            or stock_info.get("short_name")
            or symbol
        ),
        "thesis": str(thesis or "").strip() or None,
        "investment_profile": assessment.investment_profile,
        "overall_summary": overall_summary,
        "core_thesis": assessment.core_thesis,
        "biggest_issue": assessment.biggest_issue,
        "recommendation_code": recommendation,
        "recommendation": _recommendation_label(recommendation),
        "recommendation_reason": recommendation_reason,
        "score": score,
        "score_total": len(DIMENSION_IDS),
        "counts": counts,
        "dimensions": dimensions,
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
        "coverage_complete": counts["insufficient"] == 0,
        "model_error": model_error or None,
    }


__all__ = [
    "DIMENSION_DEFINITIONS",
    "DIMENSION_IDS",
    "PROFESSIONAL_BUY_CONTRACT_VERSION",
    "ProfessionalAssessment",
    "analyze_professional_buy",
    "collect_professional_evidence",
]
