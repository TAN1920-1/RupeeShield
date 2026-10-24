"""In-process rate limiting.

Why: the API can start simulations and stream events. A stuck retry loop in a
browser tab should not be able to saturate a laptop. This is a sliding-window
limiter keyed by client host, which is enough for a single-process teaching app.

Limitation (documented on purpose): the counters live in this process's memory, so
the limit is per worker and resets on restart. A multi-worker or multi-host
deployment would need a shared store such as Redis; the project does not ship one
because it would add infrastructure for no benefit locally.
"""
from __future__ import annotations

import threading
import time
from collections import deque

from app.core.config import settings


class SlidingWindowLimiter:
    """Allow at most ``limit`` requests per ``window_seconds`` per key."""

    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = max(int(limit), 1)
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, float]:
        """Register a hit. Returns ``(allowed, retry_after_seconds)``."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            bucket = self._hits.setdefault(key, deque())
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                retry_after = max(self.window_seconds - (now - bucket[0]), 0.0)
                return False, round(retry_after, 2)
            bucket.append(now)
        return True, 0.0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


rate_limiter = SlidingWindowLimiter(settings.rate_limit_requests_per_minute)
