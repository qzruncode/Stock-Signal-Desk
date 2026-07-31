# -*- coding: utf-8 -*-
"""Typed contracts used only after standard-task execution.

This module deliberately contains no routing table, tool name, tool schema or
executor.  It defines result-writing requirements and small typed structures
for deterministic aggregation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator


@dataclass(frozen=True)
class AnalysisPlaybook:
    id: str
    title: str
    evidence_standard: tuple[str, ...]
    output_contract: tuple[str, ...]

    def system_instruction(self) -> str:
        evidence = "\n".join(f"{index}. {item}" for index, item in enumerate(self.evidence_standard, 1))
        output = "\n".join(f"{index}. {item}" for index, item in enumerate(self.output_contract, 1))
        return (
            f"## 结果验收标准：{self.title}（{self.id}）\n"
            "本标准只约束已执行结果的写作与验收，不能创建任务或调用工具。\n\n"
            f"### 证据标准\n{evidence}\n\n"
            f"### 输出合同\n{output}"
        )


INDUSTRY_CHAIN = AnalysisPlaybook(
    id="industry_chain_research",
    title="产业链受益环节研究",
    evidence_standard=(
        "同时使用近期产业资讯与跨机构行业研究，并标明时间、来源和信息级别。",
        "按上游核心部件、中游制造或平台、下游应用及横向基础设施拆解。",
        "逐环节检查价值量、放量弹性、壁垒、国产替代、订单或产能兑现和降价风险。",
        "区分计划、预测和已实现数据，同时寻找支持证据与反证。",
    ),
    output_contract=(
        "先给受益优先级与判断口径，再给产业链地图。",
        "重点环节写明受益机制、兑现指标、受益节奏和主要反证。",
        "关键事实使用已取得证据中的可点击来源链接。",
        "结尾列持续跟踪指标、截至时间、证据限制和整体置信度。",
    ),
)


MARKET_OUTLOOK = AnalysisPlaybook(
    id="market_outlook_scenarios",
    title="市场主线情景研判",
    evidence_standard=(
        "当前市场状态与未来主线分开表述，单日指数涨跌和板块热度不能单独证明中期主线。",
        "候选主线至少由政策、产业供需或资本开支、技术路线和机构研究中的多类证据交叉支持。",
        "机构预测、媒体叙事和已实现事实必须分层，不把预测写成确定结果。",
        "每个方向同时检查支持证据、反证、拥挤或估值风险以及数据截至时间。",
    ),
    output_contract=(
        "先给基准情景下的主线排序和一句话结论，不用‘无法预测’代替判断。",
        "列出不超过五个候选方向，并分别写明核心逻辑、成立条件、失效信号和相对置信度。",
        "补充乐观与谨慎情景，说明哪些可观察变量会导致排序切换。",
        "最后集中说明一次证据缺口和跟踪清单，不逐段重复免责声明。",
    ),
)


THEME_COMPANY_MAPPING = AnalysisPlaybook(
    id="theme_company_mapping",
    title="产业主题到 A 股公司的证据化映射",
    evidence_standard=(
        "候选身份可来自结构化板块或公开来源中的公司事实，但证券身份必须通过本地证券库核验。",
        "没有同名概念板块不代表没有相关公司；板块成员只能作为 L1 候选证据。",
        "订单、客户、量产和收入必须逐家公司绑定到可回查原文。",
        "证券代码必须通过本地证券库核验，不能依靠模型记忆。",
    ),
    output_contract=(
        "列出返回范围内的公司和代码并说明覆盖状态。",
        "区分 L1 候选、公司级正向事实与反证边界。",
        "明确候选关系不等同订单、收入兑现或投资建议。",
    ),
)


INVESTMENT_DECISION = AnalysisPlaybook(
    id="professional_eight_dimension_boolean_gate",
    title="资深分析师八维买入分析",
    evidence_standard=(
        "区分产业长期趋势与A股当前资金主线，并核验公司业务与当前方向的真实关系。",
        "用定期报告正文、主营构成、订单、量产、客户、份额、技术和盈利能力核验产业竞争力。",
        "核验细分行业周期、供需增量、价格战/内卷以及政策、技术、需求、供给驱动。",
        "核验未来6—12个月公司与行业催化，同时列出定增、解禁、减持等反向事件。",
        "估值同时使用历史、同行、增长与上下行情景，不用单一PE代替赔率判断。",
        "重大风险同时检查现金流、应收、存货、客户集中、负债、商誉、质押、融资、监管和诉讼。",
    ),
    output_contract=(
        "结论先行，给出已通过数、首个阻断维度和未执行维度数。",
        "八个维度按契约顺序执行；首个未达到正向准入条件立即停止该股后续维度。",
        "关键来源、模型或执行故障必须标记分析未完成，不得改写成公司结论。",
        "每个已执行维度写明布尔状态、理由、关键证据和反证。",
        "只有八维全部通过才允许输出可买入，任何维度不得跨项抵消。",
        "多股任务必须证明完整集合覆盖，不能把分批缺失当作完整答案。",
    ),
)


STOCK_DEEP_RESEARCH = AnalysisPlaybook(
    id="stock_deep_research",
    title="完整个股研究",
    evidence_standard=(
        "逐家公司检查业务兑现、连续财务趋势、现金流与资产负债质量。",
        "检查当前、历史、预期与同行估值，不用单一动态 PE 代替完整估值。",
        "检查趋势、波动、量价、资金持续性、公告、风险和催化条件。",
        "来源异常和真实零覆盖必须区分，逐家公司给出证据完整度。",
    ),
    output_contract=(
        "先给公司质量和核心矛盾，再展开业务、财务、估值、预期、交易状态和风险。",
        "给出支持证据、反证、成立条件、失效条件和跟踪指标。",
        "用户没有明确要求交易决策时，不主动给确定性买卖指令。",
    ),
)


class FinancialFilterCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: Literal["debt_ratio", "revenue", "net_profit", "deducted_net_profit"]
    period_basis: Literal["latest_report", "ttm", "previous_fiscal_year", "fiscal_year"]
    fiscal_year: int | None = Field(default=None, ge=1990, le=2100)
    operator: Literal["gt", "gte", "lt", "lte", "eq"]
    threshold: float
    threshold_unit: Literal["percent", "cny", "wan_cny", "yi_cny"]
    action: Literal["exclude_matching", "keep_matching"]

    @model_validator(mode="after")
    def _validate_metric_period_and_unit(self) -> "FinancialFilterCondition":
        if self.metric == "debt_ratio":
            if self.threshold_unit != "percent":
                raise ValueError("debt_ratio threshold_unit must be percent")
            if self.period_basis == "ttm":
                raise ValueError("debt_ratio does not support ttm period basis")
        else:
            if self.threshold_unit == "percent":
                raise ValueError("currency metrics require a CNY threshold unit")
            if self.period_basis == "latest_report":
                raise ValueError("currency metrics require ttm or a fiscal-year period")
            if self.metric == "net_profit" and self.period_basis == "ttm":
                raise ValueError("net_profit currently requires a fiscal-year period")
        if self.metric not in {"net_profit", "deducted_net_profit"} and self.threshold < 0:
            raise ValueError("only profit thresholds may be negative")
        if self.period_basis == "fiscal_year" and self.fiscal_year is None:
            raise ValueError("fiscal_year is required when period_basis is fiscal_year")
        if self.period_basis != "fiscal_year" and self.fiscal_year is not None:
            raise ValueError("fiscal_year is only allowed with fiscal_year period basis")
        return self

    @property
    def normalized_threshold(self) -> float:
        multiplier = {
            "percent": 1.0,
            "cny": 1.0,
            "wan_cny": 10_000.0,
            "yi_cny": 100_000_000.0,
        }[self.threshold_unit]
        return self.threshold * multiplier

    @property
    def metric_label(self) -> str:
        return {
            "debt_ratio": "资产负债率",
            "revenue": "营业收入",
            "net_profit": "归母净利润",
            "deducted_net_profit": "扣非净利润",
        }[self.metric]

    def matches(self, value: float) -> bool:
        threshold = self.normalized_threshold
        return {
            "gt": value > threshold,
            "gte": value >= threshold,
            "lt": value < threshold,
            "lte": value <= threshold,
            "eq": value == threshold,
        }[self.operator]

    def keeps(self, value: float) -> bool:
        matched = self.matches(value)
        return not matched if self.action == "exclude_matching" else matched

    @property
    def identity(self) -> tuple[str, str, int | None]:
        return self.metric, self.period_basis, self.fiscal_year


class CollectionFinancialFilterSpec(BaseModel):
    """One collection transform containing all requested financial predicates."""

    model_config = ConfigDict(extra="forbid")

    conditions: list[FinancialFilterCondition] = Field(min_length=1, max_length=8)



class DomainBoardQuerySpec(BaseModel):
    """One semantic industry domain resolved against the live board catalog.

    The planning model may explain a free-form product domain, but it may only
    select board names supplied by the application.  The data tool subsequently
    verifies those names against the structured constituent source.  Keeping
    this contract typed prevents conversational labels from being mistaken for
    exchange board identifiers.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: str = Field(min_length=1, max_length=64)
    catalog_snapshot_id: str | None = Field(default=None, min_length=1, max_length=64)
    board_id: str | None = Field(default=None, min_length=1, max_length=64)
    board_name: str | None = Field(default=None, min_length=1, max_length=64)
    role_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9_]{0,31}$",
    )
    role_label: str | None = Field(default=None, min_length=1, max_length=64)
    board_queries: list[str] = Field(default_factory=list, max_length=4)
    mapping_type: Literal["catalog_binding", "unresolved"]
    rationale: str = Field(default="", max_length=240)
    unresolved_parts: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _validate_resolution(self) -> "DomainBoardQuerySpec":
        self.board_queries = list(dict.fromkeys(value.strip() for value in self.board_queries if value.strip()))
        self.unresolved_parts = list(dict.fromkeys(value.strip() for value in self.unresolved_parts if value.strip()))
        if self.mapping_type == "unresolved":
            if self.board_queries or self.board_id or self.board_name:
                raise ValueError("unresolved domains cannot contain bound board identity")
            if not self.unresolved_parts:
                self.unresolved_parts = [self.label]
        elif not self.board_queries:
            raise ValueError("resolved domains require at least one board_query")
        if bool(self.board_id) != bool(self.board_name):
            raise ValueError("board_id and board_name must be supplied together")
        return self

