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
        """保证相邻请求间隔落在 [min_interval, max_jitter] 区间。

        - 已距上次请求 ``>= max_jitter`` 时：不额外等待（仅记录时刻）。
        - 否则：补足到 ``[min_interval, max_jitter]`` 内的一个随机点。
        这样既保留随机抖动防封，又不会在每次请求上叠加多层 sleep。
        """
        interval = self._min_interval if min_interval is None else min_interval
        jitter_max = self._max_jitter if max_jitter is None else max_jitter
        upper = max(interval, jitter_max)
        with self._lock:
            now = time.time()
            if self._last_request_time is not None:
                elapsed = now - self._last_request_time
                if elapsed < upper:
                    # 目标间隔：[interval, upper] 之间的随机点
                    target = random.uniform(interval, upper) if jitter else interval
                    if elapsed < target:
                        time.sleep(target - elapsed)
            self._last_request_time = time.time()


akshare_rate_limiter = GlobalRateLimiter()
