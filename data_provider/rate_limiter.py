# -*- coding: utf-8 -*-
"""Cross-process rate limiter for AKShare calls."""

from __future__ import annotations

import random
from pathlib import Path
import tempfile
import threading
import time

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback keeps thread safety.
    fcntl = None


class GlobalRateLimiter:
    """Minimum-interval gate shared by threads and isolated worker processes."""

    def __init__(self, min_interval: float = 2.0, max_jitter: float = 5.0):
        self._min_interval = min_interval
        self._max_jitter = max_jitter
        self._last_request_time: float | None = None
        self._lock = threading.Lock()
        self._state_path = Path(tempfile.gettempdir()) / "daily-stock-analysis-akshare-rate.state"

    def _wait_shared(self, interval: float, upper: float, jitter: bool) -> None:
        """Reserve a request slot across the parent and isolated worker processes."""
        if fcntl is None:
            now = time.time()
            if self._last_request_time is not None:
                elapsed = now - self._last_request_time
                if elapsed < upper:
                    target = random.uniform(interval, upper) if jitter else interval
                    if elapsed < target:
                        time.sleep(target - elapsed)
            self._last_request_time = time.time()
            return

        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        with self._state_path.open("a+", encoding="utf-8") as state:
            fcntl.flock(state.fileno(), fcntl.LOCK_EX)
            try:
                state.seek(0)
                raw = state.read().strip()
                try:
                    last_request = float(raw) if raw else None
                except ValueError:
                    last_request = None
                now = time.time()
                if last_request is not None:
                    elapsed = now - last_request
                    if elapsed < upper:
                        target = random.uniform(interval, upper) if jitter else interval
                        if elapsed < target:
                            time.sleep(target - elapsed)
                reserved_at = time.time()
                state.seek(0)
                state.truncate()
                state.write(str(reserved_at))
                state.flush()
                self._last_request_time = reserved_at
            finally:
                fcntl.flock(state.fileno(), fcntl.LOCK_UN)

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
            self._wait_shared(interval, upper, jitter)


akshare_rate_limiter = GlobalRateLimiter()
