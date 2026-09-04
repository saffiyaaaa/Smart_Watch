"""Tests against the real Yahoo Finance API.

Skipped by default (see tests/conftest.py's network-marker handling). Run with:

    RUN_NETWORK_TESTS=1 pytest tests/integration/test_yfinance_provider.py

This is the Phase 4 gate item "a real provider response converts to the
internal model", proven against the live service rather than a mock of it.
"""

from __future__ import annotations

import pytest

from app.infrastructure.providers.exceptions import SymbolNotFound
from app.infrastructure.providers.yfinance_provider import YFinanceProvider

pytestmark = pytest.mark.network


class TestRealQuote:
    async def test_fetches_a_valid_quote(self):
        quote = await YFinanceProvider().get_quote("AAPL")

        assert quote.source == "yfinance"
        assert quote.symbol == "AAPL"
        assert quote.price > 0
        assert quote.market_timestamp.tzinfo is not None

    async def test_unknown_symbol_is_symbol_not_found(self):
        """Verified empirically: yfinance returns an empty DataFrame for an
        unknown ticker rather than raising, which is exactly the condition
        this maps to SymbolNotFound."""
        with pytest.raises(SymbolNotFound):
            await YFinanceProvider().get_quote("ZZZZZZINVALID")


class TestRealDailyHistory:
    async def test_fetches_recent_sessions(self):
        bars = await YFinanceProvider().get_daily_history("AAPL", days=5)

        assert len(bars) >= 3  # weekends/holidays may reduce the count
        assert all(b.close > 0 for b in bars)
        assert all(b.symbol == "AAPL" for b in bars)

    async def test_unknown_symbol_history_is_symbol_not_found(self):
        with pytest.raises(SymbolNotFound):
            await YFinanceProvider().get_daily_history("ZZZZZZINVALID", days=5)