class IndustryBenefitRoleV2(BaseModel):
    """One user-relevant value-chain role before it is bound to a board."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    role_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_]{0,31}$")
    label: str = Field(min_length=1, max_length=64)
    benefit_mechanism: str = Field(min_length=1, max_length=240)
    tier: int = Field(ge=1, le=4)

class IndustryBenefitOutlineV2(BaseModel):
    """Compact semantic decomposition produced before catalog binding."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    topic: str = Field(min_length=1, max_length=160)
    roles: tuple[IndustryBenefitRoleV2, ...] = Field(
        min_length=1,
        max_length=16,
    )
    selection_objective: str = Field(min_length=1, max_length=500)

class DomainCatalogSelectionItemV2(BaseModel):
    """One compact binding selected from the supplied finite catalog."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    board_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    role_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_]{0,31}$")
    tier: int = Field(ge=1, le=4)

class DomainCatalogSelectionV2(BaseModel):
    """The complete output of finite-set board selection."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    items: tuple[DomainCatalogSelectionItemV2, ...] = Field(
        min_length=1,
        max_length=512,
    )

class DomainResultSelectionV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["best_one", "top_k", "all_relevant"]
    max_items: int | None = Field(default=None, ge=1, le=512)

    @model_validator(mode="after")
    def _valid_cardinality(self) -> "DomainResultSelectionV2":
        if self.mode == "best_one" and self.max_items != 1:
            raise ValueError("best_one requires max_items=1")
        if self.mode == "top_k" and self.max_items is None:
            raise ValueError("top_k requires max_items")
        if self.mode == "all_relevant" and self.max_items is not None:
            raise ValueError("all_relevant requires max_items=null")
        return self

