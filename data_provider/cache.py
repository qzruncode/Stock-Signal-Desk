# -*- coding: utf-8 -*-
"""Thread-safe module-level caches for realtime quote data."""

import threading
import time
from typing import Any, Dict, Optional


class _TtlCache:
    """A simple thread-safe timed cache for a single DataFrame payload."""

    def __init__(self, ttl: int):
        self._data: Any = None
        self._timestamp: float = 0
        self._ttl = ttl
        self._lock = threading.Lock()

    @property
    def ttl(self) -> int:
        return self._ttl

    def get(self) -> Optional[Any]:
        with self._lock:
            if self._data is not None and time.time() - self._timestamp < self._ttl:
                return self._data
            return None

    def set(self, data: Any) -> None:
        with self._lock:
            self._data = data
            self._timestamp = time.time()

    def age(self) -> int:
        with self._lock:
            return int(time.time() - self._timestamp) if self._data else -1

    def invalidate(self) -> None:
        with self._lock:
            self._data = None
            self._timestamp = 0


# === Module-level cache instances ===

realtime_cache = _TtlCache(1200)       # A股实时行情缓存，20 分钟
etf_realtime_cache = _TtlCache(1200)   # ETF 实时行情缓存，20 分钟