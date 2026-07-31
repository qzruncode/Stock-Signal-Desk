# -*- coding: utf-8 -*-
"""Contracts for Agent quality evaluation and explicit user feedback."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


TerminalRunStatus = Literal[
    "completed",
    "partial",
    "failed",
    "cancelled",
    "blocked",
]
FeedbackCategory = Literal[
    "correctness",
    "evidence",
    "coverage",
    "timeliness",
    "clarity",
    "other",
]


class AgentEvaluationExpectations(BaseModel):
    """Deterministic acceptance contract for one evaluation case."""

    model_config = ConfigDict(extra="forbid")

    required_capabilities: list[str] = Field(default_factory=list)
    forbidden_capabilities: list[str] = Field(default_factory=list)
    maximum_nodes: int | None = Field(None, ge=1, le=500)
    allowed_statuses: list[TerminalRunStatus] = Field(
        default_factory=lambda: ["completed"]
    )
    require_all_steps_succeeded: bool = True
    require_complete_coverage: bool = True
    minimum_evidence_items: int = Field(0, ge=0, le=100_000)
    minimum_artifacts: int = Field(0, ge=0, le=10_000)
    required_answer_terms: list[str] = Field(default_factory=list)
    forbidden_answer_terms: list[str] = Field(default_factory=list)
    maximum_provider_calls: int | None = Field(None, ge=1, le=10_000)
    maximum_tool_calls: int | None = Field(None, ge=1, le=100_000)
    maximum_estimated_tokens: int | None = Field(
        None,
        ge=1,
        le=100_000_000,
    )
    maximum_estimated_cost_micros: int | None = Field(
        None,
        ge=1,
        le=10_000_000_000,
    )
    minimum_score: float = Field(0.85, ge=0.0, le=1.0)


class CreateAgentEvaluationCaseRequest(BaseModel):
    """Capture a terminal run as a versioned release-gate case."""

    model_config = ConfigDict(extra="forbid")

    source_run_id: str = Field(..., min_length=1, max_length=64)
    suite: str = Field("default", min_length=1, max_length=96)
    name: str = Field(..., min_length=1, max_length=160)
    description: str | None = Field(None, max_length=2_000)
    expectations: AgentEvaluationExpectations = Field(
        default_factory=AgentEvaluationExpectations
    )
    tags: list[str] = Field(default_factory=list, max_length=32)


class EvaluateAgentCaseRequest(BaseModel):
    """Choose the candidate run evaluated against a frozen case."""

    model_config = ConfigDict(extra="forbid")

    candidate_run_id: str = Field(..., min_length=1, max_length=64)


class AgentRunFeedbackRequest(BaseModel):
    """Explicit feedback owned by the current user and exact run."""

    model_config = ConfigDict(extra="forbid")

    rating: Literal[-1, 1]
    category: FeedbackCategory | None = None
    comment: str | None = Field(None, max_length=2_000)


__all__ = [
    "AgentEvaluationExpectations",
    "AgentRunFeedbackRequest",
    "CreateAgentEvaluationCaseRequest",
    "EvaluateAgentCaseRequest",
]
