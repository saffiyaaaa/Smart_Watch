"""Provider selection.

The worker and the API never import a concrete provider class directly --
they ask this module for "the configured provider" and get back something
behind the MarketDataProvider interface. Swapping yfinance for another real
provider later, or running the whole system against a mock for a demo, is a
one-line config change rather than a code change.
"""

from __future__ import annotations

from app.config import ProviderName, Settings, get_settings
from app.infrastructure.providers.base import MarketDataProvider
from app.infrastructure.providers.mock_provider import (
    ConflictingProvider,
    FailingProvider,
    MalformedProvider,
    MockProvider,
    StaleProvider,
    TimeoutProvider,
)
from app.infrastructure.providers.yfinance_provider import YFinanceProvider

_REGISTRY: dict[ProviderName, type[MarketDataProvider]] = {
    "yfinance": YFinanceProvider,
    "mock": MockProvider,
    "failing": FailingProvider,
    "timeout": TimeoutProvider,
    "stale": StaleProvider,
    "malformed": MalformedProvider,
    "conflicting": ConflictingProvider,
}


def get_provider(
    name: ProviderName | None = None, *, settings: Settings | None = None
) -> MarketDataProvider:
    """Build the configured provider.

    `name` overrides settings.market_provider explicitly -- used by tests, and
    by anything that legitimately needs two providers at once, such as a
    conflict-detection scenario pairing "mock" with "conflicting". Normal
    operation (the worker) calls this with no argument and gets whatever
    MARKET_PROVIDER is configured.
    """
    settings = settings or get_settings()
    selected = name or settings.market_provider
    provider_cls = _REGISTRY[selected]
    return provider_cls(settings) if provider_cls is YFinanceProvider else provider_cls()
