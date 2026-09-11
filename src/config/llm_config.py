# -*- coding: utf-8 -*-
"""LLM configuration helpers for the Anthropic gateway."""

import os


def resolve_unified_llm_temperature(model: str) -> float:
    """Resolve the unified LLM temperature from ``LLM_TEMPERATURE``."""
    raw_value = os.getenv("LLM_TEMPERATURE")
    if raw_value and raw_value.strip():
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            pass
    return 0.7
