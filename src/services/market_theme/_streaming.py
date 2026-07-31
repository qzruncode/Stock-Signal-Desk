# -*- coding: utf-8 -*-
"""liteLLM streaming, stream-part parsing and model-report streaming orchestration."""

from __future__ import annotations

import asyncio
from enum import Enum
import json
import logging
from typing import Any, Callable, Literal, Mapping, Optional

import litellm
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from src.llm.anthropic_gateway import (
    build_litellm_kwargs,
    resolve_anthropic_gateway_config,
)
from src.llm.generation_params import apply_litellm_generation_params
from src.storage import DatabaseManager, persist_llm_usage
from src.services.buy_criteria.mainline_policy import (
    MainlineLifecycle,
    MainlineTriggerProgress,
)

from ._llm import (
    _validate_model_report,
    build_model_report_prompts,
    build_streaming_report_draft,
)
from ._context import build_report_evidence_pack, collect_context

logger = logging.getLogger(__name__)

_MARKET_MAINLINE_REPORT_TOOL_NAME = "submit_market_mainline_report"
_MARKET_MAINLINE_MAX_TOKENS = 16_000


class MarketMainlineStageV3(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: str
    description: str


class CurrentMarketMainlineV3(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
    )

    name: str
    lifecycle: MainlineLifecycle
    stage: str
    reason: str
    branches: list[str]
    focus: str
    risks: list[str]
    evidence_refs: list[str]

    @model_validator(mode="after")
    def _confirmed_lifecycle(self) -> "CurrentMarketMainlineV3":
        if self.lifecycle not in {
            MainlineLifecycle.CONFIRMED,
            MainlineLifecycle.EXPANDING,
            MainlineLifecycle.FADING,
        }:
            raise ValueError("current mainline lifecycle must be confirmed, expanding or fading")
        return self


class CandidateMainlineTriggerV3(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
    )

    description: str
    status: MainlineTriggerProgress
    evidence_refs: list[str] = Field(default_factory=list)


MainlineEvidenceAxis = Literal[
    "institution_consensus",
    "policy",
    "supply_demand",
    "technology",
    "capital_expenditure",
    "continuous_prosperity",
]


class CandidateMainlineEvidenceAxisV3(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    axis: MainlineEvidenceAxis
    evidence_refs: list[str] = Field(min_length=1)


class CandidateMarketMainlineV3(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
    )

    name: str
    lifecycle: MainlineLifecycle
    stage_hint: str
    reason: str
    branches: list[str]
    expected_horizon: Literal[
        "one_to_six_months",
        "six_to_twelve_months",
        "over_twelve_months",
    ]
    evidence_axes: list[CandidateMainlineEvidenceAxisV3]
    trigger_assessments: list[CandidateMainlineTriggerV3]
    evidence_refs: list[str]

    @model_validator(mode="after")
    def _candidate_lifecycle(self) -> "CandidateMarketMainlineV3":
        if self.lifecycle not in {
            MainlineLifecycle.EMERGING,
            MainlineLifecycle.VALIDATING,
        }:
            raise ValueError("candidate mainline lifecycle must be emerging or validating")
        return self


class MarketMainlineEvidenceDigestV3(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    policy: list[str]
    industry: list[str]
    market: list[str]


class MarketMainlineReportV3(BaseModel):
    """Single source for provider Schema and local runtime validation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    generated_at: str
    as_of_date: str
    overview: str
    full_report: str
    market_stage: MarketMainlineStageV3
    current_mainlines: list[CurrentMarketMainlineV3]
    candidate_mainlines: list[CandidateMarketMainlineV3]
    action_summary: list[str]
    evidence_digest: MarketMainlineEvidenceDigestV3



class MarketMainlineSchemaError(ValueError):
    """Retain the exact invalid output for one targeted repair."""

    def __init__(
        self,
        message: str,
        *,
        payload: dict[str, Any],
        issues: list[dict[str, Any]],
    ) -> None:
        super().__init__(message)
        self.payload = payload
        self.issues = issues


from . import __streaming_functions1 as __streaming_functions1
from . import __streaming_functions2 as __streaming_functions2
from . import __streaming_functions3 as __streaming_functions3


def _bind_extracted_function(_member):
    import functools
    import types

    _bound = types.FunctionType(_member.__code__, globals(), _member.__name__, _member.__defaults__, _member.__closure__)
    _bound.__kwdefaults__ = _member.__kwdefaults__
    functools.update_wrapper(_bound, _member)
    return _bound


for _function_module in (__streaming_functions1, __streaming_functions2, __streaming_functions3):
    for _function_name in _function_module.__all__:
        globals()[_function_name] = _bind_extracted_function(getattr(_function_module, _function_name))


_MARKET_MAINLINE_REPORT_TOOL = {
    "type": "function",
    "function": {
        "name": _MARKET_MAINLINE_REPORT_TOOL_NAME,
        "description": ("提交仅基于给定证据包形成的A股市场主线结构化报告"),
        "parameters": _inline_json_schema(MarketMainlineReportV3.model_json_schema()),
    },
}
