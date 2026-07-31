# -*- coding: utf-8 -*-
"""Built-in Agent capability governance API contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentCapabilityGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["allow", "deny"]
    reason: str | None = Field(None, max_length=2_000)


class CreateCapabilityReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    release_version: str = Field(..., min_length=1, max_length=96)
    evaluation_suite: str = Field(..., min_length=1, max_length=96)
    minimum_pass_rate: float = Field(1.0, ge=0.0, le=1.0)


class AgentUserMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["global", "conversation"] = "global"
    conversation_id: str | None = Field(None, max_length=64)
    kind: Literal["preference", "instruction", "profile"] = (
        "preference"
    )
    memory_key: str = Field(..., min_length=1, max_length=96)
    content: str = Field(..., min_length=1, max_length=4_000)
    enabled: bool = True


__all__ = [
    "AgentCapabilityGrantRequest",
    "AgentUserMemoryRequest",
    "CreateCapabilityReleaseRequest",
]
