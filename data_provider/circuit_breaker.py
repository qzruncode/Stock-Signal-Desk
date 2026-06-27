# -*- coding: utf-8 -*-
"""Per-source circuit breaker with thread-safe state."""

import threading
import time
from typing import Dict


class RealtimeCircuitBreaker:
    """Per-source circuit breaker for flaky realtime quote endpoints.

    Thread-safe: all state mutations are protected by a lock.
    """

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: int = 300):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._failures: Dict[str, int] = {}
        self._opened_until: Dict[str, float] = {}
        self._lock = threading.Lock()

    def is_available(self, source: str) -> bool:
        with self._lock:
            opened_until = self._opened_until.get(source, 0)
            return opened_until <= time.time()

    def record_success(self, source: str) -> None:
        with self._lock:
            self._failures.pop(source, None)
            self._opened_until.pop(source, None)

    def record_failure(self, source: str, error=None) -> None:
        with self._lock:
            failures = self._failures.get(source, 0) + 1
            self._failures[source] = failures
            if failures >= self.failure_threshold:
                self._opened_until[source] = time.time() + self.cooldown_seconds


_realtime_circuit_breaker = RealtimeCircuitBreaker()
_circuit_breaker_lock = threading.Lock()


def get_realtime_circuit_breaker() -> RealtimeCircuitBreaker:
    return _realtime_circuit_breaker


def reset_circuit_breaker() -> None:
    """Replace the module-level breaker with a fresh one (testing support)."""
    global _realtime_circuit_breaker
    with _circuit_breaker_lock:
        _realtime_circuit_breaker = RealtimeCircuitBreaker()