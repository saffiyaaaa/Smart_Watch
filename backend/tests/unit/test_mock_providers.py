"""The deterministic provider family."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from app.config import get_settings
from app.infrastructure.providers.exceptions import (
    InvalidProviderData,
    ProviderTimeout,
    ProviderUnavailable,
    SymbolNotFound,
)
from app.infrastructure.providers.mock_provider import (
    UNKNOWN_SYMBOLS,
    ConflictingProvider,
    FailingProvider,
    MalformedProvider,
    MockProvider,
    StaleProvider,
    TimeoutProvider,
)


class TestMockProvider:
    async def test_returns_a_quote(self):
        q = await MockProvider().get_quote("AAPL")
        assert q.symbol == "AAPL"
        assert q.source == "mock"
        assert q.price > 0

    async def test_deterministic_across_calls(self):
        """Same symbol -> same price, whether called once or a hundred times,
        in this process or -- since it derives from the symbol alone, with no
        hidden state -- any other."""
        prices = {(await MockProvider().get_quote("AAPL")).price for _ in range(10)}
        assert len(prices) == 1

    async def test_deterministic_across_instances(self):
        a = await MockProvider().get_quote("MSFT")
        b = await MockProvider().get_quote("MSFT")
        assert a.price == b.price
        assert a.volume == b.volume

    async def test_different_symbols_get_different_prices(self):
        a = await MockProvider().get_quote("AAPL")
        b = await MockProvider().get_quote("ZZZZ")
        assert a.price != b.price

    async def test_arbitrary_symbols_still_work(self):
        """Not just the hardcoded familiar tickers -- anything valid resolves,
        so a test is never blocked on adding a symbol to a lookup table."""
        q = await MockProvider().get_quote("XYZABC")
        assert q.price > 0

    async def test_quote_is_fresh(self):
        settings = get_settings()
        q = await MockProvider().get_quote("AAPL")
        age = (datetime.now(UTC) - q.market_timestamp).total_seconds()
        assert age < settings.freshness_fresh_seconds

    async def test_reserved_symbols_are_not_found(self):
        with pytest.raises(SymbolNotFound):
            await MockProvider().get_quote("NOTFOUND")

    async def test_daily_history_length_matches_request(self):
        bars = await MockProvider().get_daily_history("AAPL", days=20)
        assert len(bars) == 20

    async def test_daily_history_is_ordered_most_recent_last_to_first(self):
        bars = await MockProvider().get_daily_history("AAPL", days=5)
        dates = [b.session_date for b in bars]
        assert dates == sorted(dates, reverse=True)

    async def test_daily_history_excludes_today(self):
        bars = await MockProvider().get_daily_history("AAPL", days=5)
        today = datetime.now(UTC).date()
        assert all(b.session_date < today for b in bars)

    async def test_daily_history_reserved_symbol_not_found(self):
        with pytest.raises(SymbolNotFound):
            await MockProvider().get_daily_history("DELISTED", days=5)


class TestFailingProvider:
    async def test_get_quote_raises_provider_unavailable(self):
        with pytest.raises(ProviderUnavailable):
            await FailingProvider().get_quote("AAPL")

    async def test_get_daily_history_raises_provider_unavailable(self):
        with pytest.raises(ProviderUnavailable):
            await FailingProvider().get_daily_history("AAPL", days=5)


class TestTimeoutProvider:
    async def test_raises_immediately_without_real_delay(self):
        """Confirms the mock does not itself sleep -- the real timeout
        machinery is proven separately in test_retry.py."""
        start = time.monotonic()
        with pytest.raises(ProviderTimeout):
            await TimeoutProvider().get_quote("AAPL")
        assert time.monotonic() - start < 0.05


class TestStaleProvider:
    async def test_quote_is_classified_stale_under_default_thresholds(self):
        settings = get_settings()
        q = await StaleProvider().get_quote("AAPL")
        age = (datetime.now(UTC) - q.market_timestamp).total_seconds()
        assert age > settings.freshness_stale_seconds

    async def test_history_still_works(self):
        """Only the quote is stale; history baselines must remain usable."""
        bars = await StaleProvider().get_daily_history("AAPL", days=10)
        assert len(bars) == 10


class TestMalformedProvider:
    async def test_raises_invalid_provider_data_not_a_raw_pydantic_error(self):
        """The failure surfaces as the provider-layer exception the rest of
        the system knows how to handle, not pydantic's internal type leaking
        through the interface boundary."""
        with pytest.raises(InvalidProviderData):
            await MalformedProvider().get_quote("AAPL")

    async def test_history_also_raises_invalid_provider_data(self):
        with pytest.raises(InvalidProviderData):
            await MalformedProvider().get_daily_history("AAPL", days=5)


class TestConflictingProvider:
    async def test_disagrees_with_mock_beyond_the_conflict_tolerance(self):
        settings = get_settings()
        mock_quote = await MockProvider().get_quote("AAPL")
        conflicting_quote = await ConflictingProvider().get_quote("AAPL")

        diff_pct = abs(conflicting_quote.price - mock_quote.price) / mock_quote.price * 100
        assert diff_pct > settings.conflict_price_tolerance_pct

    async def test_same_symbol_same_instant_different_source(self):
        mock_quote = await MockProvider().get_quote("AAPL")
        conflicting_quote = await ConflictingProvider().get_quote("AAPL")

        assert conflicting_quote.symbol == mock_quote.symbol
        assert conflicting_quote.source != mock_quote.source
        # Both "now": close enough to represent the same observed instant for
        # a conflict check, without requiring literally identical timestamps.
        gap = (conflicting_quote.market_timestamp - mock_quote.market_timestamp).total_seconds()
        assert abs(gap) < 1


def test_unknown_symbols_set_is_not_accidentally_empty():
    """A regression guard: an empty set here would silently disable the
    failure-matrix row-15 test path in TestMockProvider above."""
    assert len(UNKNOWN_SYMBOLS) > 0
