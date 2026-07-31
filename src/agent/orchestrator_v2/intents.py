# -*- coding: utf-8 -*-
"""Model-visible business intents for the unified Agent control plane.

Every capability has one closed Pydantic schema.  Security identifiers,
upstream collections, tool names, retries, cache knobs and other execution
details deliberately do not appear in these models.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from src.agent.orchestrator_v2.contracts import StrictModel
from src.services.buy_criteria.mainline_policy import MainlineStrategyProfile
from src.services.stock_screening.screen_spec import QuantitativeScreenSpec


class OutputRequestV2(StrictModel):
    language: Literal["zh-CN", "en-US"] | None = None
    format: Literal["concise", "detailed", "table", "list"] | None = None
    include_assumptions: bool | None = None


class EmptyIntent(StrictModel):
    pass


class ConfirmationSignal(StrictModel):
    user_confirmed: bool = False


GeneralResponseIntent = EmptyIntent
RealtimeQuoteIntent = EmptyIntent
CatalystAnalysisIntent = EmptyIntent
DataHealthIntent = EmptyIntent
TradeExecutionIntent = EmptyIntent
MarketMainlineResearchIntent = EmptyIntent


class SecurityLookupIntent(StrictModel):
    query: str | None = Field(default=None, min_length=1, max_length=120)
    market: Literal["sh", "sz", "bj", "hk", "us"] | None = None
    sector: str | None = Field(default=None, min_length=1, max_length=80)
    limit: int | None = Field(default=None, ge=1, le=100)

    @model_validator(mode="after")
    def _has_constraint(self) -> "SecurityLookupIntent":
        if not any((self.query, self.market, self.sector)):
            raise ValueError("query, market or sector is required")
        return self


class RecentPriceRange(StrictModel):
    kind: Literal["recent"]
    count: int = Field(ge=2, le=1000)


class ExplicitPriceRange(StrictModel):
    kind: Literal["date_range"]
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def _ordered(self) -> "ExplicitPriceRange":
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        return self


PriceRange = Annotated[
    RecentPriceRange | ExplicitPriceRange,
    Field(discriminator="kind"),
]


class PriceHistoryIntent(StrictModel):
    period: PriceRange | None = None


class TechnicalAnalysisIntent(StrictModel):
    """Technical-analysis semantics.

    Historical fetch depth is deliberately absent: it is an execution detail
    selected by the capability compiler, not a user/model-controlled field.
    """


class FundamentalAnalysisIntent(StrictModel):
    periods: int | None = Field(default=None, ge=1, le=20)
    business_category: Literal["industry", "product", "region"] | None = None


class ValuationAnalysisIntent(StrictModel):
    include_history: bool | None = None
    consensus_metric: Literal["eps", "revenue", "net_profit"] | None = None
    peer_dimension: Literal["valuation", "growth", "profitability", "quality", "all"] | None = None


class FinancialStatementAnalysisIntent(StrictModel):
    periods: int | None = Field(default=None, ge=1, le=20)


NewsTopic = Literal["market", "company", "announcement", "research", "macro", "industry", "social"]


class NewsAnalysisIntent(StrictModel):
    query: str | None = Field(default=None, min_length=1, max_length=500)
    topic: NewsTopic | None = None
    subjects: tuple[str, ...] = Field(default_factory=tuple, max_length=20)
    days: int | None = Field(default=None, ge=1, le=3650)
    limit: int | None = Field(default=None, ge=1, le=100)

    @field_validator("subjects")
    @classmethod
    def _unique_subjects(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.strip() for item in value if item.strip()))


class AnnouncementAnalysisIntent(StrictModel):
    days: int | None = Field(default=None, ge=1, le=3650)
    limit: int | None = Field(default=None, ge=1, le=100)


RiskAnalysisIntent = AnnouncementAnalysisIntent
ResearchReportAnalysisIntent = AnnouncementAnalysisIntent


class RegulatoryAnalysisIntent(StrictModel):
    keyword: str | None = Field(default=None, min_length=1, max_length=200)
    event_type: str | None = Field(default=None, min_length=1, max_length=80)
    market: Literal["sh", "sz", "bj", "all"] | None = None
    days: int | None = Field(default=None, ge=1, le=3650)
    limit: int | None = Field(default=None, ge=1, le=100)
    project_type: str | None = Field(default=None, min_length=1, max_length=80)
    project_stage: str | None = Field(default=None, min_length=1, max_length=80)
    project_status: str | None = Field(default=None, min_length=1, max_length=80)


class SocialSentimentAnalysisIntent(StrictModel):
    days: int | None = Field(default=None, ge=1, le=365)
    limit: int | None = Field(default=None, ge=1, le=200)


class StockComparisonIntent(StrictModel):
    dimension: Literal["valuation", "growth", "profitability", "quality", "all"] | None = None
    include_peers: bool | None = None


class StockDeepResearchIntent(StrictModel):
    thesis: str | None = Field(default=None, max_length=1000)


class InvestmentDecisionIntent(StrictModel):
    thesis: str | None = Field(default=None, max_length=1_000)
    mainline_strategy: MainlineStrategyProfile | None = Field(
        default=None,
        description=(
            "用户表达的主线投资时机：confirmed_mainline 只接受已经确认的"
            "当前主线；early_positioning 接受满足前瞻准入条件的候选主线。"
            "用户未表达时留空，由程序采用确认型默认值。"
        ),
    )
    output: OutputRequestV2 | None = None


class MarketOverviewIntent(StrictModel):
    days: int | None = Field(default=None, ge=1, le=365)
    include_index: bool | None = None
    index_code: str | None = Field(default=None, pattern=r"^[A-Za-z0-9.]{2,16}$")


class SectorAnalysisIntent(StrictModel):
    sector_type: Literal["industry", "concept"] | None = None
    period: Literal["today", "5d", "10d"] | None = None
    top_n: int | None = Field(default=None, ge=1, le=100)
    query: str | None = Field(default=None, min_length=1, max_length=500)
    subjects: tuple[str, ...] = Field(default_factory=tuple, max_length=20)
    days: int | None = Field(default=None, ge=1, le=3650)
    limit: int | None = Field(default=None, ge=1, le=100)


class CapitalFlowAnalysisIntent(StrictModel):
    days: int | None = Field(default=None, ge=1, le=365)


MacroIndicator = Literal["PMI", "CPI", "PPI", "GDP", "M2", "社融", "LPR"]


class MacroAnalysisIntent(StrictModel):
    indicators: tuple[MacroIndicator, ...] = Field(default_factory=tuple, max_length=5)
    periods: int | None = Field(default=None, ge=1, le=120)
    bond_yield: bool = False
    country: Literal["cn", "us"] | None = None
    term: Literal["2y", "5y", "10y", "30y"] | None = None
    days: int | None = Field(default=None, ge=1, le=3650)
    monetary_operations: bool = False
    instrument: str | None = Field(default=None, min_length=1, max_length=80)
    research_query: str | None = Field(default=None, min_length=1, max_length=500)
    research_subjects: tuple[str, ...] = Field(default_factory=tuple, max_length=20)
    limit: int | None = Field(default=None, ge=1, le=100)

    @model_validator(mode="after")
    def _has_source(self) -> "MacroAnalysisIntent":
        if not (self.indicators or self.bond_yield or self.monetary_operations or self.research_query):
            raise ValueError("at least one macro source is required")
        if self.bond_yield and (not self.country or not self.term):
            raise ValueError("bond_yield requires country and term")
        if self.research_query and not self.research_subjects:
            raise ValueError("research_query requires research_subjects")
        return self


class IndustryResearchIntent(StrictModel):
    explicit_subjects: tuple[str, ...] = Field(
        min_length=1,
        max_length=12,
        description=(
            "Only the concrete research subjects explicitly named by the user. "
            "Do not add inferred sub-sectors, components, applications, analysis "
            "dimensions, or conclusions."
        ),
    )

    @field_validator("explicit_subjects")
    @classmethod
    def _explicit_subjects(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(item.strip() for item in value if item.strip()))
        if not normalized:
            raise ValueError("explicit_subjects must contain at least one value")
        return normalized


class ThemeStockDiscoveryIntent(StrictModel):
    selection_mode: Literal["named_subset", "all_bound"] = Field(
        description=(
            "named_subset 表示只消费用户明确点名的领域；all_bound 表示用户明确" "要求消费上游结构化集合中的全部领域。"
        ),
    )
    themes: tuple[str, ...] = Field(default_factory=tuple, max_length=12)
    output: OutputRequestV2 | None = None

    @field_validator("themes")
    @classmethod
    def _themes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.strip() for item in value if item.strip()))

    @model_validator(mode="after")
    def _selection_contract(self) -> "ThemeStockDiscoveryIntent":
        if self.selection_mode == "named_subset" and not self.themes:
            raise ValueError("named_subset requires at least one theme")
        if self.selection_mode == "all_bound" and self.themes:
            raise ValueError("all_bound requires themes to be empty")
        return self


class ThemeBusinessEvidenceIntent(StrictModel):
    focus: str | None = Field(default=None, min_length=1, max_length=500)
    days: int | None = Field(default=None, ge=30, le=730)
    output: OutputRequestV2 | None = None


class StockScreeningIntent(StrictModel):
    screen_spec: QuantitativeScreenSpec
    save_group_name: str | None = Field(default=None, min_length=1, max_length=80)


class LatestReportPeriod(StrictModel):
    kind: Literal["latest_report"]


class TtmPeriod(StrictModel):
    kind: Literal["ttm"]


class PreviousFiscalYearPeriod(StrictModel):
    kind: Literal["previous_fiscal_year"]


class FiscalYearPeriod(StrictModel):
    kind: Literal["fiscal_year"]
    year: int = Field(ge=1990, le=2100)


FinancialPeriod = Annotated[
    LatestReportPeriod | TtmPeriod | PreviousFiscalYearPeriod | FiscalYearPeriod,
    Field(discriminator="kind"),
]


class MoneyAmount(StrictModel):
    value: float
    unit: Literal["cny", "wan_cny", "yi_cny"]


FilterOperator = Literal["gt", "gte", "lt", "lte", "eq"]
FilterAction = Literal["exclude_matching", "keep_matching"]


class DebtRatioPredicate(StrictModel):
    metric: Literal["debt_ratio"]
    operator: FilterOperator
    percent: float = Field(ge=0, le=1000)
    period: FinancialPeriod | None = None
    action: FilterAction


class RevenuePredicate(StrictModel):
    metric: Literal["revenue"]
    operator: FilterOperator
    amount: MoneyAmount
    period: FinancialPeriod | None = None
    action: FilterAction


class NetProfitPredicate(StrictModel):
    metric: Literal["net_profit"]
    operator: FilterOperator
    amount: MoneyAmount
    period: FinancialPeriod | None = None
    action: FilterAction


class DeductedNetProfitPredicate(StrictModel):
    metric: Literal["deducted_net_profit"]
    operator: FilterOperator
    amount: MoneyAmount
    period: FinancialPeriod | None = None
    action: FilterAction


FinancialPredicate = Annotated[
    DebtRatioPredicate | RevenuePredicate | NetProfitPredicate | DeductedNetProfitPredicate,
    Field(discriminator="metric"),
]


class CollectionFinancialFilterIntent(StrictModel):
    predicates: tuple[FinancialPredicate, ...] = Field(min_length=1, max_length=16)
    output: OutputRequestV2 | None = None

    @model_validator(mode="after")
    def _unique_predicates(self) -> "CollectionFinancialFilterIntent":
        keys = [
            (
                predicate.metric,
                predicate.operator,
                predicate.action,
                predicate.period.model_dump_json() if predicate.period is not None else "",
            )
            for predicate in self.predicates
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate financial predicates are not allowed")
        return self


class WatchlistQueryIntent(StrictModel):
    themes: tuple[str, ...] = Field(default_factory=tuple, max_length=12)
    group: str | None = Field(default=None, min_length=1, max_length=80)


class WatchlistMutationIntent(ConfirmationSignal):
    action: Literal["add", "remove"]


class WatchlistGroupManagementIntent(ConfirmationSignal):
    action: Literal["list", "create", "rename", "delete", "add", "remove"]
    group: str | None = Field(default=None, min_length=1, max_length=80)
    new_name: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def _operation_fields(self) -> "WatchlistGroupManagementIntent":
        if self.action in {"create", "delete", "add", "remove"} and not self.group:
            raise ValueError(f"{self.action} requires group")
        if self.action == "rename" and (not self.group or not self.new_name):
            raise ValueError("rename requires group and new_name")
        return self


class FormalAnalysisIntent(ConfirmationSignal):
    action: Literal["start", "status"]
    task_id: str | None = Field(default=None, min_length=1, max_length=80)
    status: str | None = Field(default=None, min_length=1, max_length=40)
    limit: int | None = Field(default=None, ge=1, le=100)
    template_id: int | None = Field(default=None, ge=1)


class AnalysisHistoryIntent(ConfirmationSignal):
    action: Literal["search", "read", "delete"]
    record_id: int | None = Field(default=None, ge=1)
    record_ids: tuple[int, ...] = Field(default_factory=tuple, max_length=100)
    include_markdown: bool | None = None
    include_news: bool | None = None
    start_date: date | None = None
    end_date: date | None = None
    page: int | None = Field(default=None, ge=1)
    limit: int | None = Field(default=None, ge=1, le=100)

    @model_validator(mode="after")
    def _operation_fields(self) -> "AnalysisHistoryIntent":
        if self.action == "read" and self.record_id is None:
            raise ValueError("read requires record_id")
        if self.action == "delete" and not self.record_ids:
            raise ValueError("delete requires record_ids")
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        return self


class AnalysisTemplateManagementIntent(ConfirmationSignal):
    action: Literal["list", "get", "create", "update", "set_default", "delete"]
    template_id: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    content: str | None = Field(default=None, min_length=1, max_length=50_000)
    set_default: bool | None = None

    @model_validator(mode="after")
    def _operation_fields(self) -> "AnalysisTemplateManagementIntent":
        if self.action in {"get", "update", "set_default", "delete"} and self.template_id is None:
            raise ValueError(f"{self.action} requires template_id")
        if self.action == "create" and (not self.name or not self.content):
            raise ValueError("create requires name and content")
        if self.action == "update" and not any((self.name, self.content, self.set_default is not None)):
            raise ValueError("update requires name, content or set_default")
        return self


class BatchAnalysisIntent(ConfirmationSignal):
    scope: Literal["symbols", "watchlist", "configured", "group"]
    group_name: str | None = Field(default=None, min_length=1, max_length=80)
    analysis_mode: Literal["template", "buy_criteria"]
    template_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _scope_fields(self) -> "BatchAnalysisIntent":
        if self.scope == "group" and not self.group_name:
            raise ValueError("group scope requires group_name")
        if self.analysis_mode == "template" and self.template_id is None:
            raise ValueError("template analysis requires template_id")
        return self


class BatchRunManagementIntent(ConfirmationSignal):
    action: Literal[
        "list",
        "status",
        "detail",
        "report",
        "pause",
        "continue",
        "resume_failed",
        "regenerate_report",
        "notify",
        "stop",
        "delete",
    ]
    run_id: str | None = Field(default=None, min_length=1, max_length=80)
    limit: int | None = Field(default=None, ge=1, le=100)

    @model_validator(mode="after")
    def _operation_fields(self) -> "BatchRunManagementIntent":
        if self.action not in {"list", "status"} and not self.run_id:
            raise ValueError(f"{self.action} requires run_id")
        return self


class AnalysisScheduleManagementIntent(ConfirmationSignal):
    action: Literal["get", "update"]
    enabled: bool | None = None
    times: tuple[str, ...] = Field(default_factory=tuple, max_length=12)
    template_id: int | None = Field(default=None, ge=1)

    @field_validator("times")
    @classmethod
    def _valid_times(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            parts = item.split(":")
            if len(parts) != 2 or not all(part.isdigit() for part in parts):
                raise ValueError("times must use HH:MM")
            hour, minute = map(int, parts)
            if hour > 23 or minute > 59:
                raise ValueError("times must use HH:MM")
        return tuple(dict.fromkeys(value))

    @model_validator(mode="after")
    def _operation_fields(self) -> "AnalysisScheduleManagementIntent":
        if self.action == "update" and self.enabled is None:
            raise ValueError("update requires enabled")
        if self.action == "update" and self.enabled and (not self.times or self.template_id is None):
            raise ValueError("enabling schedule requires times and template_id")
        return self


class NotificationIntent(ConfirmationSignal):
    action: Literal["status", "send"]
    content_type: Literal["analysis_report", "batch_report", "custom"] | None = None
    message: str | None = Field(default=None, min_length=1, max_length=20_000)
    record_id: int | None = Field(default=None, ge=1)
    batch_run_id: str | None = Field(default=None, min_length=1, max_length=80)
    title: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def _content_fields(self) -> "NotificationIntent":
        if self.action == "status":
            return self
        if self.content_type is None:
            raise ValueError("send requires content_type")
        if self.content_type == "analysis_report" and self.record_id is None:
            raise ValueError("analysis_report requires record_id")
        if self.content_type == "batch_report" and not self.batch_run_id:
            raise ValueError("batch_report requires batch_run_id")
        if self.content_type == "custom" and not self.message:
            raise ValueError("custom requires message")
        return self


class FinancialSourceDiscoveryIntent(StrictModel):
    route_path: str | None = Field(default=None, min_length=1, max_length=500)
    keyword: str | None = Field(default=None, min_length=1, max_length=120)
    namespace: str | None = Field(default=None, min_length=1, max_length=80)
    capability: str | None = Field(default=None, min_length=1, max_length=80)
    limit: int | None = Field(default=None, ge=1, le=100)


ScalarValue = str | int | float | bool


class NamedScalarInput(StrictModel):
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
    value: ScalarValue


class FinancialFeedReadIntent(StrictModel):
    route_path: str = Field(min_length=1, max_length=500)
    inputs: tuple[NamedScalarInput, ...] = Field(default_factory=tuple, max_length=20)
    namespace: str | None = Field(default=None, min_length=1, max_length=80)
    limit: int | None = Field(default=None, ge=1, le=100)


class FinancialArticleReadIntent(StrictModel):
    route_path: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=500)


class WebpageFeedTransformIntent(StrictModel):
    url: str = Field(min_length=8, max_length=2000)
    item_selector: str | None = Field(default=None, min_length=1, max_length=500)
    title_selector: str | None = Field(default=None, min_length=1, max_length=500)
    link_selector: str | None = Field(default=None, min_length=1, max_length=500)
    description_selector: str | None = Field(default=None, min_length=1, max_length=500)
    published_selector: str | None = Field(default=None, min_length=1, max_length=500)
    content_selector: str | None = Field(default=None, min_length=1, max_length=500)
    encoding: str | None = Field(default=None, min_length=1, max_length=40)
    limit: int | None = Field(default=None, ge=1, le=100)


class FinancialFeedExportIntent(ConfirmationSignal):
    route_path: str = Field(min_length=1, max_length=500)
    inputs: tuple[NamedScalarInput, ...] = Field(default_factory=tuple, max_length=20)
    namespace: str | None = Field(default=None, min_length=1, max_length=80)
    format: Literal["json", "rss", "atom", "csv"] | None = None
    limit: int | None = Field(default=None, ge=1, le=1000)


class PublicWebResearchIntent(StrictModel):
    query: str | None = Field(default=None, min_length=1, max_length=500)
    url: str | None = Field(default=None, min_length=8, max_length=2000)

    @model_validator(mode="after")
    def _one_target(self) -> "PublicWebResearchIntent":
        if bool(self.query) == bool(self.url):
            raise ValueError("provide exactly one of query or url")
        return self


def previous_fiscal_year(today: date) -> int:
    return today.year - 1


__all__ = [name for name in globals() if name.endswith("Intent")] + [
    "CollectionFinancialFilterIntent",
    "DebtRatioPredicate",
    "DeductedNetProfitPredicate",
    "EmptyIntent",
    "FinancialPeriod",
    "FinancialPredicate",
    "FiscalYearPeriod",
    "GeneralResponseIntent",
    "InvestmentDecisionIntent",
    "LatestReportPeriod",
    "MoneyAmount",
    "NetProfitPredicate",
    "OutputRequestV2",
    "PreviousFiscalYearPeriod",
    "RevenuePredicate",
    "ThemeStockDiscoveryIntent",
    "TtmPeriod",
    "previous_fiscal_year",
]
