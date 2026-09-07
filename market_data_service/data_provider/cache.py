# -*- coding: utf-8 -*-
"""Thread-safe module-level caches for realtime quote data."""

import threading
import time
from datetime import datetime, time as dtime
from typing import Any, Optional


def is_cn_market_open(now: Optional[datetime] = None) -> bool:
    """是否处于 A 股交易时段（工作日 9:30-11:30 / 13:00-15:00）。

    仅按本地时间粗判，不查交易所日历，用于决定实时行情缓存 TTL。
    """
    now = now or datetime.now()
    if now.weekday() >= 5:  # 周六日
        return False
    t = now.time()
    return (dtime(9, 30) <= t <= dtime(11, 30)) or (dtime(13, 0) <= t <= dtime(15, 0))


class _TtlCache:
    """A simple thread-safe timed cache for a single DataFrame payload.

    支持动态 TTL：传入 ``ttl_for`` 回调时，每次 ``get`` 按当前时刻重新求 TTL，
    用于实时行情在盘中/盘后采用不同新鲜度。
    """

    def __init__(self, ttl: int, ttl_for=None):
        self._data: Any = None
        self._timestamp: float = 0
        self._ttl = ttl
        self._ttl_for = ttl_for
        self._lock = threading.Lock()

    @property
    def ttl(self) -> int:
        if self._ttl_for is not None:
            try:
                return int(self._ttl_for())
            except Exception:
                return self._ttl
        return self._ttl

    def get(self) -> Optional[Any]:
        with self._lock:
            if self._data is None:
                return None
            ttl = self.ttl
            if time.time() - self._timestamp < ttl:
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

# 实时行情缓存：盘中 45 秒（保证新鲜），非盘 20 分钟（节省请求）
realtime_cache = _TtlCache(1200, ttl_for=lambda: 45 if is_cn_market_open() else 1200)
