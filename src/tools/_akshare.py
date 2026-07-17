# -*- coding: utf-8 -*-
"""Small, resilient AKShare adapter shared by professional stock tools."""

from __future__ import annotations

import math
import pickle
import threading
import time
from datetime import date, datetime
from typing import Any, Callable

import pandas as pd

from data_provider.rate_limiter import akshare_rate_limiter

_CACHE_LOCK = threading.RLock()


def _persistent_cache_get(key: str, ttl_seconds: int) -> Any | None:
    if ttl_seconds <= 0:
        return None
    try:
        from src.storage import DatabaseManager

        cached = DatabaseManager.get_instance().get_tool_cache(f"akshare:{key}")
        if not cached:
            return None
        updated_at = cached.get("updated_at")
        if updated_at is None or time.time() - updated_at.timestamp() >= ttl_seconds:
            return None
        return pickle.loads(cached["payload"])
    except Exception:
        return None


def _persistent_cache_put(key: str, value: Any) -> None:
    try:
        from src.storage import DatabaseManager

        DatabaseManager.get_instance().save_tool_cache(
            f"akshare:{key}",
            pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL),
        )
    except Exception:
        pass


def bare_symbol(symbol: str) -> str:
    from src.tools.symbols import resolve_symbol

    code = resolve_symbol(symbol).strip()
    lower = code.lower()
    if lower.startswith(("sh", "sz", "bj")):
        code = code[2:]
    if "." in code:
        code = code.split(".", 1)[0]
    return code.zfill(6) if code.isdigit() and len(code) < 6 else code


def exchange_prefix(symbol: str, *, upper: bool = True, suffix: bool = False) -> str:
    code = bare_symbol(symbol)
    from data_provider.utils import is_bse_code

    if is_bse_code(code):
        market = "BJ"
    elif code.startswith(("6", "5", "90")):
        market = "SH"
    else:
        market = "SZ"
    if suffix:
        return f"{code}.{market}"
    prefix = market if upper else market.lower()
    return f"{prefix}{code}"


def json_value(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def frame_records(frame: pd.DataFrame | None) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    return [
        {str(key): json_value(value) for key, value in row.items()}
        for row in frame.to_dict(orient="records")
    ]


def cached_call(
    key: str,
    fn: Callable[[], Any],
    *,
    ttl_seconds: int = 1800,
    attempts: int = 2,
) -> tuple[Any, bool]:
    """Run a rate-limited AKShare call with short TTL cache and one retry."""
    with _CACHE_LOCK:
        cached = _persistent_cache_get(key, ttl_seconds)
        if cached is not None:
            return cached, True

    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            akshare_rate_limiter.wait(min_interval=0.4, max_jitter=1.0)
            value = fn()
            with _CACHE_LOCK:
                _persistent_cache_put(key, value)
            return value, False
        except Exception as exc:  # upstream APIs fail in many transport-specific ways
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.6 * (attempt + 1))
    assert last_error is not None
    raise last_error


def source_meta(
    *,
    cached: bool,
    source: str = "AKShare",
    available: bool = True,
    fallback_used: bool = False,
) -> dict[str, Any]:
    return {
        "source": source,
        "data_time": datetime.now().astimezone().isoformat(),
        "success": available,
        "is_stale": not available,
        "fallback_used": fallback_used,
        "_cached": cached,
    }
