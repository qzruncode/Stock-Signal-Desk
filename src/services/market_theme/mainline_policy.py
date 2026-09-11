"""Shared lifecycle enums for market-mainline reports."""

from __future__ import annotations

from enum import Enum


class MainlineLifecycle(str, Enum):
    """Normalized lifecycle of a market narrative."""

    EMERGING = "emerging"
    VALIDATING = "validating"
    CONFIRMED = "confirmed"
    EXPANDING = "expanding"
    FADING = "fading"


class MainlineTriggerProgress(str, Enum):
    """Progress of candidate-mainline activation conditions."""

    MET = "met"
    PARTIAL = "partial"
    UNMET = "unmet"
    UNKNOWN = "unknown"


__all__ = ["MainlineLifecycle", "MainlineTriggerProgress"]
