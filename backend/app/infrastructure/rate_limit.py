"""A minimal in-memory rate limiter (Phase 13).

Fixed-window, keyed by caller. In-memory and therefore per-process --
consistent with this project's worker (see worker/scheduler.py's own
docstring on why it is a single asyncio process, not Celery/Redis): nothing
here assumes a single shared API process, but nothing in this system's stated
scope requires multiple API processes sharing one rate-limit budget either.
The day this system actually runs more than one API instance, the natural
upgrade is a Redis INCR+EXPIRE window -- Redis is already provisioned for the
Phase 12 quote cache -- but that is solving a problem this system does not
have yet. Per this project's own principle, a technology needs a concrete
reason to exist.

Route handlers in this codebase run as plain `def`s, which FastAPI executes
in a worker thread pool, so concurrent requests genuinely race across
threads, not just across asyncio tasks -- `check` is guarded with a real
threading.Lock, not an asyncio one.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock


class RateLimitExceededError(Exception):
    def __init__(self, retry_after_seconds: float) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"rate limit exceeded, retry after {retry_after_seconds:.1f}s")


@dataclass
class _Window:
    count: int = 0
    started_at: float = field(default_factory=time.monotonic)


class InMemoryRateLimiter:
    """Allows at most `limit` calls to `check(key)` per `window_seconds`,
    per distinct key.

    A fixed window, not a sliding one: simpler, and the boundary imprecision
    it accepts (a burst of up to 2x `limit` spanning a window edge) is a
    reasonable trade for an abuse-prevention limiter that need not be exact,
    only "sensible" -- see docs/product-spec.md Phase 13.
    """

    def __init__(self, *, limit: int, window_seconds: float) -> None:
        self._limit = limit
        self._window_seconds = window_seconds
        self._windows: dict[str, _Window] = defaultdict(_Window)
        self._lock = Lock()

    def check(self, key: str) -> None:
        """Raises RateLimitExceededError if `key` is over budget for the current
        window; otherwise records the call and returns."""
        now = time.monotonic()
        with self._lock:
            window = self._windows[key]
            elapsed = now - window.started_at
            if elapsed >= self._window_seconds:
                window.count = 0
                window.started_at = now
                elapsed = 0.0

            window.count += 1
            if window.count > self._limit:
                raise RateLimitExceededError(self._window_seconds - elapsed)

    def reset(self) -> None:
        """Test-only: clear all state without constructing a new instance."""
        with self._lock:
            self._windows.clear()
