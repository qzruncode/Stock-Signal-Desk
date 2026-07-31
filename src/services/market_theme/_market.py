# -*- coding: utf-8 -*-
"""Presentation helpers for model-produced market-mainline conclusions.

Business semantics are intentionally absent.  This module never discovers a
theme, assigns a lifecycle stage or scores a narrative.  It only renders a
validated structured conclusion or exposes raw observations when semantic
synthesis is unavailable.
"""

from __future__ import annotations

from typing import Any


def build_trade_action(theme: dict[str, Any]) -> str:
    """Return the model's explicit action implication without reclassifying it."""
    return str(theme.get("action") or theme.get("focus") or theme.get("judgement") or "").strip()


def build_deep_summary(
    market_stage: dict[str, Any],
    lifecycle_notes: list[dict[str, Any]],
    future_outlook: list[dict[str, Any]],
) -> str:
    """Render already-structured semantic output; never infer missing content."""
    stage = str(market_stage.get("label") or "未研判").strip()
    current = "、".join(
        str(item.get("theme") or "").strip() for item in lifecycle_notes if str(item.get("theme") or "").strip()
    )
    future = "、".join(
        str(item.get("name") or "").strip() for item in future_outlook if str(item.get("name") or "").strip()
    )
    if not current and not future:
        return "本轮只有原始市场证据，语义主线研判尚未完成。"
    parts = [f"市场阶段：{stage}。"]
    if current:
        parts.append(f"当前主线：{current}。")
    if future:
        parts.append(f"候选方向：{future}。")
    return "".join(parts)


def validated_themes(report: dict[str, Any]) -> list[dict[str, Any]]:
    values = report.get("current_mainlines")
    return [item for item in values or [] if isinstance(item, dict)]


def validated_future_themes(report: dict[str, Any]) -> list[dict[str, Any]]:
    values = report.get("candidate_mainlines") or report.get("future_mainlines")
    return [item for item in values or [] if isinstance(item, dict)]


def empty_market_stage() -> dict[str, str]:
    return {
        "label": "未研判",
        "description": "原始市场证据已收集，但动态语义研判尚未完成。",
    }
