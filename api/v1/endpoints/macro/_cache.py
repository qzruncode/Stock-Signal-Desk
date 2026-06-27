# -*- coding: utf-8 -*-
"""Cache layer and background-refresh helper for the macro package.

Two consumption patterns are supported:

**Pattern A — cache-first with background refresh (lock + bg-refresh)**
  1. Read cache/DB → if hit and fresh, return immediately.
  2. If hit but stale, attempt non-blocking lock → on success spawn
     daemon thread to refresh → return cached (possibly stale) data.
  3. If miss, run synchronously, persist, return.

  Intended for endpoints backed by a DB table (index, bond-yield, indicator).

**Pattern B — pure daily cache (no lock, no bg-refresh)**
  1. Read cache → if hit, return immediately.
  2. If miss, run synchronously, persist to cache, return.

  Intended for aggregate data that is expensive to recompute on every
  request (sector-flow, market-breadth).
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Daily-cache helpers (Pattern B — _macro_cache_get / _macro_cache_put)
# ---------------------------------------------------------------------------

def _macro_cache_key(prefix: str) -> str:
    return f"macro:v2:{prefix}:{datetime.now().strftime('%Y%m%d')}"


def _macro_cache_get(prefix: str) -> dict | None:
    try:
        from src.storage import DatabaseManager
        raw = DatabaseManager.get_instance().get_kline_snapshot(_macro_cache_key(prefix))
        if raw:
            return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        logger.warning("[Macro-Cache] read error", exc_info=True)
    return None


def _macro_cache_put(prefix: str, data: dict) -> None:
    try:
        from src.storage import DatabaseManager
        DatabaseManager.get_instance().save_kline_snapshot(
            _macro_cache_key(prefix), json.dumps(data, ensure_ascii=False))
    except Exception:
        logger.warning("[Macro-Cache] write error", exc_info=True)


# ---------------------------------------------------------------------------
# Background-refresh helper (Pattern A)
# ---------------------------------------------------------------------------

def bg_refresh_if_stale(
    *,
    lock: threading.Lock,
    fetcher,
    on_success,
    log_tag: str = "[Macro]",
) -> None:
    """Attempt non-blocking lock and spawn a daemon background refresh.

    Parameters
    ----------
    lock:
        Module-level lock shared across requests to prevent redundant refreshes.
    fetcher:
        Zero-argument callable that returns fresh data (or None on failure).
    on_success:
        Single-argument callable receiving the fresh data from *fetcher*.
        Typically ``db.save_*(...)``.
    log_tag:
        Prefix for warning logs on failure.
    """
    if lock.acquire(blocking=False):
        def _bg():
            try:
                data = fetcher()
                if data is not None and data:
                    on_success(data)
            except Exception as e:
                logger.warning(f"{log_tag} bg-refresh failed: {e}")
            finally:
                lock.release()
        threading.Thread(target=_bg, daemon=True).start()