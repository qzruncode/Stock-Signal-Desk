# -*- coding: utf-8 -*-
"""Typed contracts used only after standard-task execution.

This module deliberately contains no routing table, tool name, tool schema or
executor.  It defines result-writing requirements and small typed structures
for deterministic aggregation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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
    id="strict_sequential_buy_decision",
    title="九项严格买入判断",
    evidence_standard=(
        "固定顺序核验当前市场主线真实受益和可验证产业竞争力。",
        "核验未来三年空间、景气上行、不过度内卷和未来6—12个月催化。",
        "核验重大风险、合理估值与利好是否已被股价反映。",
        "最后用当前行情确定买入区间、止损、目标参考和风险收益比。",
        "分业务收入或利润未披露时，允许用订单、销量、客户、产能、量产和连续增速替代核验。",
    ),
    output_contract=(
        "每只股票在首个未通过项停止，后续项目不得抵消失败。",
        "只有九项全部通过才允许输出可买入，数据或分析失败一律不可买入。",
        "可买入项必须给仓位建议、买入区间、止损、风险收益比和逻辑失效条件。",
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

    metric: Literal["debt_ratio", "revenue", "deducted_net_profit"]
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
        if self.metric != "deducted_net_profit" and self.threshold < 0:
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
            "deducted_net_profit": "扣非净利润",
        }[self.metric]


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
    "INDUSTRY_CHAIN",
    "INVESTMENT_DECISION",
    "MappingSelectionContext",
    "STOCK_DEEP_RESEARCH",
    "THEME_COMPANY_MAPPING",
]