class DomainSelectionAssumptionV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field_path: str
    value: Any
    reason: str = Field(min_length=1, max_length=300)
    source: Literal["program_default"] = "program_default"

class DomainCollectionCoverageV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    catalog_total: int = Field(ge=0)
    catalog_supplied: int = Field(ge=0)
    selected_count: int = Field(ge=0)
    binding_complete: bool

    @model_validator(mode="after")
    def _consistent_coverage(self) -> "DomainCollectionCoverageV2":
        if self.catalog_supplied > self.catalog_total:
            raise ValueError("catalog_supplied cannot exceed catalog_total")
        expected = self.catalog_total > 0 and self.catalog_supplied == self.catalog_total and self.selected_count > 0
        if self.binding_complete != expected:
            raise ValueError(
                "binding_complete must reflect complete catalog supply and " "a non-empty validated selection"
            )
        return self

class DomainBoardBindingV2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    board_id: str = Field(min_length=1, max_length=64)
    board_name: str = Field(min_length=1, max_length=64)
    role_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_]{0,31}$")
    role_label: str = Field(min_length=1, max_length=64)
    tier: int = Field(ge=1, le=4)
    rationale: str = Field(min_length=1, max_length=360)
    main_flow_rank: int | None = None
    main_net_inflow: float | None = None
    main_net_inflow_pct: float | None = None
    pct_chg: float | None = None

