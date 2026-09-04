"""QuoteCache: the Phase 12 quote cache.

An in-memory fake client stands in for redis.asyncio.Redis so the cache's
logic -- key format, TTL, fail-open behaviour, the disabled default -- is
verified without a live Redis server. A real round trip against Redis is a
separate, infrastructure-gated concern; nothing about it changes this logic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.config import Settings
from app.domain.market.quote import Quote
from app.infrastructure.cache import QuoteCache


def _settings(**overrides) -> Settings:
    base = {"_env_file": None, "environment": "test", "jwt_secret": "x"}
    return Settings(**{**base, **overrides})


def _quote(symbol: str = "AAPL") -> Quote:
    now = datetime.now(UTC)
    return Quote(
        source="mock",
        symbol=symbol,
        price=Decimal("180.00"),
        volume=1_000_000,
        market_timestamp=now,
        fetched_at=now,
    )


class FakeRedis:
    """The minimal async surface QuoteCache uses, backed by a plain dict.
    TTL is recorded but not enforced -- expiry isn't the cache's own logic to
    verify, it's Redis's, and a live-Redis test covers that separately."""

    def __init__(self, *, raise_on: str | None = None) -> None:
        self._store: dict[str, str] = {}
        self._raise_on = raise_on

    async def get(self, name: str) -> str | None:
        if self._raise_on == "get":
            raise ConnectionError("redis unreachable")
        return self._store.get(name)

    async def set(self, name: str, value: str, ex: int | None = None) -> None:
        if self._raise_on == "set":
            raise ConnectionError("redis unreachable")
        self._store[name] = value

    async def aclose(self) -> None:
        pass


class TestDisabledByDefault:
    async def test_get_is_always_a_miss_when_disabled(self):
        cache = QuoteCache(_settings(cache_enabled=False), client=FakeRedis())
        await cache.set("AAPL", _quote())
        assert await cache.get("AAPL") is None

    async def test_set_writes_nothing_when_disabled(self):
        client = FakeRedis()
        cache = QuoteCache(_settings(cache_enabled=False), client=client)
        await cache.set("AAPL", _quote())
        assert client._store == {}

    async def test_no_client_is_constructed_when_disabled(self):
        """Without an injected client, a disabled cache must not even try to
        reach settings.redis_url -- Redis being unreachable must never be
        able to affect a system that has caching turned off."""
        cache = QuoteCache(_settings(cache_enabled=False))
        assert await cache.get("AAPL") is None


class TestEnabledRoundTrip:
    async def test_set_then_get_returns_the_same_quote(self):
        client = FakeRedis()
        cache = QuoteCache(_settings(cache_enabled=True), client=client)
        quote = _quote("MSFT")

        await cache.set("MSFT", quote)
        cached = await cache.get("MSFT")

        assert cached == quote

    async def test_key_is_case_insensitive(self):
        client = FakeRedis()
        cache = QuoteCache(_settings(cache_enabled=True), client=client)
        await cache.set("aapl", _quote("AAPL"))
        assert await cache.get("AAPL") is not None

    async def test_miss_for_an_unset_symbol(self):
        cache = QuoteCache(_settings(cache_enabled=True), client=FakeRedis())
        assert await cache.get("NEVERSET") is None

    async def test_ttl_is_passed_through_to_the_client(self):
        captured: dict[str, object] = {}

        class RecordingRedis(FakeRedis):
            async def set(self, name: str, value: str, ex: int | None = None) -> None:
                captured["ex"] = ex
                await super().set(name, value, ex=ex)

        cache = QuoteCache(
            _settings(cache_enabled=True, cache_quote_ttl_seconds=45), client=RecordingRedis()
        )
        await cache.set("AAPL", _quote())
        assert captured["ex"] == 45


class TestFailsOpen:
    """A cache is an optimisation; it must never become a new way for
    ingestion to fail."""

    async def test_a_read_error_is_a_miss_not_an_exception(self):
        cache = QuoteCache(_settings(cache_enabled=True), client=FakeRedis(raise_on="get"))
        assert await cache.get("AAPL") is None

    async def test_a_write_error_does_not_raise(self):
        cache = QuoteCache(_settings(cache_enabled=True), client=FakeRedis(raise_on="set"))
        await cache.set("AAPL", _quote())  # must not raise

    async def test_an_unparsable_cached_value_is_a_miss(self):
        client = FakeRedis()
        client._store["quote:AAPL"] = "not valid json"
        cache = QuoteCache(_settings(cache_enabled=True), client=client)
        assert await cache.get("AAPL") is None


class TestClose:
    async def test_closing_an_owned_client_is_a_noop_when_disabled(self):
        cache = QuoteCache(_settings(cache_enabled=False))
        await cache.close()  # must not raise

    async def test_an_injected_client_is_not_closed(self):
        """A caller-supplied client belongs to the caller; QuoteCache must
        not close a connection it did not open."""
        closed = False

        class TrackingRedis(FakeRedis):
            async def aclose(self) -> None:
                nonlocal closed
                closed = True

        cache = QuoteCache(_settings(cache_enabled=True), client=TrackingRedis())
        await cache.close()
        assert closed is False
