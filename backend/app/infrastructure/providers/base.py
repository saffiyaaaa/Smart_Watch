"""The provider interface.

Two methods, both async so the worker's event loop is never blocked by
network I/O. Every implementation -- yfinance or any of the deterministic
mocks -- must raise only the exceptions in
app.infrastructure.providers.exceptions; nothing else about a provider (its
HTTP client, its SDK, its rate-limit headers) may leak past this boundary.
That is what lets the worker, and every test of the worker, treat a real
provider and a mock identically.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.market.quote import Bar, Quote


class MarketDataProvider(ABC):
    @abstractmethod
    async def get_quote(self, symbol: str) -> Quote:
        """The latest known price for a symbol."""

    @abstractmethod
    async def get_daily_history(self, symbol: str, days: int) -> list[Bar]:
        """Up to `days` completed trading sessions, most recent last."""
