"""QuoteCache against a real Redis instance.

The fake-client tests in tests/unit/test_quote_cache.py cover the cache's
logic; what a fake client cannot prove is that redis.asyncio actually
round-trips a Quote and actually expires it, which is what this file checks
against the redis service in docker-compose.yml. Skipped when Redis is not
reachable, same as postgres_required for PostgreSQL.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.config import Settings
from app.domain.market.quote import Quote
from app.infrastructure.cache import QuoteCache
from tests.conftest import redis_required

pytestmark = [pytest.mark.redis, redis_required]


def _settings(**overrides) -> Settings:
    base = {"_env_file": None, "environment": "test", "jwt_secret": "x", "cache_enabled": True}
    return Settings(**{**base, **overrides})


def _quote(symbol: str) -> Quote:
    now = datetime.now(UTC)
    return Quote(
        source="mock",
        symbol=symbol,
        price=Decimal("180.00"),
        volume=1_000_000,
        market_timestamp=now,
        fetched_at=now,
    )


async def test_set_then_get_round_trips_through_real_redis():
    cache = QuoteCache(_settings())
    quote = _quote("REDISTEST1")
    try:
        await cache.set(quote.symbol, quote)
        cached = await cache.get(quote.symbol)
        assert cached == quote
    finally:
        await cache.close()


async def test_a_symbol_never_set_is_a_miss():
    cache = QuoteCache(_settings())
    try:
        assert await cache.get("NEVERCACHED") is None
    finally:
        await cache.close()


async def test_entry_expires_after_its_ttl():
    cache = QuoteCache(_settings(cache_quote_ttl_seconds=1))
    quote = _quote("REDISTEST2")
    try:
        await cache.set(quote.symbol, quote)
        assert await cache.get(quote.symbol) is not None

        await asyncio.sleep(1.5)

        assert await cache.get(quote.symbol) is None
    finally:
        await cache.close()
