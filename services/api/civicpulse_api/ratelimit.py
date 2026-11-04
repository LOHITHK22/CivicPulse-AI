"""In-process, per-client token-bucket rate limiter.

Adequate for a single-instance portfolio deployment. A multi-instance deployment would need a
shared store; that is deliberately not added until it is needed.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict


class TokenBucketLimiter:
    def __init__(self, per_minute: int, *, burst: int | None = None, max_clients: int = 10_000) -> None:
        self.rate = per_minute / 60.0
        self.capacity = float(burst or per_minute)
        self.max_clients = max_clients
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self._lock = threading.Lock()

    def allow(self, client: str, now: float | None = None) -> tuple[bool, float]:
        """Returns (allowed, retry_after_seconds)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            tokens, last = self._buckets.get(client, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            if tokens >= 1:
                self._buckets[client] = (tokens - 1, now)
                self._buckets.move_to_end(client)
                allowed, retry = True, 0.0
            else:
                self._buckets[client] = (tokens, now)
                allowed, retry = False, (1 - tokens) / self.rate
            while len(self._buckets) > self.max_clients:
                self._buckets.popitem(last=False)
            return allowed, retry