class DomainCollectionV2(BaseModel):
    """Versioned terminal resource published after complete catalog binding."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    type: Literal["domain_collection_v2"] = "domain_collection_v2"
    schema_version: Literal["2.0"] = "2.0"
    catalog_snapshot_id: str = Field(min_length=1, max_length=64)
    requested_topic: str = Field(min_length=1, max_length=500)
    benefit_outline: IndustryBenefitOutlineV2
    boards: tuple[DomainBoardBindingV2, ...] = Field(
        min_length=1,
        max_length=512,
    )
    result_selection: DomainResultSelectionV2
    assumptions: tuple[DomainSelectionAssumptionV2, ...] = ()
    coverage: DomainCollectionCoverageV2
    source_name: str = Field(min_length=1, max_length=200)
    source_date: str = Field(default="", max_length=80)
    lineage: tuple[str, ...] = Field(min_length=1, max_length=16)

class InvestmentThesisContext(BaseModel):
    """Structured thesis evidence reused by professional investment workflows."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: str = Field(default="", max_length=400)
    domains: list[DomainBoardQuerySpec] = Field(default_factory=list, max_length=512)

class ThemeDomainThesis(BaseModel):
    """Why one project board is relevant to the parent investment theme."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: str = Field(min_length=1, max_length=64)
    rationale: str = Field(default="", max_length=300)
    tier: int | None = Field(default=None, ge=1, le=4)

class ThemeEvidenceContext(BaseModel):
    """Parent thesis retained while company facts are checked per sub-domain."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    target_topics: list[str] = Field(min_length=1, max_length=8)
    domain_theses: list[ThemeDomainThesis] = Field(
        default_factory=list,
        max_length=512,
    )

    @model_validator(mode="after")
    def _dedupe_context(self) -> "ThemeEvidenceContext":
        self.target_topics = list(dict.fromkeys(value.strip() for value in self.target_topics if value.strip()))
        if not self.target_topics:
            raise ValueError("target_topics must contain at least one topic")
        by_label: dict[str, ThemeDomainThesis] = {}
        for thesis in self.domain_theses:
            by_label.setdefault(thesis.label, thesis)
        self.domain_theses = list(by_label.values())
        return self

    def thesis_for(self, label: str) -> ThemeDomainThesis | None:
        return next((thesis for thesis in self.domain_theses if thesis.label == label), None)

class MappingSelectionContext(BaseModel):
    """Semantic writing context; it never selects a workflow or a tool."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    topic: str = ""
    objective: str = ""
    selection_mode: Literal["none", "complete_inventory", "ranked_shortlist"] = "none"
    thesis_requirements: list[str] = Field(default_factory=list)
    research_dimensions: list[str] = Field(default_factory=list)

    @property
    def normalized_topic(self) -> str:
        return self.topic.strip()

__all__ = [
    "AnalysisPlaybook",
    "CollectionFinancialFilterSpec",
    "DomainBoardQuerySpec",
    "FinancialFilterCondition",
    "InvestmentThesisContext",
    "INDUSTRY_CHAIN",
    "INVESTMENT_DECISION",
    "MARKET_OUTLOOK",
    "MappingSelectionContext",
    "STOCK_DEEP_RESEARCH",
    "ThemeDomainThesis",
    "ThemeEvidenceContext",
    "THEME_COMPANY_MAPPING",
]


from . import _result_contracts_functions1 as _result_contracts_functions1


def _bind_extracted_function(_member):
    import functools
    import types

    _bound = types.FunctionType(_member.__code__, globals(), _member.__name__, _member.__defaults__, _member.__closure__)
    _bound.__kwdefaults__ = _member.__kwdefaults__
    functools.update_wrapper(_bound, _member)
    return _bound


for _function_module in (_result_contracts_functions1,):
    for _function_name in _function_module.__all__:
        globals()[_function_name] = _bind_extracted_function(getattr(_function_module, _function_name))
