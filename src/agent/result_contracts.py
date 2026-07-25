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
        evidence = "\n".join(
            f"{index}. {item}"
            for index, item in enumerate(self.evidence_standard, 1)
        )
        output = "\n".join(
            f"{index}. {item}"
            for index, item in enumerate(self.output_contract, 1)
        )
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


THEME_COMPANY_MAPPING = AnalysisPlaybook(
    id="theme_company_mapping",
    title="产业主题到 A 股公司的证据化映射",
    evidence_standard=(
        "候选身份必须来自结构化板块和本地证券库，概念成员只能作为 L1 候选证据。",
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
        "八个维度按契约顺序执行；首个不通过或证据不足立即停止该股后续维度。",
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


class CollectionFinancialFilterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: Literal["debt_ratio", "revenue", "net_profit", "deducted_net_profit"]
    period_basis: Literal[
        "latest_report", "ttm", "previous_fiscal_year", "fiscal_year"
    ]
    fiscal_year: int | None = Field(default=None, ge=1990, le=2100)
    operator: Literal["gt", "gte", "lt", "lte", "eq"]
    threshold: float
    threshold_unit: Literal["percent", "cny", "wan_cny", "yi_cny"]
    action: Literal["exclude_matching", "keep_matching"]

    @model_validator(mode="after")
    def _validate_metric_period_and_unit(self) -> "CollectionFinancialFilterSpec":
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


def project_collection_financial_filter_entities(
    input_entities: Iterable[Mapping[str, Any]],
    result_context: Iterable[Mapping[str, Any]],
    parameters: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Project the exact retained collection from typed financial results.

    The rendered Markdown is deliberately irrelevant here.  A follow-up such
    as "这些里面哪些能买" must inherit the program-computed output set, not
    the input set and not a sample copied by the planning model.
    """
    spec = CollectionFinancialFilterSpec.model_validate(parameters)
    ordered = [
        {
            "symbol": str(item.get("symbol") or "").strip(),
            "name": str(item.get("name") or item.get("symbol") or "").strip(),
        }
        for item in input_entities
        if str(item.get("symbol") or "").strip()
    ]
    rows: dict[str, Mapping[str, Any]] = {}
    for result in result_context:
        if not isinstance(result, Mapping) or result.get("success") is False:
            continue
        for item in result.get("items") or []:
            if not isinstance(item, Mapping):
                continue
            symbol = str(item.get("symbol") or "").strip()
            if symbol and isinstance(item.get("financial_value"), (int, float)):
                rows[symbol] = item

    # An incomplete transform has no authoritative output collection.  Failing
    # closed here prevents a partial batch from silently becoming the next
    # conversational universe.
    if any(item["symbol"] not in rows for item in ordered):
        return []

    threshold = spec.normalized_threshold
    comparator = {
        "gt": lambda value: value > threshold,
        "gte": lambda value: value >= threshold,
        "lt": lambda value: value < threshold,
        "lte": lambda value: value <= threshold,
        "eq": lambda value: value == threshold,
    }[spec.operator]
    retained: list[dict[str, str]] = []
    for entity in ordered:
        matched = comparator(float(rows[entity["symbol"]]["financial_value"]))
        keep = not matched if spec.action == "exclude_matching" else matched
        if keep:
            retained.append(entity)
    return retained


def project_task_output_entities(
    kind: str,
    input_entities: Iterable[Mapping[str, Any]],
    result_context: Iterable[Mapping[str, Any]],
    parameters: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Return the task's typed output collection according to its contract."""
    if kind == "collection_financial_filter":
        return project_collection_financial_filter_entities(
            input_entities,
            result_context,
            parameters,
        )

    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            symbol = str(
                value.get("symbol")
                or value.get("code")
                or value.get("stock_code")
                or ""
            ).strip()
            if len(symbol) == 6 and symbol.isdigit() and symbol not in seen:
                seen.add(symbol)
                found.append({
                    "symbol": symbol,
                    "name": str(
                        value.get("name")
                        or value.get("stock_name")
                        or symbol
                    ).strip(),
                })
            for child in value.values():
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)

    for result in result_context:
        visit(result)
    if found:
        return found
    return [
        {
            "symbol": str(item.get("symbol") or "").strip(),
            "name": str(item.get("name") or item.get("symbol") or "").strip(),
        }
        for item in input_entities
        if str(item.get("symbol") or "").strip()
    ]


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
    board_queries: list[str] = Field(default_factory=list, max_length=4)
    mapping_type: Literal["catalog_binding", "unresolved"]
    rationale: str = Field(default="", max_length=240)
    unresolved_parts: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _validate_resolution(self) -> "DomainBoardQuerySpec":
        self.board_queries = list(dict.fromkeys(
            value.strip() for value in self.board_queries if value.strip()
        ))
        self.unresolved_parts = list(dict.fromkeys(
            value.strip() for value in self.unresolved_parts if value.strip()
        ))
        if self.mapping_type == "unresolved":
            if self.board_queries:
                raise ValueError("unresolved domains cannot contain board_queries")
            if not self.unresolved_parts:
                self.unresolved_parts = [self.label]
        elif not self.board_queries:
            raise ValueError("resolved domains require at least one board_query")
        return self


class InvestmentThesisContext(BaseModel):
    """Structured thesis evidence reused by professional investment workflows."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: str = Field(default="", max_length=400)
    domains: list[DomainBoardQuerySpec] = Field(default_factory=list, max_length=12)


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
    "InvestmentThesisContext",
    "INDUSTRY_CHAIN",
    "INVESTMENT_DECISION",
    "MappingSelectionContext",
    "STOCK_DEEP_RESEARCH",
    "THEME_COMPANY_MAPPING",
]
