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
        self.issues = issues or [
            {
                "pointer": "/choices/0/message/tool_calls",
                "code": "forced_schema_missing",
                "expected": ("exactly one forced tool call whose arguments match the " "supplied schema"),
                "allowed": [],
            }
        ]



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
            raise ValueError("dimensions must contain the exact eight dimensions in contract order")
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

_NUMERIC_CLAIM_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*" r"(?P<unit>%|亿元|万元|元|倍|MW|GW|个月|季度|年|月|日|家|只)",
    re.IGNORECASE,
)

_NUMERIC_RANGE_RE = re.compile(
    r"(?P<start>\d+(?:\.\d+)?)\s*[-—~～至]\s*"
    r"(?P<end>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>%|亿元|万元|元|倍|MW|GW|个月|季度|年|月|日|家|只)",
    re.IGNORECASE,
)

_ISO_DATE_RE = re.compile(r"(?P<year>20\d{2})-(?P<month>\d{1,2})-(?P<day>\d{1,2})")

_BARE_DECIMAL_RE = re.compile(
    r"(?<![\d./:-])(?P<value>\d+\.\d+)(?![\d])" r"(?!\s*(?:%|亿元|万元|元|倍|MW|GW|个月|季度|年|月|日|家|只))",
    re.IGNORECASE,
)

_DANGLING_HEADLINE_RE = re.compile(r"(?:为|是|包括|来自|由于|因为|但|且|及|与|和|或|：|:|，|,)$")

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


from . import _professional_analysis_methods1 as _professional_analysis_methods1
from . import _professional_analysis_methods2 as _professional_analysis_methods2
from . import _professional_analysis_methods3 as _professional_analysis_methods3
from . import _professional_analysis_methods4 as _professional_analysis_methods4
from . import _professional_analysis_methods5 as _professional_analysis_methods5
from . import _professional_analysis_methods6 as _professional_analysis_methods6
from . import _professional_analysis_methods7 as _professional_analysis_methods7


def _bind_analysis_function(_member):
    import functools
    import types

    _bound = types.FunctionType(
        _member.__code__,
        globals(),
        _member.__name__,
        _member.__defaults__,
        _member.__closure__,
    )
    _bound.__kwdefaults__ = _member.__kwdefaults__
    functools.update_wrapper(_bound, _member)
    return _bound


for _method_module in (_professional_analysis_methods1, _professional_analysis_methods2, _professional_analysis_methods3, _professional_analysis_methods4, _professional_analysis_methods5, _professional_analysis_methods6, _professional_analysis_methods7):
    for _function_name in _method_module.__all__:
        globals()[_function_name] = _bind_analysis_function(
            getattr(_method_module, _function_name)
        )
