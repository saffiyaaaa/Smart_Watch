"""Deterministic, network-free providers.

MockProvider is the "everything is fine" baseline used for local development,
demos, and the default test configuration. Each of the others deviates from it
in exactly one property, so a specific failure mode from
docs/product-spec.md's failure matrix can be injected without touching any
business logic: the worker and the change-detection pipeline cannot tell a
mock failure from a real one, because both surface as the same exception from
app.infrastructure.providers.exceptions.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from pydantic import ValidationError as PydanticValidationError

from app.domain.market.quote import Bar, Quote
from app.infrastructure.providers.base import MarketDataProvider
from app.infrastructure.providers.exceptions import (
    InvalidProviderData,
    ProviderTimeout,
    ProviderUnavailable,
    SymbolNotFound,
)

# A few familiar symbols get a recognisable price; anything else falls back to
# a value derived from the symbol itself, so a test can use any ticker without
# maintaining a lookup table for it.
_MOCK_BASE_PRICES = {
    "AAPL": Decimal("180.00"),
    "MSFT": Decimal("400.00"),
    "NVDA": Decimal("120.00"),
    "TSLA": Decimal("250.00"),
}

# Reserved symbols that deterministically behave like an unknown or delisted
# ticker, so failure-matrix row 15 has a test path that needs no extra
# provider class.
UNKNOWN_SYMBOLS = frozenset({"NOTFOUND", "DELISTED"})


def _deterministic_price(symbol: str) -> Decimal:
    """A stable, symbol-derived price: the same symbol always produces the
    same mock quote, in this process or any other."""
    if symbol in _MOCK_BASE_PRICES:
        return _MOCK_BASE_PRICES[symbol]
    digest = hashlib.sha256(symbol.encode()).hexdigest()
    cents = int(digest[:6], 16) % 50_000
    return Decimal(cents) / Decimal(100) + Decimal("10.00")


def _deterministic_volume(symbol: str) -> int:
    digest = hashlib.sha256(symbol.encode()).hexdigest()
    return 1_000_000 + int(digest[6:10], 16)


class MockProvider(MarketDataProvider):
    """Everything succeeds. market_timestamp is always "now", so every quote
    is FRESH under the default thresholds."""

    source = "mock"

    async def get_quote(self, symbol: str) -> Quote:
        if symbol in UNKNOWN_SYMBOLS:
            raise SymbolNotFound(f"no data for {symbol!r}")
        now = datetime.now(UTC)
        return Quote(
            source=self.source,
            symbol=symbol,
            price=_deterministic_price(symbol),
            volume=_deterministic_volume(symbol),
            market_timestamp=now,
            fetched_at=now,
        )

    async def get_daily_history(self, symbol: str, days: int) -> list[Bar]:
        if symbol in UNKNOWN_SYMBOLS:
            raise SymbolNotFound(f"no history for {symbol!r}")

        base = _deterministic_price(symbol)
        today = datetime.now(UTC).date()
        bars = []
        for i in range(1, days + 1):
            # A small deterministic wobble so the series is not perfectly
            # flat. tests/fixtures/seed.py covers the flat case separately
            # for scoring tests that want an exact average; this path is for
            # exercising the baseline calculation against varied input.
            wobble = Decimal((i * 37) % 21 - 10) / Decimal(1000)
            bars.append(
                Bar(
                    source=self.source,
                    symbol=symbol,
                    session_date=today - timedelta(days=i),
                    close=base * (Decimal("1.00") + wobble),
                    volume=_deterministic_volume(symbol),
                )
            )
        return bars


class FailingProvider(MarketDataProvider):
    """Always unavailable, as if the provider were down or returning 5xx for
    every request. Exercises retry and last-known-good fallback behaviour
    without a real outage."""

    source = "failing"

    async def get_quote(self, symbol: str) -> Quote:
        raise ProviderUnavailable(f"mock failure for {symbol}")

    async def get_daily_history(self, symbol: str, days: int) -> list[Bar]:
        raise ProviderUnavailable(f"mock failure for {symbol}")


class TimeoutProvider(MarketDataProvider):
    """Always times out.

    Raises ProviderTimeout directly rather than actually sleeping past a
    timeout: anything that needs "the provider timed out" as a precondition
    should stay fast, not pay for a real timeout window. The timeout-and-retry
    machinery itself is tested directly in tests/unit/test_retry.py against an
    artificially slow function.
    """

    source = "timeout"

    async def get_quote(self, symbol: str) -> Quote:
        raise ProviderTimeout(f"mock timeout for {symbol}")

    async def get_daily_history(self, symbol: str, days: int) -> list[Bar]:
        raise ProviderTimeout(f"mock timeout for {symbol}")


class StaleProvider(MarketDataProvider):
    """Every quote is old enough to classify as STALE under the default
    freshness thresholds (docs/product-spec.md section 2), so ingestion and
    scoring's stale-data handling can be tested without waiting for real data
    to age."""

    source = "stale"
    STALE_AGE = timedelta(hours=2)

    async def get_quote(self, symbol: str) -> Quote:
        stale_time = datetime.now(UTC) - self.STALE_AGE
        return Quote(
            source=self.source,
            symbol=symbol,
            price=_deterministic_price(symbol),
            volume=_deterministic_volume(symbol),
            market_timestamp=stale_time,
            fetched_at=datetime.now(UTC),
        )

    async def get_daily_history(self, symbol: str, days: int) -> list[Bar]:
        return await MockProvider().get_daily_history(symbol, days)


class MalformedProvider(MarketDataProvider):
    """Returns data that fails Quote's own validation.

    This exercises the real path a broken provider response takes --
    construction raising pydantic.ValidationError, this class translating that
    into InvalidProviderData -- rather than a test merely asserting that path
    exists.
    """

    source = "malformed"

    async def get_quote(self, symbol: str) -> Quote:
        try:
            return Quote(
                source=self.source,
                symbol=symbol,
                price=Decimal("-1.00"),  # invalid: trips Quote's own check
                volume=_deterministic_volume(symbol),
                market_timestamp=datetime.now(UTC),
                fetched_at=datetime.now(UTC),
            )
        except PydanticValidationError as exc:
            raise InvalidProviderData(f"malformed response for {symbol}") from exc

    async def get_daily_history(self, symbol: str, days: int) -> list[Bar]:
        raise InvalidProviderData(f"malformed history for {symbol}")


class ConflictingProvider(MarketDataProvider):
    """A second, disagreeing source.

    Not meant to run alone -- it exists to be paired with MockProvider in a
    test that fetches from both. Same symbol, same instant, a price 2% away
    from MockProvider's, comfortably past docs/product-spec.md's 0.5%
    conflict tolerance. That divergence is what "conflicting data" means in
    this system.
    """

    source = "conflicting"
    _DISAGREEMENT_FACTOR = Decimal("1.02")

    async def get_quote(self, symbol: str) -> Quote:
        now = datetime.now(UTC)
        return Quote(
            source=self.source,
            symbol=symbol,
            price=_deterministic_price(symbol) * self._DISAGREEMENT_FACTOR,
            volume=_deterministic_volume(symbol),
            market_timestamp=now,
            fetched_at=now,
        )

    async def get_daily_history(self, symbol: str, days: int) -> list[Bar]:
        return await MockProvider().get_daily_history(symbol, days)
