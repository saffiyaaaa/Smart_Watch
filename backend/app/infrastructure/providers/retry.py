"""Timeout and bounded, backed-off retry for a blocking provider call.

This is where the failure-matrix promise "the API never waits indefinitely on
a provider" actually gets kept. Every provider method that touches a network
routes through here exactly once.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable

from app.infrastructure.providers.exceptions import (
    InvalidProviderData,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimitedError,
    SymbolNotFound,
)

logger = logging.getLogger("smw.provider")

# Never retried: the same malformed response or unknown symbol comes back
# unchanged, so retrying only delays the correct answer by three attempts.
_NOT_RETRYABLE = (InvalidProviderData, SymbolNotFound)


async def call_with_retry[T](
    fn: Callable[[], T],
    *,
    timeout_seconds: float,
    max_retries: int,
    backoff_base_seconds: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    jitter: Callable[[], float] = random.random,
) -> T:
    """Run a synchronous, blocking provider call under a timeout with retry.

    `fn` runs via asyncio.to_thread so a slow or hanging network call cannot
    block the event loop the worker shares with everything else.

    Retried: a timeout, ProviderUnavailable, and its subclass
    RateLimitedError. Not retried: InvalidProviderData, SymbolNotFound --
    listed in _NOT_RETRYABLE above and re-raised on the first occurrence.

    A RateLimitedError carrying `retry_after` is honoured by waiting exactly
    that long instead of the exponential schedule, because the provider told
    us precisely how long to wait and guessing shorter just gets rate limited
    again.

    `sleep` and `jitter` are injectable so tests can assert on backoff
    behaviour -- attempt counts, delay growth, retry_after handling --
    without any test actually waiting for it.

    A limitation worth being explicit about: asyncio.wait_for only stops
    *waiting*. yfinance offers no cancellation hook, so a call that has
    genuinely hung keeps running in its worker thread for however long the
    underlying socket takes to give up -- possibly longer than
    timeout_seconds, occupying a thread-pool slot the whole time. The
    guarantee this function actually provides is the one the failure matrix
    asks for: the *caller* (the worker, the API) is never blocked past
    timeout_seconds waiting on the result. Reclaiming the thread promptly is a
    separate problem that a library without native cancellation cannot fully
    solve from the outside.
    """
    last_error: ProviderTimeout | ProviderUnavailable | None = None

    for attempt in range(1, max_retries + 1):
        try:
            return await asyncio.wait_for(asyncio.to_thread(fn), timeout=timeout_seconds)
        except _NOT_RETRYABLE:
            raise
        except TimeoutError as exc:
            last_error = ProviderTimeout(
                f"timed out after {timeout_seconds}s (attempt {attempt}/{max_retries})"
            )
            last_error.__cause__ = exc
        except RateLimitedError as exc:
            last_error = exc
            if exc.retry_after is not None and attempt < max_retries:
                logger.warning("rate limited, honouring retry_after=%.1fs", exc.retry_after)
                await sleep(exc.retry_after)
                continue
        except ProviderUnavailable as exc:
            last_error = exc

        if attempt < max_retries:
            delay = backoff_base_seconds * (2 ** (attempt - 1)) + jitter() * backoff_base_seconds
            logger.warning(
                "provider call failed (attempt %d/%d): %s -- retrying in %.2fs",
                attempt,
                max_retries,
                last_error,
                delay,
            )
            await sleep(delay)

    # max_retries is validated >= 1 (see Settings), so the loop above always
    # runs at least once and last_error is always set by the time it exits.
    assert last_error is not None
    raise last_error
