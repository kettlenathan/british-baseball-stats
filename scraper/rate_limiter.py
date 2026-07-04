"""Simple fixed-delay-with-jitter throttle applied before every real network
fetch (cache hits bypass this entirely — see cache.py)."""

import random
import time

from config import REQUEST_DELAY_JITTER_SECONDS, REQUEST_DELAY_SECONDS


class RateLimiter:
    def __init__(self, delay: float = REQUEST_DELAY_SECONDS, jitter: float = REQUEST_DELAY_JITTER_SECONDS):
        self.delay = delay
        self.jitter = jitter
        self._last_request_at: float | None = None

    def wait(self) -> None:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            target = self.delay + random.uniform(0, self.jitter)
            remaining = target - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_at = time.monotonic()
