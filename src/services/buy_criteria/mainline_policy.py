"""Typed market-mainline strategy and classification contracts.

The market report describes where an industry direction sits in the mainline
lifecycle.  The buy workflow separately declares how much lifecycle
confirmation the user requires.  Keeping those two concepts separate prevents
an emerging direction from being treated as either an already-confirmed
mainline or an unrelated industry.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


class MainlineStrategyProfile(str, Enum):
    """User-visible investment timing preference for the first Boolean gate."""

    CONFIRMED_MAINLINE = "confirmed_mainline"
    EARLY_POSITIONING = "early_positioning"


class MainlineLifecycle(str, Enum):
    """Normalized lifecycle shared by market reports and gate outcomes."""

    EMERGING = "emerging"
    VALIDATING = "validating"
    CONFIRMED = "confirmed"
    EXPANDING = "expanding"
    FADING = "fading"


class MainlineDirectionRelation(str, Enum):
    """How the requested direction relates to a structured market narrative."""

    CORE = "core"
    ACTIVE_BRANCH = "active_branch"
    EMERGING_BRANCH = "emerging_branch"
    LONG_TERM_ONLY = "long_term_only"
    UNRELATED = "unrelated"


class MainlineTriggerProgress(str, Enum):
    """Aggregate progress of the candidate-mainline activation conditions."""

    MET = "met"
    PARTIAL = "partial"
    UNMET = "unmet"
    UNKNOWN = "unknown"


class MainlineGateClassification(BaseModel):
    """Auditable semantic classification returned with the first gate."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
    )

    strategy_profile: MainlineStrategyProfile
    direction_relation: MainlineDirectionRelation
    lifecycle: MainlineLifecycle | None = None
    matched_mainline: str | None = None
    matched_branch: str | None = None
    trigger_progress: MainlineTriggerProgress = MainlineTriggerProgress.UNKNOWN


def normalize_mainline_strategy(
    value: MainlineStrategyProfile | str | None,
) -> MainlineStrategyProfile:
    """Apply the program-owned default without semantic keyword routing."""

    if value is None or not str(value).strip():
        return MainlineStrategyProfile.CONFIRMED_MAINLINE
    return MainlineStrategyProfile(value)


def mainline_strategy_label(
    value: MainlineStrategyProfile | str,
) -> str:
    profile = normalize_mainline_strategy(value)
    return "前瞻布局型" if profile == MainlineStrategyProfile.EARLY_POSITIONING else "确认型主线"


__all__ = [
    "MainlineDirectionRelation",
    "MainlineGateClassification",
    "MainlineLifecycle",
    "MainlineStrategyProfile",
    "MainlineTriggerProgress",
    "mainline_strategy_label",
    "normalize_mainline_strategy",
]
