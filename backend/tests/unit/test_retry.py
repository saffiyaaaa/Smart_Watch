"""call_with_retry: timeout and backoff behaviour.

Every test injects `sleep` and `jitter` so nothing here waits in real time --
including the timeout tests, which use a genuinely slow function but a tiny
configured timeout, so a "timeout after 3 retries" test still finishes in
milliseconds.
"""

from __future__ import annotations

import asyncio

import pytest

from app.infrastructure.providers.exceptions import (
    InvalidProviderData,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimitedError,
    SymbolNotFound,
)
from app.infrastructure.providers.retry import call_with_retry


def _recording_sleep():
    """A sleep stand-in that records every requested duration instead of
    waiting, so backoff *timing* can be asserted without spending it."""
    delays: list[float] = []

    async def sleep(seconds: float) -> None:
        delays.append(seconds)

    return sleep, delays


class Counter:
    """A tiny call counter, so a fake fn can behave differently attempt to
    attempt (fail twice, then succeed; always fail; etc.)."""

    def __init__(self) -> None:
        self.calls = 0


class TestSuccessPaths:
    async def test_succeeds_on_first_attempt_with_no_delay(self):
        sleep, delays = _recording_sleep()
        result = await call_with_retry(
            lambda: "ok",
            timeout_seconds=1,
            max_retries=3,
            backoff_base_seconds=0.1,
            sleep=sleep,
        )
        assert result == "ok"
        assert delays == []

    async def test_succeeds_after_transient_failures(self):
        counter = Counter()

        def flaky():
            counter.calls += 1
            if counter.calls < 3:
                raise ProviderUnavailable("temporary")
            return "recovered"

        sleep, delays = _recording_sleep()
        result = await call_with_retry(
            flaky, timeout_seconds=1, max_retries=5, backoff_base_seconds=0.1, sleep=sleep
        )
        assert result == "recovered"
        assert counter.calls == 3
        assert len(delays) == 2  # slept between attempt 1->2 and 2->3, not after success


class TestRetryableExhaustion:
    async def test_provider_unavailable_retried_up_to_the_limit(self):
        counter = Counter()

        def always_fails():
            counter.calls += 1
            raise ProviderUnavailable("still down")

        sleep, _ = _recording_sleep()
        with pytest.raises(ProviderUnavailable, match="still down"):
            await call_with_retry(
                always_fails,
                timeout_seconds=1,
                max_retries=3,
                backoff_base_seconds=0.1,
                sleep=sleep,
            )
        assert counter.calls == 3

    async def test_last_error_is_the_one_raised(self):
        counter = Counter()

        def fails_with_changing_messages():
            counter.calls += 1
            raise ProviderUnavailable(f"failure #{counter.calls}")

        sleep, _ = _recording_sleep()
        with pytest.raises(ProviderUnavailable, match="failure #3"):
            await call_with_retry(
                fails_with_changing_messages,
                timeout_seconds=1,
                max_retries=3,
                backoff_base_seconds=0.1,
                sleep=sleep,
            )


class TestBackoffSchedule:
    async def test_delay_grows_exponentially(self):
        """base * 2**(attempt-1), with jitter fixed at 0 for a clean
        assertion: attempts 1,2,3,4 wait base*1, base*2, base*4, base*8."""
        counter = Counter()

        def always_fails():
            counter.calls += 1
            raise ProviderUnavailable("down")

        sleep, delays = _recording_sleep()
        with pytest.raises(ProviderUnavailable):
            await call_with_retry(
                always_fails,
                timeout_seconds=1,
                max_retries=5,
                backoff_base_seconds=0.1,
                sleep=sleep,
                jitter=lambda: 0.0,
            )
        assert delays == pytest.approx([0.1, 0.2, 0.4, 0.8])

    async def test_jitter_is_added_on_top_of_the_base_delay(self):
        sleep, delays = _recording_sleep()
        with pytest.raises(ProviderUnavailable):
            await call_with_retry(
                lambda: (_ for _ in ()).throw(ProviderUnavailable("down")),
                timeout_seconds=1,
                max_retries=2,
                backoff_base_seconds=1.0,
                sleep=sleep,
                jitter=lambda: 0.5,
            )
        # base(1.0) * 2**0 + jitter(0.5) * base(1.0) == 1.5
        assert delays == pytest.approx([1.5])

    async def test_single_retry_never_sleeps(self):
        sleep, delays = _recording_sleep()
        with pytest.raises(ProviderUnavailable):
            await call_with_retry(
                lambda: (_ for _ in ()).throw(ProviderUnavailable("down")),
                timeout_seconds=1,
                max_retries=1,
                backoff_base_seconds=1.0,
                sleep=sleep,
            )
        assert delays == []


