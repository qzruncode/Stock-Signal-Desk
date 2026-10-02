"""Independent Goal-mode contracts.

Goal is a product mode of its own.  These contracts deliberately do not
inherit the Planning or Team contracts; they describe only the outcome,
evidence, and bounded action loop owned by GoalGraph.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


GoalCriterionStatus = Literal["satisfied", "pending", "failed", "unverifiable"]
GoalVerificationMethod = Literal[
    "tool_result",
    "runtime_check",
    "user_confirmation",
    "model_assessment",
    "unknown",
]
GoalActionKind = Literal["tool", "ask_user", "finish"]
GoalAssessmentStatus = Literal[
    "continue",
    "replan",
    "waiting_for_user",
    "completed",
    "blocked",
    "failed",
]


class GoalCriterion(BaseModel):
    """One required or optional condition in the user-owned goal."""

    model_config = ConfigDict(extra="forbid")

    criterion_id: str = Field(min_length=1, max_length=96)
    description: str = Field(min_length=1, max_length=1_200)
    required: bool = True
    verification_method: GoalVerificationMethod = "unknown"
    status: GoalCriterionStatus = "pending"
    evidence_ids: list[str] = Field(default_factory=list, max_length=24)
    explanation: str = Field(default="", max_length=1_000)


class GoalContract(BaseModel):
    """Canonical, versioned contract extracted before Goal execution."""

    model_config = ConfigDict(extra="forbid")

    progress_text: str = Field(
        default="", max_length=1_200,
        description="直接对用户说的开场：用一两句说明理解的目标和关注点。保持陈述语气；需要用户回答的问题单独填写 clarification_question。不要在工具观察前断言数据是否可获取或是否最新。",
    )
    clarification_question: str = Field(
        default="", max_length=1_200,
        description="只有缺失信息会实质阻碍安全或有用地推进目标时，才填写一个需要用户回答的具体问题；非阻塞偏好应采用合理默认值继续。",
    )
    schema_version: Literal["goal.v1"] = "goal.v1"
    objective: str = Field(min_length=1, max_length=2_000)
    scope: str = Field(default="", max_length=1_200)
    constraints: list[str] = Field(default_factory=list, max_length=12)
    metrics: list[str] = Field(default_factory=list, max_length=12)
    # Empty criteria are accepted at intake so Goal can pause at the explicit
    # confirmation gate and ask the user to supply a completion definition.
    success_criteria: list[GoalCriterion] = Field(default_factory=list, max_length=8)
    escalation_policy: str = Field(default="目标无法验证时请求用户确认", max_length=800)
    revision: int = Field(default=1, ge=1, le=32)


class GoalAction(BaseModel):
    """One bounded action selected by GoalGraph."""

    model_config = ConfigDict(extra="forbid")

    progress_text: str = Field(
        default="", max_length=1_200,
        description="只有本轮有新发现或新动作时，才用自然语言简短说明；不写确认套话或重复目标。需要用户决定时直接提出问题。动作尚未执行，不得把预期当作结果。",
    )
    kind: GoalActionKind
    action_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(default="", max_length=128)
    arguments: dict[str, object] = Field(default_factory=dict)
    criterion_ids: list[str] = Field(default_factory=list, max_length=8)
    rationale: str = Field(default="", max_length=1_000)


class GoalCriterionAssessment(BaseModel):
    """Model proposal for one criterion; the server applies the evidence gate."""

    model_config = ConfigDict(extra="forbid")

    criterion_id: str = Field(min_length=1, max_length=96)
    status: GoalCriterionStatus
    evidence_ids: list[str] = Field(default_factory=list, max_length=24)
    explanation: str = Field(default="", max_length=1_000)


class GoalAssessment(BaseModel):
    """Structured monitor decision for the next GoalGraph transition."""

    model_config = ConfigDict(extra="forbid")

    status: GoalAssessmentStatus
    progress_text: str = Field(
        default="", max_length=1_200,
        description="面向用户的一两句进展：这次实际发现了什么、对目标有何影响、还缺什么；只讲本轮新信息，不列内部状态。",
    )
    criteria: list[GoalCriterionAssessment] = Field(default_factory=list, max_length=8)
    blocker: str = Field(default="", max_length=1_200)
    next_action: str = Field(default="", max_length=800)


class GoalFinalAnswer(BaseModel):
    """User-facing result contract for the independent Goal run."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1)
    evidence_ids: list[str] = Field(
        default_factory=list,
        max_length=80,
        description="支撑最终答案事实结论的 evidence_id，只能从本轮 eligible=true 的证据中选择。",
    )
    limitations: list[str] = Field(default_factory=list, max_length=12)


__all__ = [
    "GoalAction",
    "GoalActionKind",
    "GoalAssessment",
    "GoalAssessmentStatus",
    "GoalContract",
    "GoalCriterion",
    "GoalCriterionAssessment",
    "GoalCriterionStatus",
    "GoalFinalAnswer",
    "GoalVerificationMethod",
]
