"""Shared mainland-index catalog for Agent intents and data adapters."""

from __future__ import annotations

from typing import Final


A_SHARE_INDEX_MAP: Final[dict[str, tuple[str, str]]] = {
    "000001": ("上证指数", "sh000001"),
    "000300": ("沪深300", "sh000300"),
    "399001": ("深证成指", "sz399001"),
    "399006": ("创业板指", "sz399006"),
    "000688": ("科创50", "sh000688"),
}


__all__ = ["A_SHARE_INDEX_MAP"]