class TestNonRetryableFailFast:
    @pytest.mark.parametrize("exc_cls", [InvalidProviderData, SymbolNotFound])
    async def test_not_retried_and_raised_immediately(self, exc_cls):
        counter = Counter()

        def fails_permanently():
            counter.calls += 1
            raise exc_cls("permanent")

        sleep, delays = _recording_sleep()
        with pytest.raises(exc_cls):
            await call_with_retry(
                fails_permanently,
                timeout_seconds=1,
                max_retries=5,
                backoff_base_seconds=0.1,
                sleep=sleep,
            )
        # Exactly one attempt: retrying a validation failure only delays the
        # correct answer, it never changes it.
        assert counter.calls == 1
        assert delays == []


class TestTimeout:
    async def test_timeout_becomes_provider_timeout(self):
        """A genuinely slow function against a tiny timeout. Real time cost
        is bounded by timeout_seconds x max_retries, kept small on purpose."""

        def hangs():
            import time

            time.sleep(0.3)
            return "never"

        sleep, _ = _recording_sleep()
        with pytest.raises(ProviderTimeout, match=r"timed out after 0\.05s"):
            await call_with_retry(
                hangs, timeout_seconds=0.05, max_retries=2, backoff_base_seconds=0.01, sleep=sleep
            )

    async def test_timeout_is_retried(self):
        counter = Counter()

        def sometimes_hangs():
            import time

            counter.calls += 1
            if counter.calls < 2:
                time.sleep(0.3)
            return "recovered"

        sleep, _ = _recording_sleep()
        result = await call_with_retry(
            sometimes_hangs,
            timeout_seconds=0.05,
            max_retries=3,
            backoff_base_seconds=0.01,
            sleep=sleep,
        )
        assert result == "recovered"
        assert counter.calls == 2

    async def test_timeout_preserves_the_original_exception_as_cause(self):
        def hangs():
            import time

            time.sleep(0.3)

        sleep, _ = _recording_sleep()
        with pytest.raises(ProviderTimeout) as exc_info:
            await call_with_retry(
                hangs, timeout_seconds=0.05, max_retries=1, backoff_base_seconds=0.01, sleep=sleep
            )
        assert isinstance(exc_info.value.__cause__, TimeoutError)


class TestRateLimiting:
    async def test_retry_after_is_honoured_instead_of_exponential_backoff(self):
        counter = Counter()

        def rate_limited_once():
            counter.calls += 1
            if counter.calls == 1:
                raise RateLimitedError("slow down", retry_after=7.5)
            return "ok"

        sleep, delays = _recording_sleep()
        result = await call_with_retry(
            rate_limited_once,
            timeout_seconds=1,
            max_retries=3,
            backoff_base_seconds=0.1,
            sleep=sleep,
        )
        assert result == "ok"
        # 7.5s from the server, not 0.1s from the default schedule.
        assert delays == [7.5]

    async def test_rate_limit_without_retry_after_uses_default_backoff(self):
        counter = Counter()

        def rate_limited_once():
            counter.calls += 1
            if counter.calls == 1:
                raise RateLimitedError("slow down")
            return "ok"

        sleep, delays = _recording_sleep()
        result = await call_with_retry(
            rate_limited_once,
            timeout_seconds=1,
            max_retries=3,
            backoff_base_seconds=0.2,
            sleep=sleep,
            jitter=lambda: 0.0,
        )
        assert result == "ok"
        assert delays == pytest.approx([0.2])

    async def test_rate_limit_exhausts_retries_like_any_other_failure(self):
        sleep, _ = _recording_sleep()
        with pytest.raises(RateLimitedError):
            await call_with_retry(
                lambda: (_ for _ in ()).throw(RateLimitedError("slow down", retry_after=100)),
                timeout_seconds=1,
                max_retries=2,
                backoff_base_seconds=0.1,
                sleep=sleep,
            )


class TestConcurrencyDoesNotBlockTheEventLoop:
    async def test_a_hanging_call_does_not_block_other_tasks(self):
        """The whole point of asyncio.to_thread: a slow provider call must not
        freeze the event loop that everything else in the worker shares."""
        other_task_ran = asyncio.Event()

        async def other_work():
            await asyncio.sleep(0)
            other_task_ran.set()

        def hangs():
            import time

            time.sleep(0.2)
            return "done"

        task = asyncio.create_task(other_work())
        result = await call_with_retry(
            hangs, timeout_seconds=1, max_retries=1, backoff_base_seconds=0.01
        )
        await task

        assert result == "done"
        assert other_task_ran.is_set()
