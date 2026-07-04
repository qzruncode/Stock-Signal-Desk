# -*- coding: utf-8 -*-
"""Process-wide rate limiter for Akshare calls."""

from __future__ import annotations

import random
import threading
import time


class GlobalRateLimiter:
    """Thread-safe minimum-interval gate shared by all fetcher instances."""

    def __init__(self, min_interval: float = 2.0, max_jitter: float = 5.0):
        self._min_interval = min_interval
        self._max_jitter = max_jitter
        self._last_request_time: float | None = None
        self._lock = threading.Lock()

    def wait(
        self,
        *,
        min_interval: float | None = None,
        max_jitter: float | None = None,
        jitter: bool = True,
    ) -> None:
        interval = self._min_interval if min_interval is None else min_interval
        jitter_max = self._max_jitter if max_jitter is None else max_jitter
        with self._lock:
            now = time.time()
            if self._last_request_time is not None:
                elapsed = now - self._last_request_time
                if elapsed < interval:
                    time.sleep(interval - elapsed)
                if jitter:
                    upper = max(interval, jitter_max)
                    time.sleep(random.uniform(interval, upper))
            self._last_request_time = time.time()


akshare_rate_limiter = GlobalRateLimiter()
