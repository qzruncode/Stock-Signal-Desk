# -*- coding: utf-8 -*-
"""Shared stock-symbol normalization for registered tools."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def resolve_symbol(value: str) -> str:
    """Resolve a stock name/code into a normalized stock code when possible."""
    raw = (value or "").strip()
    if not raw:
        return raw
    try:
        from src.services.name_to_code_resolver import resolve_name_to_code

        return resolve_name_to_code(raw) or raw
    except Exception as exc:
        logger.debug("symbol resolve failed for %s: %s", raw, exc)
        return raw


def resolve_symbols_csv(value: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for part in (value or "").replace("，", ",").split(","):
        if not part.strip():
            continue
        symbol = resolve_symbol(part).strip()
        if symbol and symbol not in seen:
            seen.add(symbol)
            result.append(symbol)
    return result
