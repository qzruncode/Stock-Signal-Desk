# -*- coding: utf-8 -*-
"""Extensible indicator-combination plan contract for the screening UI.

The existing quantitative screen spec remains the executable ATR contract used
by the agent tool.  This plan is the UI-facing composition layer: common
universe/output settings live once, while each indicator contributes a
condition with its own parameter object.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.services.stock_screening.screen_spec import (
    FinancialFilter,
    OutputField,
    ScreenSort,
    ScreenUniverse,
)


class IndicatorCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    indicator: str = Field(min_length=1, max_length=80)
    parameters: dict[str, Any] = Field(default_factory=dict)


class IndicatorScreenScope(BaseModel):
    """The source universe selected by the settings-page filter."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["all", "watchlist_group"] = "all"
    group_id: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def _validate_group_reference(self) -> "IndicatorScreenScope":
        if self.type == "watchlist_group" and not self.group_id:
            raise ValueError("选择分组筛选范围时必须提供 group_id")
        if self.type == "all" and self.group_id is not None:
            raise ValueError("全部股票筛选范围不能提供 group_id")
        return self


class IndicatorScreenPlan(BaseModel):
    """A composable screen made of one or more catalogued conditions."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["1.0"]
    combination: Literal["all", "any"] = "all"
    conditions: list[IndicatorCondition] = Field(min_length=1, max_length=8)
    scope: IndicatorScreenScope = Field(default_factory=IndicatorScreenScope)
    universe: ScreenUniverse
    financial_filters: list[FinancialFilter] = Field(default_factory=list, max_length=8)
    sort: ScreenSort
    output_fields: list[OutputField] = Field(min_length=1, max_length=12)
    preview_limit: int = Field(ge=1, le=20)


def indicator_screen_plan_schema() -> dict[str, Any]:
    """Return the JSON schema used by the settings API and future clients."""
    return IndicatorScreenPlan.model_json_schema()


__all__ = [
    "IndicatorCondition",
    "IndicatorScreenScope",
    "IndicatorScreenPlan",
    "indicator_screen_plan_schema",
]
