"""Provider selection."""

from __future__ import annotations

from typing import get_args

import pytest

from app.config import ProviderName, Settings
from app.infrastructure.providers.factory import _REGISTRY, get_provider
from app.infrastructure.providers.mock_provider import MockProvider
from app.infrastructure.providers.yfinance_provider import YFinanceProvider


def _settings(**overrides) -> Settings:
    base = {"_env_file": None, "environment": "test", "jwt_secret": "x"}
    return Settings(**{**base, **overrides})


def test_registry_covers_every_declared_provider_name():
    """A regression guard for a specific mistake: adding a value to the
    ProviderName Literal in config.py without wiring it into the factory would
    otherwise fail only when someone actually selects it, possibly in
    production."""
    assert set(_REGISTRY) == set(get_args(ProviderName))


def test_default_comes_from_settings():
    settings = _settings(market_provider="mock")
    assert isinstance(get_provider(settings=settings), MockProvider)


def test_explicit_name_overrides_settings():
    settings = _settings(market_provider="mock")
    assert isinstance(get_provider("failing", settings=settings), type(get_provider("failing")))


@pytest.mark.parametrize("name", get_args(ProviderName))
def test_every_registered_name_builds_something(name: ProviderName):
    provider = get_provider(name, settings=_settings())
    assert provider is not None


def test_yfinance_receives_settings():
    """The real provider needs configured timeout/retry values; the mocks do
    not take any -- this is the one branch in get_provider worth a directed
    test rather than relying on the loop above."""
    settings = _settings(provider_timeout_seconds=3.0)
    provider = get_provider("yfinance", settings=settings)
    assert isinstance(provider, YFinanceProvider)
    assert provider._settings.provider_timeout_seconds == 3.0
