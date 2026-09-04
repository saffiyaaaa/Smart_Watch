"""InMemoryRateLimiter: the fixed-window counter behind /auth/register and
/auth/login (Phase 13)."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.infrastructure.rate_limit import InMemoryRateLimiter, RateLimitExceededError


class TestWithinBudget:
    def test_calls_up_to_the_limit_all_succeed(self):
        limiter = InMemoryRateLimiter(limit=3, window_seconds=60)
        for _ in range(3):
            limiter.check("1.2.3.4")  # must not raise

    def test_the_call_over_the_limit_raises(self):
        limiter = InMemoryRateLimiter(limit=3, window_seconds=60)
        for _ in range(3):
            limiter.check("1.2.3.4")
        try:
            limiter.check("1.2.3.4")
            raised = False
        except RateLimitExceededError:
            raised = True
        assert raised

    def test_retry_after_is_positive_and_within_the_window(self):
        limiter = InMemoryRateLimiter(limit=1, window_seconds=10)
        limiter.check("1.2.3.4")
        try:
            limiter.check("1.2.3.4")
            raised = None
        except RateLimitExceededError as exc:
            raised = exc
        assert raised is not None
        assert 0 < raised.retry_after_seconds <= 10


class TestKeysAreIndependent:
    def test_one_key_exceeding_its_budget_does_not_affect_another(self):
        limiter = InMemoryRateLimiter(limit=1, window_seconds=60)
        limiter.check("1.2.3.4")
        limiter.check("5.6.7.8")  # must not raise -- a different key


class TestWindowResets:
    def test_a_new_window_resets_the_count(self):
        limiter = InMemoryRateLimiter(limit=1, window_seconds=0.05)
        limiter.check("1.2.3.4")
        time.sleep(0.1)
        limiter.check("1.2.3.4")  # must not raise -- the window rolled over


class TestThreadSafety:
    """FastAPI runs sync route handlers -- and this dependency -- in a
    worker thread pool, so concurrent requests genuinely race across threads,
    not just async tasks. Without the lock, two threads reading-then-writing
    `window.count` could both see room for one more call and both succeed,
    letting through more than `limit` -- the exact bug a rate limiter exists
    to prevent."""

    def test_concurrent_callers_never_exceed_the_limit(self):
        limiter = InMemoryRateLimiter(limit=5, window_seconds=60)
        threads_n = 20
        barrier = threading.Barrier(threads_n)
        allowed = 0
        lock = threading.Lock()

        def call():
            nonlocal allowed
            barrier.wait(timeout=5)
            try:
                limiter.check("shared-key")
            except RateLimitExceededError:
                return
            with lock:
                allowed += 1

        with ThreadPoolExecutor(max_workers=threads_n) as pool:
            futures = [pool.submit(call) for _ in range(threads_n)]
            for f in as_completed(futures):
                f.result()

        assert allowed == 5
