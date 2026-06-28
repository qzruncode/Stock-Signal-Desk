# -*- coding: utf-8 -*-
"""Shared internal types for system config service mixins."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class _LLMDiagnostic:
    """Internal structured diagnosis for LLM test and discovery failures."""

    error_code: str
    retryable: bool
    message: str
    reason: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


def _normalize_agent_model(model: str, configured_models: Optional[set] = None) -> str:
    """Normalize a model name, adding openai/ prefix if needed."""
    normalized = (model or "").strip()
    if not normalized:
        return ""
    if "/" not in normalized:
        if configured_models and normalized in configured_models:
            return normalized
        return f"openai/{normalized}"
    return normalized
