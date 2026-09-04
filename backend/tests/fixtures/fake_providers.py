"""Test-only MarketDataProvider implementations.

The mock providers in app/infrastructure/providers/mock_provider.py exist for
the application itself (demos, default config) and each varies exactly one
property from "everything is fine". These fakes exist only for tests that need
finer control than that -- an exact sequence of timestamps, or a failure
targeted at one specific symbol in a batch -- and have no reason to live in
application code.
"""

from __future__ import annotations

import asyncio

from app.domain.market.quote import Bar, Quote
from app.infrastructure.providers.base import MarketDataProvider
from app.infrastructure.providers.mock_provider import MockProvider


class SequenceProvider(MarketDataProvider):
    """Returns a fixed, ordered sequence of quotes for one symbol -- one per
    call -- so a test can control exactly what "the provider returned this,
    then later returned that" means, including returning an older timestamp
    after a newer one to simulate an out-of-order arrival.
    """

    source = "sequence"

    def __init__(self, quotes: list[Quote]) -> None:
        self._quotes = list(quotes)
        self._index = 0

    async def get_quote(self, symbol: str) -> Quote:
        if self._index >= len(self._quotes):
            raise IndexError(f"SequenceProvider exhausted after {len(self._quotes)} calls")
        quote = self._quotes[self._index]
        self._index += 1
        return quote

    async def get_daily_history(self, symbol: str, days: int) -> list[Bar]:
        return await MockProvider().get_daily_history(symbol, days)


class SelectiveFailureProvider(MarketDataProvider):
    """Behaves like MockProvider for every symbol except the ones named,
    which fail with the given exception -- for testing that one bad symbol in
    a batch does not affect the others.
    """

    def __init__(self, failing_symbols: set[str], *, exception_factory) -> None:
        self._failing_symbols = failing_symbols
        self._exception_factory = exception_factory
        self._mock = MockProvider()

    async def get_quote(self, symbol: str) -> Quote:
        if symbol in self._failing_symbols:
            raise self._exception_factory(symbol)
        return await self._mock.get_quote(symbol)

    async def get_daily_history(self, symbol: str, days: int) -> list[Bar]:
        if symbol in self._failing_symbols:
            raise self._exception_factory(symbol)
        return await self._mock.get_daily_history(symbol, days)


class HistoryFailsQuoteSucceedsProvider(MarketDataProvider):
    """The quote always succeeds; the daily-history call always fails.

    Exists to prove that a baseline-refresh failure cannot roll back a
    snapshot that was already committed -- see worker/ingestion.py's
    docstring on why those two writes are separate transactions.
    """

    def __init__(self, *, exception_factory) -> None:
        self._exception_factory = exception_factory
        self._mock = MockProvider()

    async def get_quote(self, symbol: str) -> Quote:
        return await self._mock.get_quote(symbol)

    async def get_daily_history(self, symbol: str, days: int) -> list[Bar]:
        raise self._exception_factory(symbol)


class CallCountingProvider(MarketDataProvider):
    """Counts get_quote calls per symbol -- for proving a cache hit really
    did skip the provider, not just that the result looked the same."""

    def __init__(self) -> None:
        self._mock = MockProvider()
        self.calls: dict[str, int] = {}

    async def get_quote(self, symbol: str) -> Quote:
        self.calls[symbol] = self.calls.get(symbol, 0) + 1
        return await self._mock.get_quote(symbol)

    async def get_daily_history(self, symbol: str, days: int) -> list[Bar]:
        return await self._mock.get_daily_history(symbol, days)


class ConcurrencyTrackingProvider(MarketDataProvider):
    """Records how many `get_quote` calls were ever in flight at once.

    Each call holds a short asyncio.sleep before returning, so overlapping
    callers genuinely overlap rather than finishing before the next one
    starts -- the same reasoning as test_concurrency.py's thread barrier,
    adapted to asyncio: without an artificial delay, a fast fake provider
    would let calls run to completion one after another even under
    ingest_all's semaphore, and the test would prove nothing about the bound.
    """

    def __init__(self, *, delay_seconds: float = 0.05) -> None:
        self._delay = delay_seconds
        self._mock = MockProvider()
        self._in_flight = 0
        self.max_concurrent = 0

    async def get_quote(self, symbol: str) -> Quote:
        self._in_flight += 1
        self.max_concurrent = max(self.max_concurrent, self._in_flight)
        try:
            await asyncio.sleep(self._delay)
            return await self._mock.get_quote(symbol)
        finally:
            self._in_flight -= 1

    async def get_daily_history(self, symbol: str, days: int) -> list[Bar]:
        return await self._mock.get_daily_history(symbol, days)
