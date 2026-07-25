# -*- coding: utf-8 -*-
"""Cache and provider-text normalization for market evidence."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "market_theme_analysis:v4"


def cache_key(layer: str) -> str:
    return f"{_CACHE_PREFIX}:{layer}:{datetime.now().strftime('%Y%m%d%H')}"


def cache_get(layer: str) -> Optional[dict]:
    try:
        from src.storage import DatabaseManager

        raw = DatabaseManager.get_instance().get_kline_snapshot(cache_key(layer))
        if raw:
            return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        logger.debug("market theme cache read failed", exc_info=True)
    return None


def cache_put(layer: str, data: dict) -> None:
    try:
        from src.storage import DatabaseManager

        DatabaseManager.get_instance().save_kline_snapshot(cache_key(layer), json.dumps(data, ensure_ascii=False))
    except Exception:
        logger.debug("market theme cache write failed", exc_info=True)


def safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def shorten(text: str, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def normalize_texts(items: Iterable[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        if text:
            result.append(text)
    return result
