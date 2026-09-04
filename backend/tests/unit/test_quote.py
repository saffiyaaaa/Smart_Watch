"""Quote and Bar validation -- pure, no network, no database."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.market.quote import MAX_FUTURE_SKEW, Bar, Quote

NOW = datetime(2026, 3, 11, 15, 30, tzinfo=UTC)


def _quote(**overrides) -> Quote:
    base = {
        "source": "mock",
        "symbol": "AAPL",
        "price": Decimal("180.00"),
        "volume": 1_000_000,
        "market_timestamp": NOW,
        "fetched_at": NOW,
    }
    return Quote(**{**base, **overrides})


class TestQuoteAcceptsValidInput:
    def test_typical_quote(self):
        q = _quote()
        assert q.symbol == "AAPL"
        assert q.price == Decimal("180.00")

    def test_zero_volume_is_valid(self):
        """A real, legitimate value: no trades yet this session (observed at
        market open against the real yfinance API)."""
        assert _quote(volume=0).volume == 0

    def test_none_volume_is_valid(self):
        """Distinct from zero: the provider did not report volume at all."""
        assert _quote(volume=None).volume is None

    def test_is_frozen(self):
        q = _quote()
        with pytest.raises(ValidationError):
            q.price = Decimal("999.00")  # type: ignore[misc]

    def test_market_timestamp_within_skew_allowance_is_accepted(self):
        nearly_now = datetime.now(UTC) + MAX_FUTURE_SKEW - timedelta(seconds=1)
        _quote(market_timestamp=nearly_now, fetched_at=nearly_now)


class TestQuoteRejectsInvalidInput:
    def test_zero_price_rejected(self):
        with pytest.raises(ValidationError, match="price"):
            _quote(price=Decimal("0"))

    def test_negative_price_rejected(self):
        with pytest.raises(ValidationError, match="price"):
            _quote(price=Decimal("-5.00"))

    def test_nan_price_rejected(self):
        with pytest.raises(ValidationError, match="finite"):
            _quote(price=Decimal("NaN"))

    def test_infinite_price_rejected(self):
        with pytest.raises(ValidationError, match="finite"):
            _quote(price=Decimal("Infinity"))

    def test_negative_volume_rejected(self):
        with pytest.raises(ValidationError, match="volume"):
            _quote(volume=-1)

    def test_invalid_symbol_rejected(self):
        with pytest.raises(ValidationError):
            _quote(symbol="not valid!")

    def test_blank_source_rejected(self):
        with pytest.raises(ValidationError, match="source"):
            _quote(source="   ")

    def test_naive_market_timestamp_rejected(self):
        with pytest.raises(ValidationError, match="timezone-aware"):
            _quote(market_timestamp=datetime(2026, 3, 11, 15, 30))

    def test_naive_fetched_at_rejected(self):
        with pytest.raises(ValidationError, match="timezone-aware"):
            _quote(fetched_at=datetime(2026, 3, 11, 15, 30))

    def test_market_timestamp_from_the_future_rejected(self):
        """A provider claiming a price held at a moment that has not happened
        yet is either broken or malicious; neither should be trusted."""
        future = datetime.now(UTC) + timedelta(hours=1)
        with pytest.raises(ValidationError, match="future"):
            _quote(market_timestamp=future)

    def test_market_timestamp_beyond_skew_boundary_rejected(self):
        just_over = datetime.now(UTC) + MAX_FUTURE_SKEW + timedelta(seconds=1)
        with pytest.raises(ValidationError, match="future"):
            _quote(market_timestamp=just_over)


class TestBar:
    def _bar(self, **overrides) -> Bar:
        base = {
            "source": "mock",
            "symbol": "AAPL",
            "session_date": date(2026, 3, 10),
            "close": Decimal("175.00"),
            "volume": 40_000_000,
        }
        return Bar(**{**base, **overrides})

    def test_typical_bar(self):
        assert self._bar().close == Decimal("175.00")

    def test_zero_close_rejected(self):
        with pytest.raises(ValidationError, match="close"):
            self._bar(close=Decimal("0"))

    def test_negative_volume_rejected(self):
        with pytest.raises(ValidationError, match="volume"):
            self._bar(volume=-1)

    def test_none_volume_is_valid(self):
        assert self._bar(volume=None).volume is None

    def test_invalid_symbol_rejected(self):
        with pytest.raises(ValidationError):
            self._bar(symbol="")
