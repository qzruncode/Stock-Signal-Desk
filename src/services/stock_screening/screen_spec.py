# -*- coding: utf-8 -*-
"""Typed contract for deterministic all-market stock screening.

The semantic model may translate natural language into this contract, but it
cannot execute formulas or silently invent omitted conditions.  The executor
validates the same contract again and returns the normalized spec verbatim so
the final answer can prove exactly what was run.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ComparisonOperator = Literal["gt", "gte", "lt", "lte", "eq"]
OutputField = Literal[
    "current_atr_pct",
    "long_term_mean_pct",
    "dynamic_warning_pct",
    "qualified_days",
    "qualified_ratio_pct",
    "revenue_ttm",
    "parent_net_profit_ttm",
    "deducted_net_profit_ttm",
    "debt_ratio",
    "financial_report_period",
    "financial_source",
    "latest_trade_date",
]
SortField = Literal[
    "code",
    "current_atr_pct",
    "long_term_mean_pct",
    "dynamic_warning_pct",
    "qualified_days",
    "qualified_ratio_pct",
    "revenue_ttm",
    "parent_net_profit_ttm",
    "deducted_net_profit_ttm",
    "debt_ratio",
]
FinancialOutputField = Literal[
    "revenue_ttm",
    "parent_net_profit_ttm",
    "deducted_net_profit_ttm",
    "debt_ratio",
    "financial_report_period",
    "financial_source",
]
FinancialSortField = Literal[
    "code",
    "revenue_ttm",
    "parent_net_profit_ttm",
    "deducted_net_profit_ttm",
    "debt_ratio",
]
FinancialField = Literal[
    "revenue_ttm",
    "parent_net_profit_ttm",
    "deducted_net_profit_ttm",
    "debt_ratio",
]


class ScreenUniverse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["active"]
    markets: list[Literal["sh", "sz", "bj"]] = Field(min_length=1, max_length=3)
    include_st: bool
    min_listing_trading_days: int = Field(ge=1, le=1000)
    price_adjustment: Literal["qfq"]
    # Optional explicit code scope used by the settings-page group filter.
    # ``None`` means the normal market universe; ``[]`` intentionally means
    # an empty selected group.
    codes: list[str] | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def _deduplicate_markets(self) -> "ScreenUniverse":
        if len(self.markets) != len(set(self.markets)):
            raise ValueError("markets 不能重复")
        if self.codes is not None:
            cleaned = [str(code).strip() for code in self.codes if str(code).strip()]
            if len(cleaned) != len(set(cleaned)):
                raise ValueError("codes 不能重复")
            self.codes = cleaned
        return self


class AtrRelativeFrequencyRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: Literal["atr_relative_frequency"]
    atr_period: int = Field(ge=2, le=120)
    atr_average: Literal["sma", "ema", "wilder"]
    baseline_period: int = Field(ge=2, le=250)
    baseline_average: Literal["sma", "ema"]
    threshold_operator: Literal["multiply", "divide"]
    threshold_value: float = Field(gt=0, le=100)
    # The original ATR screener used an absolute ATR/close percentage line
    # (2.8 by default).  Keep the dynamic baseline fields for compatibility,
    # but let a supplied absolute threshold take precedence during evaluation.
    volatility_threshold_pct: float | None = Field(default=None, ge=0.1, le=100)
    daily_comparison: ComparisonOperator
    lookback_days: int = Field(ge=1, le=500)
    min_qualified_days: int | None = Field(ge=0, le=500)
    min_qualified_ratio_pct: float | None = Field(ge=0, le=100)

    @model_validator(mode="after")
    def _validate_qualification_thresholds(self) -> "AtrRelativeFrequencyRule":
        if self.min_qualified_days is None and self.min_qualified_ratio_pct is None:
            raise ValueError("达标天数和达标比例至少需要一个条件")
        if self.min_qualified_days is not None and self.min_qualified_days > self.lookback_days:
            raise ValueError("min_qualified_days 不能大于 lookback_days")
        return self


class FinancialFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: FinancialField
    operator: ComparisonOperator
    value: float


class ScreenSort(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: SortField
    order: Literal["asc", "desc"]


class QuantitativeScreenSpec(BaseModel):
    """Complete, executable and auditable stock-screening request."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["1.0"]
    universe: ScreenUniverse
    technical_rule: AtrRelativeFrequencyRule
    financial_filters: list[FinancialFilter] = Field(max_length=8)
    sort: ScreenSort
    output_fields: list[OutputField] = Field(min_length=1, max_length=12)
    preview_limit: int = Field(ge=1, le=20)

    @model_validator(mode="after")
    def _validate_cross_field_contract(self) -> "QuantitativeScreenSpec":
        if len(self.output_fields) != len(set(self.output_fields)):
            raise ValueError("output_fields 不能重复")
        financial_fields = {item.field for item in self.financial_filters}
        if self.sort.field in {"revenue_ttm", "parent_net_profit_ttm", "deducted_net_profit_ttm", "debt_ratio"}:
            financial_fields.add(self.sort.field)
        # Financial report metadata has no meaning unless at least one
        # financial value is requested, filtered or used for sorting.
        if (
            {"financial_report_period", "financial_source"}.intersection(self.output_fields)
            and not financial_fields.intersection(self.output_fields)
            and not self.financial_filters
            and self.sort.field not in {"revenue_ttm", "parent_net_profit_ttm", "deducted_net_profit_ttm", "debt_ratio"}
        ):
            raise ValueError("请求财务报告期或来源时，必须同时请求至少一个财务指标")
        return self

    def required_financial_fields(self) -> set[str]:
        fields = {item.field for item in self.financial_filters}
        fields.update(
            field
            for field in self.output_fields
            if field in {"revenue_ttm", "parent_net_profit_ttm", "deducted_net_profit_ttm", "debt_ratio"}
        )
        if self.sort.field in {"revenue_ttm", "parent_net_profit_ttm", "deducted_net_profit_ttm", "debt_ratio"}:
            fields.add(self.sort.field)
        return fields


class FinancialScreenSort(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: FinancialSortField
    order: Literal["asc", "desc"]


class FinancialScreenSpec(BaseModel):
    """Complete executable contract for a financial-only indicator screen.

    Financial indicators must not be adapted into the ATR contract: doing so
    silently fetches daily bars and applies an unrelated volatility rule.  A
    separate contract keeps the data requirements and the result provenance
    honest while retaining the same universe/filter/output vocabulary.
    """

    model_config = ConfigDict(extra="forbid")

    version: Literal["1.0"]
    universe: ScreenUniverse
    financial_filters: list[FinancialFilter] = Field(max_length=8)
    sort: FinancialScreenSort
    output_fields: list[FinancialOutputField] = Field(min_length=1, max_length=8)
    preview_limit: int = Field(ge=1, le=20)

    @model_validator(mode="after")
    def _validate_fields(self) -> "FinancialScreenSpec":
        if len(self.output_fields) != len(set(self.output_fields)):
            raise ValueError("output_fields 不能重复")
        numeric_fields = {
            "revenue_ttm",
            "parent_net_profit_ttm",
            "deducted_net_profit_ttm",
            "debt_ratio",
        }
        if not (
            self.financial_filters
            or self.sort.field in numeric_fields
            or numeric_fields.intersection(self.output_fields)
        ):
            raise ValueError("财务筛选至少需要一个财务数值字段作为条件、排序或输出")
        return self

    def required_financial_fields(self) -> set[str]:
        fields = {item.field for item in self.financial_filters}
        fields.update(
            field
            for field in self.output_fields
            if field in {
                "revenue_ttm",
                "parent_net_profit_ttm",
                "deducted_net_profit_ttm",
                "debt_ratio",
            }
        )
        if self.sort.field != "code":
            fields.add(self.sort.field)
        return fields


def financial_screen_spec_schema() -> dict[str, Any]:
    """Return the JSON schema for the financial-only screen contract."""

    return FinancialScreenSpec.model_json_schema()


def quantitative_screen_spec_schema(*, nullable: bool = False) -> dict[str, Any]:
    """Return an inline JSON schema accepted by both LLM tool surfaces."""

    number_or_null = ["number", "null"]
    integer_or_null = ["integer", "null"]
    schema: dict[str, Any] = {
        "type": "object",
        "description": (
            "完整且不可省略的筛选规格；所有周期、比较符、阈值、范围、财务条件、排序和输出字段"
            "都必须来自用户当前要求或紧邻上一轮规格，禁止只传修改字段。"
        ),
        "additionalProperties": False,
        "properties": {
            "version": {"type": "string", "enum": ["1.0"]},
            "universe": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "status": {"type": "string", "enum": ["active"]},
                    "markets": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["sh", "sz", "bj"]},
                        "minItems": 1,
                        "maxItems": 3,
                    },
                    "include_st": {"type": "boolean"},
                    "min_listing_trading_days": {"type": "integer", "minimum": 1, "maximum": 1000},
                    "price_adjustment": {"type": "string", "enum": ["qfq"]},
                    "codes": {
                        "type": ["array", "null"],
                        "items": {"type": "string"},
                        "maxItems": 5000,
                    },
                },
                "required": ["status", "markets", "include_st", "min_listing_trading_days", "price_adjustment"],
            },
            "technical_rule": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "strategy": {"type": "string", "enum": ["atr_relative_frequency"]},
                    "atr_period": {"type": "integer", "minimum": 2, "maximum": 120},
                    "atr_average": {"type": "string", "enum": ["sma", "ema", "wilder"]},
                    "baseline_period": {"type": "integer", "minimum": 2, "maximum": 250},
                    "baseline_average": {"type": "string", "enum": ["sma", "ema"]},
                    "threshold_operator": {"type": "string", "enum": ["multiply", "divide"]},
                    "threshold_value": {"type": "number", "exclusiveMinimum": 0, "maximum": 100},
                    "volatility_threshold_pct": {"type": number_or_null, "minimum": 0.1, "maximum": 100},
                    "daily_comparison": {"type": "string", "enum": ["gt", "gte", "lt", "lte", "eq"]},
                    "lookback_days": {"type": "integer", "minimum": 1, "maximum": 500},
                    "min_qualified_days": {"type": integer_or_null, "minimum": 0, "maximum": 500},
                    "min_qualified_ratio_pct": {"type": number_or_null, "minimum": 0, "maximum": 100},
                },
                "required": [
                    "strategy",
                    "atr_period",
                    "atr_average",
                    "baseline_period",
                    "baseline_average",
                    "threshold_operator",
                    "threshold_value",
                    "daily_comparison",
                    "lookback_days",
                    "min_qualified_days",
                    "min_qualified_ratio_pct",
                ],
            },
            "financial_filters": {
                "type": "array",
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "field": {
                            "type": "string",
                            "enum": [
                                "revenue_ttm",
                                "parent_net_profit_ttm",
                                "deducted_net_profit_ttm",
                                "debt_ratio",
                            ],
                        },
                        "operator": {"type": "string", "enum": ["gt", "gte", "lt", "lte", "eq"]},
                        "value": {"type": "number"},
                    },
                    "required": ["field", "operator", "value"],
                },
            },
            "sort": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "field": {
                        "type": "string",
                        "enum": [
                            "code",
                            "current_atr_pct",
                            "long_term_mean_pct",
                            "dynamic_warning_pct",
                            "qualified_days",
                            "qualified_ratio_pct",
                            "revenue_ttm",
                            "parent_net_profit_ttm",
                            "deducted_net_profit_ttm",
                            "debt_ratio",
                        ],
                    },
                    "order": {"type": "string", "enum": ["asc", "desc"]},
                },
                "required": ["field", "order"],
            },
            "output_fields": {
                "type": "array",
                "minItems": 1,
                "maxItems": 12,
                "uniqueItems": True,
                "items": {
                    "type": "string",
                    "enum": [
                        "current_atr_pct",
                        "long_term_mean_pct",
                        "dynamic_warning_pct",
                        "qualified_days",
                        "qualified_ratio_pct",
                        "revenue_ttm",
                        "parent_net_profit_ttm",
                        "deducted_net_profit_ttm",
                        "debt_ratio",
                        "financial_report_period",
                        "financial_source",
                        "latest_trade_date",
                    ],
                },
            },
            "preview_limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "required": [
            "version",
            "universe",
            "technical_rule",
            "financial_filters",
            "sort",
            "output_fields",
            "preview_limit",
        ],
    }
    if nullable:
        schema["type"] = ["object", "null"]
    return schema


__all__ = [
    "AtrRelativeFrequencyRule",
    "FinancialFilter",
    "FinancialScreenSpec",
    "FinancialScreenSort",
    "QuantitativeScreenSpec",
    "ScreenSort",
    "ScreenUniverse",
    "quantitative_screen_spec_schema",
    "financial_screen_spec_schema",
]
